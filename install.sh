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

# Scope device access to *this* installing user only — not world-readable.
# Using OWNER="$USER" MODE="0600" means only $USER can read/write the device,
# eliminating the local-priv-esc / touch-keylogger surface of MODE="0666".
UDEV_RULE='/etc/udev/rules.d/99-uinput.rules'
info "Writing udev rule for /dev/uinput (owner=$USER mode=0600)…"
echo "KERNEL==\"uinput\", OWNER=\"$USER\", MODE=\"0600\"" \
    | sudo tee "$UDEV_RULE" >/dev/null

TOUCHPAD_RULE='/etc/udev/rules.d/99-touchpad-uaccess.rules'
info "Writing udev rule for touchpad access (owner=$USER mode=0600 + uaccess)…"
# uaccess additionally lets systemd-logind add ACLs for the active seat user
# at login time; OWNER+MODE is the belt-and-braces fallback for systems where
# uaccess doesn't end up applying (e.g. when udevadm is triggered manually).
echo "KERNEL==\"event*\", SUBSYSTEM==\"input\", ENV{ID_INPUT_TOUCHPAD}==\"1\", OWNER=\"$USER\", MODE=\"0600\", TAG+=\"uaccess\"" \
    | sudo tee "$TOUCHPAD_RULE" >/dev/null

sudo udevadm control --reload-rules
# --action=add is what fires MODE/OWNER/TAG application on existing nodes
sudo udevadm trigger --action=add --subsystem-match=input
sudo udevadm trigger --action=add --subsystem-match=misc

# With OWNER="$USER" rules in place we no longer need 'input' group membership
# for the app to work — keep this section out so we don't over-privilege the
# user with broad input-group access just to run gesture recognition.

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
# Forward Wayland/D-Bus session variables so gdbus & uinput find the right session.
# The udev OWNER="$USER" rules give the user direct access to the devices,
# so no SupplementaryGroups= needed (which doesn't work in a user unit anyway).
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
