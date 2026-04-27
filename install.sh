#!/usr/bin/env bash
# install.sh – set up gesture-app on Ubuntu/GNOME (Wayland)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_NAME="gesture-app.service"
PYTHON="${PYTHON:-python3}"
VENV="$APP_DIR/.venv"

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── 0. Pre-flight ─────────────────────────────────────────────────────────────
if [[ $EUID -eq 0 ]]; then
    error "Do not run as root — the script will call sudo when needed."
    exit 1
fi

command -v "$PYTHON" >/dev/null 2>&1 || {
    error "python3 not found.  Run: sudo apt install python3 python3-venv"
    exit 1
}

# ── 1. System packages ────────────────────────────────────────────────────────
info "Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y \
    python3-venv \
    python3-pip \
    dbus \
    libglib2.0-bin   # provides the gdbus CLI

# ── 2. uinput kernel module (for fallback key injection) ──────────────────────
info "Ensuring uinput module loads at boot…"
if ! grep -q "^uinput" /etc/modules-load.d/uinput.conf 2>/dev/null; then
    echo "uinput" | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
fi
sudo modprobe uinput 2>/dev/null || warn "modprobe uinput: may already be loaded"

UDEV_RULE='/etc/udev/rules.d/99-uinput.rules'
info "Writing udev rule for /dev/uinput (MODE=0666)…"
# 0666 lets the user session write to /dev/uinput without needing the
# 'input' group to be active in the current login.  Required for the
# evdev.UInput-based virtual keyboard used for Alt+Tab, Super, etc.
echo 'KERNEL=="uinput", MODE="0666"' | sudo tee "$UDEV_RULE" >/dev/null

# Grant the logged-in seat user direct access to the touchpad via uaccess.
# This avoids needing a logout/login for input-group membership to take effect.
TOUCHPAD_RULE='/etc/udev/rules.d/99-touchpad-uaccess.rules'
info "Writing udev rule for touchpad access (MODE=0666 + uaccess tag)…"
# MODE="0666" makes the touchpad device world-readable AND writable.
# evdev's list_devices() requires R+W access (it calls os.access(fn, R_OK|W_OK)),
# so 0664 alone is not enough — write access is required even though we never
# write to the device.  TAG+="uaccess" additionally grants seat-user ACLs.
echo 'KERNEL=="event*", SUBSYSTEM=="input", ENV{ID_INPUT_TOUCHPAD}=="1", MODE="0666", TAG+="uaccess"' \
    | sudo tee "$TOUCHPAD_RULE" >/dev/null
sudo udevadm control --reload-rules
# Use --action=add so the mode/ACL changes are actually applied to existing nodes
sudo udevadm trigger --action=add --subsystem-match=input

# Also add to input group (good practice, needed after reboot before first login)
NEED_RELOGIN=0
if ! groups | grep -qw input; then
    info "Adding $USER to the 'input' group (takes effect on next login)…"
    sudo usermod -aG input "$USER"
fi

# ── 4. Python virtual environment ─────────────────────────────────────────────
info "Creating Python virtual environment at $VENV…"
# --system-site-packages lets the venv access system gi/dbus if ever needed
"$PYTHON" -m venv --system-site-packages "$VENV"

info "Installing Python dependencies…"
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

info "Python packages installed."

# ── 5. Verify evdev can see the touchpad ─────────────────────────────────────
info "Checking for a multitouch touchpad…"
if "$VENV/bin/python" - <<'PYEOF' 2>/dev/null; then
import sys
sys.path.insert(0, ''"$APP_DIR"'')
from detector.device_finder import find_touchpad
d = find_touchpad()
if d:
    print(f"  Found: {d.path} ({d.name})")
else:
    print("  WARNING: No touchpad found yet (may need re-login for group membership)")
PYEOF
    true
else
    warn "Could not run device check — will work after re-login if group was just added."
fi

# ── 6. Bind Super+D to "show desktop" (used by 3-finger swipe down) ──────────
if command -v gsettings >/dev/null 2>&1; then
    info "Binding Super+D to 'show-desktop'…"
    gsettings set org.gnome.desktop.wm.keybindings show-desktop "['<Super>d']" || \
        warn "gsettings show-desktop bind failed — set it manually in Settings → Keyboard"
fi

# ── 7. systemd user service ───────────────────────────────────────────────────
mkdir -p "$SERVICE_DIR"

info "Writing systemd user service: $SERVICE_DIR/$SERVICE_NAME"
cat > "$SERVICE_DIR/$SERVICE_NAME" <<EOF
[Unit]
Description=Touchpad gesture recognizer for GNOME on Wayland
Documentation=file://$APP_DIR/README.md
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$VENV/bin/python $APP_DIR/main.py
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
# Activate the input group immediately — no logout/login needed
SupplementaryGroups=input
# Forward Wayland/D-Bus session variables
PassEnvironment=WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS DISPLAY

[Install]
WantedBy=graphical-session.target
EOF

# ── 8. Enable and start ───────────────────────────────────────────────────────
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"

info "Starting $SERVICE_NAME…"
systemctl --user restart "$SERVICE_NAME"
sleep 1
systemctl --user status "$SERVICE_NAME" --no-pager || true

echo ""
info "Installation complete."
echo ""
echo "  Useful commands:"
echo "    systemctl --user status  $SERVICE_NAME"
echo "    systemctl --user restart $SERVICE_NAME"
echo "    systemctl --user stop    $SERVICE_NAME"
echo "    journalctl --user -u     $SERVICE_NAME -f"
echo ""
echo "  Test manually with debug output:"
echo "    $VENV/bin/python $APP_DIR/main.py --debug"
echo "    $VENV/bin/python $APP_DIR/main.py --list     # show all input devices"
echo ""
