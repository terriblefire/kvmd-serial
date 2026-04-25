#!/bin/bash
#
# PiKVM Serial Console Plugin - Installer
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/terriblefire/kvmd/master/contrib/serial-plugin/install.sh | sudo bash
#
# Or from a local copy:
#   ssh root@<pikvm-ip> 'bash -s' < install.sh
#
set -e

KVMD_PY="$(python3 -c 'import kvmd, os; print(os.path.dirname(kvmd.__file__))')"
KVMD_WEB="/usr/share/kvmd/web"
XTERM_VERSION="5.3.0"
XTERM_FIT_VERSION="0.10.0"

echo "=== PiKVM Serial Console Plugin Installer ==="
echo "KVMD Python path: $KVMD_PY"

# Ensure we're on PiKVM
if [ ! -f /usr/bin/kvmd ]; then
    echo "ERROR: This script must be run on a PiKVM device."
    exit 1
fi

# Remount read-write
if mount | grep -q 'on / .*ro[,)]'; then
    echo "[*] Remounting filesystem read-write..."
    rw 2>/dev/null || mount -o remount,rw /
fi

# Ensure pyserial is installed
if ! python3 -c "import serial" 2>/dev/null; then
    echo "[*] Installing pyserial..."
    pacman -S --noconfirm python-pyserial
fi

# --- Python plugin files ---

echo "[*] Installing Python plugin..."

mkdir -p "$KVMD_PY/plugins/serial"

cat > "$KVMD_PY/plugins/serial/__init__.py" << 'PLUGIN_INIT'
from typing import AsyncGenerator
from .. import BasePlugin
from .. import get_plugin_class

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
    async def write(self, data: str) -> None:
        raise NotImplementedError
    async def read(self) -> str:
        raise NotImplementedError
    async def set_speed(self, speed: int) -> None:
        raise NotImplementedError

def get_serial_class(name: str) -> type[BaseSerial]:
    return get_plugin_class("serial", name)
PLUGIN_INIT

cat > "$KVMD_PY/plugins/serial/tty.py" << 'PLUGIN_TTY'
import asyncio
import os
from typing import AsyncGenerator, Any
import serial
from ...logging import get_logger
from ... import aiotools
from ...yamlconf import Option
from ...validators.os import valid_abs_path
from ...validators.hw import valid_tty_speed
from . import BaseSerial

class Plugin(BaseSerial):
    def __init__(self, device_path: str, speed: int, read_timeout: float, poll_interval: float, **kwargs: Any) -> None:
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
        return {"enabled": True, "online": self.__online, "device": self.__device_path, "speed": self.__speed}

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
                await asyncio.get_event_loop().run_in_executor(None, self.__serial.write, data.encode("utf-8", errors="replace"))
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

    def __try_connect(self) -> None:
        if self.__serial is not None:
            return
        if not os.path.exists(self.__device_path):
            if self.__online:
                self.__online = False
            return
        try:
            self.__serial = serial.Serial(self.__device_path, self.__speed, timeout=self.__read_timeout)
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
PLUGIN_TTY

cat > "$KVMD_PY/apps/kvmd/api/serial.py" << 'API_SERIAL'
from aiohttp.web import Request, Response
from ....htserver import exposed_http, exposed_ws, make_json_response, WsSession
from ....plugins.serial import BaseSerial
from ....validators.hw import valid_tty_speed

class SerialApi:
    def __init__(self, serial: BaseSerial) -> None:
        self.__serial = serial

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

    @exposed_ws("serial_write")
    async def __ws_write_handler(self, _: WsSession, event: dict) -> None:
        data = event.get("data", "")
        if data:
            await self.__serial.write(data)
API_SERIAL

# --- Patch Python source files ---

echo "[*] Patching kvmd Python sources..."

python3 << 'PATCH_PY'
import sys

files_patched = []

