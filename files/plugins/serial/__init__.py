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


from typing import AsyncGenerator

from .. import BasePlugin
from .. import get_plugin_class


# =====
class BaseSerial(BasePlugin):
    async def get_state(self) -> dict:
        raise NotImplementedError

    async def trigger_state(self) -> None:
        raise NotImplementedError

    async def poll_state(self) -> AsyncGenerator[dict, None]:
        yield {}
        raise NotImplementedError

    async def cleanup(self) -> None:
        pass

    # =====

    async def write(self, data: str) -> None:
        raise NotImplementedError

    async def read(self) -> str:
        raise NotImplementedError

    async def set_speed(self, speed: int) -> None:
        raise NotImplementedError


# =====
def get_serial_class(name: str) -> type[BaseSerial]:
    return get_plugin_class("serial", name)  # type: ignore
