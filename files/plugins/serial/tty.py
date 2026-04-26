# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import asyncio
import os
import time

from typing import AsyncGenerator
from typing import Any

import serial

from ...logging import get_logger

from ... import aiotools

from ...yamlconf import Option

from ...validators.os import valid_abs_path
from ...validators.hw import valid_tty_speed

from . import BaseSerial


# =====
class Plugin(BaseSerial):
    def __init__(
        self,
        device_path: str,
        speed: int,
        read_timeout: float,
        poll_interval: float,
        **kwargs: Any,
    ) -> None:

        super().__init__(**kwargs)

        self.__device_path = device_path
        self.__speed = speed
        self.__read_timeout = read_timeout
        self.__poll_interval = poll_interval

        self.__serial: (serial.Serial | None) = None
        self.__online = False
        self.__buffer = ""
        self.__notifier = aiotools.AioNotifier()
        self.__upload_lock = asyncio.Lock()

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "device":        Option("/dev/ttyUSB0", type=valid_abs_path, unpack_as="device_path"),
            "speed":         Option(115200,         type=valid_tty_speed),
            "read_timeout":  Option(0.1,            type=float),
            "poll_interval": Option(0.1,            type=float),
        }

    async def get_state(self) -> dict:
        return {
            "enabled": True,
            "online": self.__online,
            "device": self.__device_path,
            "speed": self.__speed,
        }

    async def trigger_state(self) -> None:
        self.__notifier.notify()

    async def poll_state(self) -> AsyncGenerator[dict, None]:
        while True:
            self.__try_connect()

            if not self.__upload_lock.locked():
                data = self.__try_read()
            else:
                data = ""

            yield await self.get_state()

            if data:
                yield {"data": data}

            await self.__notifier.wait(timeout=self.__poll_interval)

    async def cleanup(self) -> None:
        self.__close()

    async def write(self, data: str) -> None:
        if self.__serial and self.__online:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.__serial.write, data.encode("utf-8", errors="replace"),
                )
            except Exception:
                get_logger().exception("Serial write error")
                self.__close()

    async def read(self) -> str:
        buf = self.__buffer
        self.__buffer = ""
        return buf

    async def set_speed(self, speed: int) -> None:
        self.__speed = speed
        self.__close()
        self.__notifier.notify()

    async def upload(self, address: int, data: bytes, call: bool=False, go: bool=False) -> dict:
        if not self.__serial or not self.__online:
            return {"error": "serial port not connected"}

        logger = get_logger()
        transcript = []

        async with self.__upload_lock:
            # Drain any buffered data the poll loop hasn't consumed
            await asyncio.sleep(self.__poll_interval * 2)
            if self.__serial and self.__serial.in_waiting:
                drained = self.__serial.read(self.__serial.in_waiting)
                logger.info("Upload: drained %d bytes", len(drained))

            t0 = time.monotonic()
            records = _make_srecords(address, data)
            logger.info("Upload: %d bytes -> %d S-records to address %08X", len(data), len(records), address)

            # Send CR to trigger a fresh prompt
            self.__serial_write_raw(b"\r")
            transcript.append("TX: \\r")

            # Wait for monitor prompt
            prompt = self.__read_until_sync(">", timeout=5.0, transcript=transcript)
            if prompt is None:
                return {"error": "timeout waiting for monitor prompt", "transcript": transcript}

            # Send each S-record
            for (i, record) in enumerate(records):
                rec_bytes = record.encode("ascii")
                self.__serial_write_raw(rec_bytes)
                transcript.append(f"TX: {record.rstrip(chr(13)).rstrip(chr(10))}")
                logger.info("Upload: sent record %d/%d", i + 1, len(records))

                # Wait for OK and then consume the prompt that follows
                ack = self.__read_until_sync(">", timeout=2.0, transcript=transcript)
                if ack is None:
                    return {"error": f"timeout waiting for response after record {i+1}", "transcript": transcript}
                if "?" in ack and "OK" not in ack:
                    return {"error": f"monitor rejected record {i+1}", "transcript": transcript}

            output = ""

            # Call (JSR)
            if call:
                cmd = f"c {address:08X}\r"
                self.__serial_write_raw(cmd.encode("ascii"))
                transcript.append(f"TX: {cmd.rstrip(chr(13))}")
                resp = self.__read_until_sync("OK", timeout=10.0, transcript=transcript)
                output = resp or ""

            # Go (JMP)
            if go:
                cmd = f"g {address:08X}\r"
                self.__serial_write_raw(cmd.encode("ascii"))
                transcript.append(f"TX: {cmd.rstrip(chr(13))}")
                await asyncio.sleep(0.1)

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info("Upload: complete in %dms", elapsed_ms)
            return {
                "bytes": len(data),
                "records": len(records),
                "elapsed_ms": elapsed_ms,
                "output": output,
                "transcript": transcript,
            }

    def __serial_write_raw(self, data: bytes) -> None:
        if self.__serial and self.__online:
            self.__serial.write(data)
            self.__serial.flush()

    def __read_until_sync(self, marker: str, timeout: float, transcript: list) -> (str | None):
        if not self.__serial or not self.__online:
            return None
        buf = ""
        deadline = time.monotonic() + timeout
        saved_timeout = self.__serial.timeout
        self.__serial.timeout = 0.5
        try:
            while time.monotonic() < deadline:
                waiting = self.__serial.in_waiting
                raw = self.__serial.read(max(1, waiting))
                if raw:
                    text = raw.decode("ascii", errors="replace")
                    buf += text
                    get_logger().info("Upload RX: %r", text)
                    if marker in buf:
                        transcript.append(f"RX: {buf.rstrip()}")
                        return buf
        finally:
            self.__serial.timeout = saved_timeout
        transcript.append(f"RX (timeout): {buf.rstrip()}")
        get_logger().warning("Upload: timeout waiting for %r, got: %r", marker, buf)
        return None

    # =====

    def __try_connect(self) -> None:
        if self.__serial is not None:
            return
        if not os.path.exists(self.__device_path):
            if self.__online:
                self.__online = False
            return
        try:
            self.__serial = serial.Serial(
                self.__device_path,
                self.__speed,
                timeout=self.__read_timeout,
            )
            self.__online = True
            get_logger().info("Serial port %s opened at %d baud", self.__device_path, self.__speed)
        except Exception:
            get_logger().exception("Failed to open serial port %s", self.__device_path)
            self.__serial = None
            self.__online = False

    def __try_read(self) -> str:
        if not self.__serial or not self.__online:
            return ""
        try:
            raw = self.__serial.read(self.__serial.in_waiting or 1)
            if raw:
                return raw.decode("utf-8", errors="replace")
            return ""
        except Exception:
            get_logger().exception("Serial read error")
            self.__close()
            return ""

    def __close(self) -> None:
        if self.__serial:
            try:
                self.__serial.close()
            except Exception:
                pass
            self.__serial = None
        self.__online = False


def _make_srecords(address: int, data: bytes, bytes_per_record: int=32) -> list[str]:
    records = []
    offset = 0
    while offset < len(data):
        chunk = data[offset:offset + bytes_per_record]
        addr = address + offset
        # S3: byte count = 4 (address) + len(data) + 1 (checksum)
        byte_count = 4 + len(chunk) + 1
        raw = [byte_count]
        raw.extend(addr.to_bytes(4, "big"))
        raw.extend(chunk)
        checksum = (~sum(raw)) & 0xFF
        raw.append(checksum)
        records.append("S3" + "".join(f"{b:02X}" for b in raw) + "\r\n")
        offset += bytes_per_record
    # S7 end record with entry address
    raw = [5]  # byte count: 4 (address) + 1 (checksum)
    raw.extend(address.to_bytes(4, "big"))
    checksum = (~sum(raw)) & 0xFF
    raw.append(checksum)
    records.append("S7" + "".join(f"{b:02X}" for b in raw) + "\r\n")
    return records