# --- server.py ---
path = sys.argv[1] if len(sys.argv) > 1 else None
import kvmd
import os
kvmd_dir = os.path.dirname(kvmd.__file__)

server_path = os.path.join(kvmd_dir, "apps/kvmd/server.py")
with open(server_path, "r") as f:
    c = f.read()
if "BaseSerial" not in c:
    c = c.replace("from ...plugins.msd import BaseMsd", "from ...plugins.msd import BaseMsd\nfrom ...plugins.serial import BaseSerial")
    c = c.replace("from .api.switch import SwitchApi", "from .api.serial import SerialApi\nfrom .api.switch import SwitchApi")
    c = c.replace('__EV_CLIENTS_STATE = "clients"', '__EV_SERIAL_STATE = "serial"\n    __EV_CLIENTS_STATE = "clients"')
    c = c.replace("msd: BaseMsd,\n        streamer: Streamer,", "msd: BaseMsd,\n        serial: (BaseSerial | None),\n        streamer: Streamer,")
    c = c.replace("StreamerApi(streamer, ocr),\n            SwitchApi(switch),", "StreamerApi(streamer, ocr),\n            *([SerialApi(serial)] if serial else []),\n            SwitchApi(switch),")
    c = c.replace('_Subsystem.make(msd,      "MSD",      self.__EV_MSD_STATE),', '_Subsystem.make(msd,      "MSD",      self.__EV_MSD_STATE),\n            *([_Subsystem.make(serial, "Serial", self.__EV_SERIAL_STATE)] if serial else []),')
    with open(server_path, "w") as f:
        f.write(c)
    files_patched.append("server.py")

# --- __init__.py ---
init_path = os.path.join(kvmd_dir, "apps/kvmd/__init__.py")
with open(init_path, "r") as f:
    c = f.read()
if "get_serial_class" not in c:
    c = c.replace("from ...plugins.msd import get_msd_class", "from ...plugins.msd import get_msd_class\nfrom ...plugins.serial import get_serial_class")
    c = c.replace("load_gpio=True,\n    ).config", "load_gpio=True,\n        load_serial=True,\n    ).config")
    c = c.replace(
        "    hid = get_hid_class(config.hid.type)(**hid_kwargs)\n    streamer",
        '    hid = get_hid_class(config.hid.type)(**hid_kwargs)\n\n    serial = None\n    if config.serial.type:\n        serial = get_serial_class(config.serial.type)(**config.serial._unpack(ignore=["type"]))\n\n    streamer'
    )
    c = c.replace("msd=get_msd_class(config.msd.type)(**msd_kwargs),\n        streamer=streamer,", "msd=get_msd_class(config.msd.type)(**msd_kwargs),\n        serial=serial,\n        streamer=streamer,")
    with open(init_path, "w") as f:
        f.write(c)
    files_patched.append("__init__.py")

# --- _scheme.py ---
scheme_path = os.path.join(kvmd_dir, "apps/_scheme.py")
with open(scheme_path, "r") as f:
    c = f.read()
if "get_serial_class" not in c:
    c = c.replace("from ..plugins.msd import get_msd_class", "from ..plugins.msd import get_msd_class\nfrom ..plugins.serial import get_serial_class")
    c = c.replace("load_gpio: bool=False,\n    load_all: bool=False,", "load_gpio: bool=False,\n    load_serial: bool=False,\n    load_all: bool=False,")
    c = c.replace("load_auth = load_hid = load_atx = load_msd = load_gpio = True", "load_auth = load_hid = load_atx = load_msd = load_gpio = load_serial = True")
    c = c.replace(
        '    for (load, section, get_class) in [\n        (load_hid, "hid", get_hid_class),',
        '    if load_serial and config.kvmd.serial.type:\n        scheme["kvmd"]["serial"].update(get_serial_class(config.kvmd.serial.type).get_plugin_options())\n        rebuild = True\n\n    for (load, section, get_class) in [\n        (load_hid, "hid", get_hid_class),'
    )
    c = c.replace(
        '"msd": {\n                "type": Option("", type=valid_stripped_string_not_empty),\n                # Dynamic content\n            },',
        '"msd": {\n                "type": Option("", type=valid_stripped_string_not_empty),\n                # Dynamic content\n            },\n\n            "serial": {\n                "type": Option("", type=valid_stripped_string),\n                # Dynamic content\n            },'
    )
    with open(scheme_path, "w") as f:
        f.write(c)
    files_patched.append("_scheme.py")

