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


from aiohttp.web import Request
from aiohttp.web import Response

from ....htserver import exposed_http
from ....htserver import exposed_ws
from ....htserver import make_json_response
from ....htserver import WsSession

from ....plugins.serial import BaseSerial

from ....validators.basic import valid_bool
from ....validators.hw import valid_tty_speed


# =====
class SerialApi:
    def __init__(self, serial: BaseSerial) -> None:
        self.__serial = serial

    # =====

    @exposed_http("GET", "/serial")
    async def __state_handler(self, _: Request) -> Response:
        return make_json_response(await self.__serial.get_state())

    @exposed_http("POST", "/serial/write")
    async def __write_handler(self, req: Request) -> Response:
        data = await req.text()
        await self.__serial.write(data)
        return make_json_response()

    @exposed_http("POST", "/serial/set_speed")
    async def __set_speed_handler(self, req: Request) -> Response:
        speed = valid_tty_speed(req.query.get("speed"))
        await self.__serial.set_speed(speed)
        return make_json_response()

    @exposed_http("POST", "/serial/upload")
    async def __upload_handler(self, req: Request) -> Response:
        address_str = req.query.get("address")
        if not address_str:
            return make_json_response({"error": "address parameter required"}, status=400)
        address = int(address_str, 16)
        data = await req.read()
        if not data:
            return make_json_response({"error": "no data in request body"}, status=400)
        call = valid_bool(req.query.get("call", False))
        go = valid_bool(req.query.get("go", False))
        result = await self.__serial.upload(address, data, call=call, go=go)
        if "error" in result:
            return make_json_response(result, status=409)
        return make_json_response(result)

    @exposed_ws("serial_write")
    async def __ws_write_handler(self, _: WsSession, event: dict) -> None:
        data = event.get("data", "")
        if data:
            await self.__serial.write(data)
