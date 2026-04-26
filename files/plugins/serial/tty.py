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
            data = self.__try_read()

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

        t0 = time.monotonic()
        records = _make_srecords(address, data)

        # Wait for monitor prompt
        prompt = await self.__read_until(">", timeout=5.0)
        if prompt is None:
            return {"error": "timeout waiting for monitor prompt"}

        # Send each S-record
        for record in records:
            await self.__write_line(record)
            ack = await self.__read_until("OK", timeout=2.0)
            if ack is None:
                return {"error": f"timeout waiting for OK after record"}

        output = ""

        # Call (JSR)
        if call:
            cmd = f"c {address:08X}\r"
            await self.__write_line(cmd)
            resp = await self.__read_until("OK", timeout=10.0)
            output = resp or ""

        # Go (JMP)
        if go:
            cmd = f"g {address:08X}\r"
            await self.__write_line(cmd)
            # No response expected - execution transfers
            await asyncio.sleep(0.1)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "bytes": len(data),
            "records": len(records),
            "elapsed_ms": elapsed_ms,
            "output": output,
        }

    async def __write_line(self, line: str) -> None:
        if self.__serial and self.__online:
            line_bytes = line.encode("ascii") if not line.endswith("\r") else line.rstrip("\r").encode("ascii") + b"\r"
            await asyncio.get_event_loop().run_in_executor(
                None, self.__serial.write, line_bytes,
            )

    async def __read_until(self, marker: str, timeout: float) -> (str | None):
        if not self.__serial or not self.__online:
            return None
        buf = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, self.__serial.read, max(1, self.__serial.in_waiting),
            )
            if raw:
                buf += raw.decode("ascii", errors="replace")
                if marker in buf:
                    return buf
            else:
                await asyncio.sleep(0.01)
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
        records.append("S3" + "".join(f"{b:02X}" for b in raw) + "\r")
        offset += bytes_per_record
    # S7 end record with entry address
    raw = [5]  # byte count: 4 (address) + 1 (checksum)
    raw.extend(address.to_bytes(4, "big"))
    checksum = (~sum(raw)) & 0xFF
    raw.append(checksum)
    records.append("S7" + "".join(f"{b:02X}" for b in raw) + "\r")
    return records