if files_patched:
    print(f"Patched: {', '.join(files_patched)}")
else:
    print("All Python files already patched")
PATCH_PY

# --- Web UI files ---

echo "[*] Installing web UI..."

# Download xterm.js if not present
if [ ! -f "$KVMD_WEB/share/js/xterm.min.js" ]; then
    echo "[*] Downloading xterm.js v${XTERM_VERSION}..."
    curl -sL "https://cdn.jsdelivr.net/npm/xterm@${XTERM_VERSION}/lib/xterm.min.js" -o "$KVMD_WEB/share/js/xterm.min.js"
    curl -sL "https://cdn.jsdelivr.net/npm/xterm@${XTERM_VERSION}/css/xterm.css" -o "$KVMD_WEB/share/css/xterm.css"
    curl -sL "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@${XTERM_FIT_VERSION}/lib/addon-fit.min.js" -o "$KVMD_WEB/share/js/xterm-addon-fit.min.js"
fi

# Install serial.js
cat > "$KVMD_WEB/share/js/kvm/serial.js" << 'SERIAL_JS'
"use strict";
import {tools, $} from "../tools.js";
import {wm} from "../wm.js";

export function Serial() {
	var self = this;
	var __ws = null;
	var __state = null;
	var __term = null;
	var __fit_addon = null;
	var __term_container = null;
	var __initialized = false;
	var __pending_data = "";
	var __resize_observer = null;

	var __init__ = function() {
		__term_container = $("serial-term-container");
		tools.el.setOnClick($("serial-clear-button"), function() {
			if (__term) { __term.clear(); }
		});
		let autoscroll_switch = $("serial-autoscroll-switch");
		if (autoscroll_switch) {
			tools.storage.bindSimpleSwitch(autoscroll_switch, "serial.autoscroll", true);
		}
		let speed_select = $("serial-speed-select");
		if (speed_select) {
			speed_select.addEventListener("change", function() {
				tools.httpPost("api/serial/set_speed", {"speed": speed_select.value}, function(http) {
					if (http.status !== 200) { wm.error("Failed to set baud rate", http.responseText); }
				});
			});
		}
		let serial_window = $("serial-window");
		if (serial_window) {
			let observer = new MutationObserver(function() {
				if (!serial_window.classList.contains("hidden") && !__initialized) { __initTerm(); }
				if (!serial_window.classList.contains("hidden") && __term && __fit_addon) {
					setTimeout(function() { __fit_addon.fit(); }, 50);
				}
			});
			observer.observe(serial_window, {attributes: true, attributeFilter: ["class"]});
		}
	};

	var __initTerm = function() {
		if (__initialized || !__term_container || typeof Terminal === "undefined") { return; }
		__term = new Terminal({
			cursorBlink: true, cursorStyle: "block", fontSize: 14,
			fontFamily: "'Courier New', monospace",
			theme: {background: "#1e1e1e", foreground: "#d4d4d4", cursor: "#d4d4d4", selectionBackground: "#444444"},
			scrollback: 5000, convertEol: false, allowProposedApi: true,
		});
		if (typeof FitAddon !== "undefined") {
			__fit_addon = new FitAddon.FitAddon();
			__term.loadAddon(__fit_addon);
		}
		__term.open(__term_container);
		if (__fit_addon) { __fit_addon.fit(); }
		__term.onData(function(data) {
			if (__ws && __ws.readyState === WebSocket.OPEN) {
				__ws.send(JSON.stringify({"event_type": "serial_write", "event": {"data": data}}));
			}
		});
		__resize_observer = new ResizeObserver(function() {
			if (__fit_addon && __term) { __fit_addon.fit(); }
		});
		__resize_observer.observe(__term_container);
		__initialized = true;
		if (__pending_data) { __term.write(__pending_data); __pending_data = ""; }
	};

	self.setSocket = function(ws) { __ws = ws; };

	self.setState = function(state) {
		if (state) {
			if (!__state) { __state = {}; }
			if (state.enabled !== undefined) {
				__state.enabled = state.enabled;
				tools.feature.setEnabled($("serial-dropdown"), __state.enabled);
			}
			if (state.online !== undefined) {
				__state.online = state.online;
				$("serial-led").className = (state.online ? "led-green" : "led-gray");
				$("serial-led").title = (state.online ? "Serial: Connected" : "Serial: Disconnected");
			}
			if (state.device !== undefined) {
				__state.device = state.device;
				let el = $("serial-device-info");
				if (el) { el.innerText = state.device; }
			}
			if (state.speed !== undefined) {
				__state.speed = state.speed;
				let speed_select = $("serial-speed-select");
				if (speed_select && speed_select !== document.activeElement) { speed_select.value = String(state.speed); }
			}
			if (state.data !== undefined && state.data.length > 0) {
				if (__term) { __term.write(state.data); } else { __pending_data += state.data; }
			}
		} else {
			__state = null;
			$("serial-led").className = "led-gray";
			$("serial-led").title = "Serial: Disconnected";
			tools.feature.setEnabled($("serial-dropdown"), false);
		}
	};

	__init__();
}
SERIAL_JS

