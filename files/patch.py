#!/usr/bin/env python3
"""
Patches kvmd source files and web UI to integrate the serial console plugin.
Safe to run multiple times - checks before patching.
"""

import os
import sys


def patch_file(path, replacements):
    with open(path, "r") as f:
        content = f.read()
    changed = False
    for (old, new, check) in replacements:
        if check not in content:
            content = content.replace(old, new)
            changed = True
    if changed:
        with open(path, "w") as f:
            f.write(content)
        print(f"  Patched: {path}")
    else:
        print(f"  Already patched: {path}")


def main():
    import kvmd
    kvmd_dir = os.path.dirname(kvmd.__file__)
    web_dir = "/usr/share/kvmd/web"

    print("==> Patching kvmd for serial console plugin...")

    # server.py
    patch_file(os.path.join(kvmd_dir, "apps/kvmd/server.py"), [
        ("from ...plugins.msd import BaseMsd",
         "from ...plugins.msd import BaseMsd\nfrom ...plugins.serial import BaseSerial",
         "BaseSerial"),
        ("from .api.switch import SwitchApi",
         "from .api.serial import SerialApi\nfrom .api.switch import SwitchApi",
         "SerialApi"),
        ('__EV_CLIENTS_STATE = "clients"',
         '__EV_SERIAL_STATE = "serial"\n    __EV_CLIENTS_STATE = "clients"',
         "__EV_SERIAL_STATE"),
        ("msd: BaseMsd,\n        streamer: Streamer,",
         "msd: BaseMsd,\n        serial: (BaseSerial | None),\n        streamer: Streamer,",
         "serial: (BaseSerial"),
        ("StreamerApi(streamer, ocr),\n            SwitchApi(switch),",
         "StreamerApi(streamer, ocr),\n            *([SerialApi(serial)] if serial else []),\n            SwitchApi(switch),",
         "SerialApi(serial)"),
        ('_Subsystem.make(msd,      "MSD",      self.__EV_MSD_STATE),',
         '_Subsystem.make(msd,      "MSD",      self.__EV_MSD_STATE),\n            *([_Subsystem.make(serial, "Serial", self.__EV_SERIAL_STATE)] if serial else []),',
         '"Serial", self.__EV_SERIAL_STATE'),
    ])

    # __init__.py
    patch_file(os.path.join(kvmd_dir, "apps/kvmd/__init__.py"), [
        ("from ...plugins.msd import get_msd_class",
         "from ...plugins.msd import get_msd_class\nfrom ...plugins.serial import get_serial_class",
         "get_serial_class"),
        ("load_gpio=True,\n    ).config",
         "load_gpio=True,\n        load_serial=True,\n    ).config",
         "load_serial"),
        ("    hid = get_hid_class(config.hid.type)(**hid_kwargs)\n    streamer",
         '    hid = get_hid_class(config.hid.type)(**hid_kwargs)\n\n    serial = None\n    if config.serial.type:\n        serial = get_serial_class(config.serial.type)(**config.serial._unpack(ignore=["type"]))\n\n    streamer',
         "serial = None"),
        ("msd=get_msd_class(config.msd.type)(**msd_kwargs),\n        streamer=streamer,",
         "msd=get_msd_class(config.msd.type)(**msd_kwargs),\n        serial=serial,\n        streamer=streamer,",
         "serial=serial"),
    ])

    # _scheme.py
    patch_file(os.path.join(kvmd_dir, "apps/_scheme.py"), [
        ("from ..plugins.msd import get_msd_class",
         "from ..plugins.msd import get_msd_class\nfrom ..plugins.serial import get_serial_class",
         "get_serial_class"),
        ("load_gpio: bool=False,\n    load_all: bool=False,",
         "load_gpio: bool=False,\n    load_serial: bool=False,\n    load_all: bool=False,",
         "load_serial"),
        ("load_auth = load_hid = load_atx = load_msd = load_gpio = True",
         "load_auth = load_hid = load_atx = load_msd = load_gpio = load_serial = True",
         "load_serial = True"),
        ('    for (load, section, get_class) in [\n        (load_hid, "hid", get_hid_class),',
         '    if load_serial and config.kvmd.serial.type:\n        scheme["kvmd"]["serial"].update(get_serial_class(config.kvmd.serial.type).get_plugin_options())\n        rebuild = True\n\n    for (load, section, get_class) in [\n        (load_hid, "hid", get_hid_class),',
         "load_serial and config"),
        ('"msd": {\n                "type": Option("", type=valid_stripped_string_not_empty),\n                # Dynamic content\n            },',
         '"msd": {\n                "type": Option("", type=valid_stripped_string_not_empty),\n                # Dynamic content\n            },\n\n            "serial": {\n                "type": Option("", type=valid_stripped_string),\n                # Dynamic content\n            },',
         '"serial": {'),
    ])

    # session.js
    session_path = os.path.join(web_dir, "share/js/kvm/session.js")
    patch_file(session_path, [
        ('import {Switch} from "./switch.js";',
         'import {Serial} from "./serial.js";\nimport {Switch} from "./switch.js";',
         "Serial"),
        ('var __switch = new Switch();',
         'var __serial = new Serial();\n\tvar __switch = new Switch();',
         "__serial"),
        ('__hid.setSocket(__ws);',
         '__hid.setSocket(__ws);\n\t\t__serial.setSocket(__ws);',
         "__serial.setSocket"),
        ('case "ocr": __ocr.setState(ev); break;',
         'case "ocr": __ocr.setState(ev); break;\n\t\t\tcase "serial": __serial.setState(ev); break;',
         '"serial": __serial'),
        ('__recorder.setSocket(null);',
         '__serial.setState(null);\n\t\t__serial.setSocket(null);\n\t\t__recorder.setSocket(null);',
         "__serial.setState(null)"),
    ])

    # index.html
    html_path = os.path.join(web_dir, "kvm/index.html")
    with open(html_path, "r") as f:
        content = f.read()

    changed = False

    if "xterm.css" not in content:
        content = content.replace("</head>", '    <link rel="stylesheet" href="../share/css/xterm.css">\n</head>')
        changed = True

    if "xterm.min.js" not in content:
        content = content.replace("</body>", '    <script src="../share/js/xterm.min.js"></script>\n    <script src="../share/js/xterm-addon-fit.min.js"></script>\n</body>')
        changed = True

    if "serial-dropdown" not in content:
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
        content = content.replace(
            '      <li class="right feature-disabled" id="gpio-dropdown">',
            serial_navbar + '      <li class="right feature-disabled" id="gpio-dropdown">'
        )
        changed = True

    if "serial-term-container" not in content:
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
        content = content.replace(
            '    <div class="hidden window window-elegant window-resizable" id="webterm-window"',
            serial_window + '    <div class="hidden window window-elegant window-resizable" id="webterm-window"'
        )
        changed = True

    if changed:
        with open(html_path, "w") as f:
            f.write(content)
        print(f"  Patched: {html_path}")
    else:
        print(f"  Already patched: {html_path}")

    print("==> Done. Restart kvmd: systemctl restart kvmd")


if __name__ == "__main__":
    main()
