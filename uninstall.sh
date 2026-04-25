#!/bin/bash
#
# PiKVM Serial Console Plugin - Uninstaller
#
set -e

KVMD_PY="$(python3 -c 'import kvmd, os; print(os.path.dirname(kvmd.__file__))')"
KVMD_WEB="/usr/share/kvmd/web"

echo "=== PiKVM Serial Console Plugin Uninstaller ==="

if mount | grep -q 'on / .*ro[,)]'; then
    rw 2>/dev/null || mount -o remount,rw /
fi

# Restore backed up files if they exist
for f in \
    "$KVMD_PY/apps/kvmd/server.py" \
    "$KVMD_PY/apps/kvmd/__init__.py" \
    "$KVMD_PY/apps/_scheme.py" \
    "$KVMD_WEB/share/js/kvm/session.js" \
    "$KVMD_WEB/kvm/index.html"; do
    if [ -f "${f}.bak" ]; then
        cp "${f}.bak" "$f"
        rm "${f}.bak"
        echo "Restored: $f"
    fi
done

# Remove plugin files
rm -rf "$KVMD_PY/plugins/serial"
rm -f "$KVMD_PY/apps/kvmd/api/serial.py"
rm -f "$KVMD_WEB/share/js/kvm/serial.js"
rm -f "$KVMD_WEB/share/js/xterm.min.js"
rm -f "$KVMD_WEB/share/js/xterm-addon-fit.min.js"
rm -f "$KVMD_WEB/share/css/xterm.css"

echo ""
echo "Removed plugin files."
echo "NOTE: The serial config in /etc/kvmd/override.yaml was NOT removed."
echo "      Remove the 'serial:' section manually if desired."
echo ""

systemctl restart kvmd
echo "=== Uninstall complete ==="
