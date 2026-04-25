/*****************************************************************************
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
*****************************************************************************/


"use strict";


import {tools, $} from "../tools.js";
import {wm} from "../wm.js";


export function Serial() {
	var self = this;

	/************************************************************************/

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
			if (__term) {
				__term.clear();
			}
		});

		let autoscroll_switch = $("serial-autoscroll-switch");
		if (autoscroll_switch) {
			tools.storage.bindSimpleSwitch(autoscroll_switch, "serial.autoscroll", true);
		}

		let speed_select = $("serial-speed-select");
		if (speed_select) {
			speed_select.addEventListener("change", function() {
				tools.httpPost("api/serial/set_speed", {"speed": speed_select.value}, function(http) {
					if (http.status !== 200) {
						wm.error("Failed to set baud rate", http.responseText);
					}
				});
			});
		}

		// Initialize xterm when the serial window becomes visible
		let serial_window = $("serial-window");
		if (serial_window) {
			let observer = new MutationObserver(function() {
				if (!serial_window.classList.contains("hidden") && !__initialized) {
					__initTerm();
				}
				if (!serial_window.classList.contains("hidden") && __term && __fit_addon) {
					setTimeout(function() { __fit_addon.fit(); }, 50);
				}
			});
			observer.observe(serial_window, {attributes: true, attributeFilter: ["class"]});
		}
	};

	var __initTerm = function() {
		if (__initialized || !__term_container) {
			return;
		}
		if (typeof Terminal === "undefined") {
			return;
		}

		__term = new Terminal({
			cursorBlink: true,
			cursorStyle: "block",
			fontSize: 14,
			fontFamily: "'Courier New', monospace",
			theme: {
				background: "#1e1e1e",
				foreground: "#d4d4d4",
				cursor: "#d4d4d4",
				selectionBackground: "#444444",
			},
			scrollback: 5000,
			convertEol: false,
			allowProposedApi: true,
		});

		if (typeof FitAddon !== "undefined") {
			__fit_addon = new FitAddon.FitAddon();
			__term.loadAddon(__fit_addon);
		}

		__term.open(__term_container);

		if (__fit_addon) {
			__fit_addon.fit();
		}

		// Send keypresses to serial
		__term.onData(function(data) {
			if (__ws && __ws.readyState === WebSocket.OPEN) {
				__ws.send(JSON.stringify({
					"event_type": "serial_write",
					"event": {"data": data},
				}));
			}
		});

		// Resize terminal when container resizes
		__resize_observer = new ResizeObserver(function() {
			if (__fit_addon && __term) {
				__fit_addon.fit();
			}
		});
		__resize_observer.observe(__term_container);

		__initialized = true;

		// Flush any data that arrived before terminal was ready
		if (__pending_data) {
			__term.write(__pending_data);
			__pending_data = "";
		}
	};

	/************************************************************************/

	self.setSocket = function(ws) {
		__ws = ws;
	};

	self.setState = function(state) {
		if (state) {
			if (!__state) {
				__state = {};
			}
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
				if (el) {
					el.innerText = state.device;
				}
			}
			if (state.speed !== undefined) {
				__state.speed = state.speed;
				let speed_select = $("serial-speed-select");
				if (speed_select && speed_select !== document.activeElement) {
					speed_select.value = String(state.speed);
				}
			}
			if (state.data !== undefined && state.data.length > 0) {
				if (__term) {
					__term.write(state.data);
				} else {
					__pending_data += state.data;
				}
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