# Patch session.js
if ! grep -q 'Serial' "$KVMD_WEB/share/js/kvm/session.js"; then
    echo "[*] Patching session.js..."
    python3 << 'PATCH_SESSION'
import os
path = os.path.join(os.environ.get("KVMD_WEB", "/usr/share/kvmd/web"), "share/js/kvm/session.js")
with open(path, "r") as f:
    c = f.read()
c = c.replace('import {Switch} from "./switch.js";', 'import {Serial} from "./serial.js";\nimport {Switch} from "./switch.js";')
c = c.replace('var __switch = new Switch();', 'var __serial = new Serial();\n\tvar __switch = new Switch();')
c = c.replace('__hid.setSocket(__ws);', '__hid.setSocket(__ws);\n\t\t__serial.setSocket(__ws);')
c = c.replace('case "ocr": __ocr.setState(ev); break;', 'case "ocr": __ocr.setState(ev); break;\n\t\t\tcase "serial": __serial.setState(ev); break;')
c = c.replace('__recorder.setSocket(null);', '__serial.setState(null);\n\t\t__serial.setSocket(null);\n\t\t__recorder.setSocket(null);')
with open(path, "w") as f:
    f.write(c)
PATCH_SESSION
fi

# Patch index.html
echo "[*] Patching index.html..."
python3 << 'PATCH_HTML'
import os
path = os.path.join(os.environ.get("KVMD_WEB", "/usr/share/kvmd/web"), "kvm/index.html")
with open(path, "r") as f:
    c = f.read()

changed = False

# Add xterm CSS
if "xterm.css" not in c:
    c = c.replace("</head>", '    <link rel="stylesheet" href="../share/css/xterm.css">\n</head>')
    changed = True

# Add xterm JS
if "xterm.min.js" not in c:
    c = c.replace("</body>", '    <script src="../share/js/xterm.min.js"></script>\n    <script src="../share/js/xterm-addon-fit.min.js"></script>\n</body>')
    changed = True

