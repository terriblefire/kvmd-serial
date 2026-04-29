# Maintainer: Stephen Sheridan <stephen@terriblefire.com>

pkgname=kvmd-serial
pkgver=1.2.1
pkgrel=1
pkgdesc="Serial console plugin for PiKVM - adds serial port streaming with ANSI terminal UI"
url="https://github.com/terriblefire/kvmd"
license=(GPL)
arch=(any)
depends=(
	kvmd
	python-pyserial
)
install=kvmd-serial.install
source=()
md5sums=()

package() {
	cd "$startdir"

	# Python plugin
	local _pydir="$pkgdir/usr/lib/python3.14/site-packages/kvmd/plugins/serial"
	install -Dm644 -t "$_pydir" \
		"$startdir/files/plugins/serial/__init__.py" \
		"$startdir/files/plugins/serial/tty.py"

	# API handler
	install -Dm644 "$startdir/files/api/serial.py" \
		"$pkgdir/usr/lib/python3.14/site-packages/kvmd/apps/kvmd/api/serial.py"

	# Web UI - JS
	install -Dm644 "$startdir/files/web/serial.js" \
		"$pkgdir/usr/share/kvmd/web/share/js/kvm/serial.js"

	# Web UI - xterm.js (bundled)
	install -Dm644 "$startdir/files/web/xterm.min.js" \
		"$pkgdir/usr/share/kvmd/web/share/js/xterm.min.js"
	install -Dm644 "$startdir/files/web/xterm-addon-fit.min.js" \
		"$pkgdir/usr/share/kvmd/web/share/js/xterm-addon-fit.min.js"
	install -Dm644 "$startdir/files/web/xterm.css" \
		"$pkgdir/usr/share/kvmd/web/share/css/xterm.css"

	# Web UI - serial port icon
	install -Dm644 "$startdir/files/web/led-serial.svg" \
		"$pkgdir/usr/share/kvmd/web/share/svg/led-serial.svg"

	# Patch script (used by .install hooks)
	install -Dm644 "$startdir/files/patch.py" \
		"$pkgdir/usr/share/kvmd-serial/patch.py"
}