# Add serial navbar
if "serial-dropdown" not in c:
    serial_navbar = '''      <li class="right feature-disabled" id="serial-dropdown">
        <div class="menu-item menu-button" href="#"><img class="led-gray" id="serial-led" src="../share/svg/led-atx-power.svg"><span>Serial</span>
        </div>
        <div class="hidden menu">
          <div class="text"><b>Serial Console<br></b><sub id="serial-device-info">&nbsp;</sub></div>
          <hr>
          <table class="kv">
            <tr>
              <td>Baud rate:</td>
              <td align="right">
                <select id="serial-speed-select" style="width: 100px">
                  <option value="1200">1200</option>
                  <option value="2400">2400</option>
                  <option value="4800">4800</option>
                  <option value="9600">9600</option>
                  <option value="19200">19200</option>
                  <option value="38400">38400</option>
                  <option value="57600">57600</option>
                  <option value="115200" selected>115200</option>
                </select>
              </td>
            </tr>
          </table>
          <hr>
              <table class="kv">
                <tr>
                      <td>Auto-scroll output:</td>
                      <td align="right">
                        <div class="switch-box">
                          <input checked type="checkbox" id="serial-autoscroll-switch">
                          <label for="serial-autoscroll-switch"><span class="switch-inner"></span><span class="switch"></span></label>
                        </div>
                      </td>
                </tr>
              </table>
          <hr>
          <div class="buttons">
            <button data-wm-menu-force-hide data-wm-window-show="serial-window">&bull; Open Serial Console</button>
          </div>
        </div>
      </li>
'''
    c = c.replace('      <li class="right feature-disabled" id="gpio-dropdown">', serial_navbar + '      <li class="right feature-disabled" id="gpio-dropdown">')
    changed = True

# Add serial window
if "serial-term-container" not in c:
    serial_window = '''    <div class="hidden window window-elegant window-resizable" id="serial-window" data-wm-window-show-centered style="display: flex; width: 820px; height: 520px; min-width: 640px; min-height: 400px">
      <div class="window-header">
        <div class="window-grab">Serial Console</div>
        <div class="window-buttons">
          <button data-wm-window-set-original></button>
          <button data-wm-window-set-maximized></button>
          <button data-wm-window-close></button>
        </div>
      </div>
      <div style="display: flex; flex-direction: column; flex: 1; padding: 5px; gap: 5px;">
        <div id="serial-term-container" style="flex: 1; overflow: hidden;"></div>
        <div style="display: flex; gap: 5px; align-items: center;">
          <span style="flex: 1; font-size: 11px; color: #888;">Click terminal to type. Keys are sent directly.</span>
          <button id="serial-clear-button">Clear</button>
        </div>
      </div>
    </div>
'''
    # Insert before webterm window
    c = c.replace(
        '    <div class="hidden window window-elegant window-resizable" id="webterm-window"',
        serial_window + '    <div class="hidden window window-elegant window-resizable" id="webterm-window"'
    )
    changed = True

if changed:
    with open(path, "w") as f:
        f.write(c)
    print("index.html patched")
else:
    print("index.html already patched")
PATCH_HTML

# --- Configuration ---

echo "[*] Configuring serial plugin..."
if ! grep -q 'serial:' /etc/kvmd/override.yaml 2>/dev/null; then
    cat >> /etc/kvmd/override.yaml << 'YAML'

    serial:
        type: tty
        device: /dev/ttyUSB0
        speed: 115200
YAML
    echo "    Added serial config to /etc/kvmd/override.yaml"
    echo "    Edit /etc/kvmd/override.yaml to change device/speed."
else
    echo "    Serial config already present in override.yaml"
fi

# --- Restart ---

echo "[*] Restarting kvmd..."
systemctl restart kvmd

echo ""
echo "=== Installation complete ==="
echo ""
echo "Open the PiKVM web UI and look for the 'Serial' button in the navbar."
echo "Default: /dev/ttyUSB0 at 115200 baud (changeable from the UI)."
echo ""
echo "To uninstall, run: curl -sSL .../uninstall.sh | sudo bash"
echo "Or manually remove the serial sections and restart kvmd."
