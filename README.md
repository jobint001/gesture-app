# gesture-app

Touchpad gesture recognizer for **GNOME on Wayland** — written in Python, no
`xdotool`, `ydotool`, `libinput-gestures`, or `touchégg` dependency.

Reads raw multitouch events directly from `/dev/input/eventN` via `evdev`,
classifies them into named gestures, and triggers GNOME actions through D-Bus
or a `uinput`-based virtual keyboard.

The 4-finger Alt+Tab is **interactive** — the popup appears mid-swipe and
follows your fingers, so you can swipe left/right to navigate apps and lift to
commit, just like on Windows 11 / macOS.

## Default gestures

| Gesture | Action |
|---|---|
| 2-finger swipe left  | Browser forward (Alt+Right) |
| 2-finger swipe right | Browser back (Alt+Left) |
| 3-finger swipe up    | Activities overview |
| 3-finger swipe down  | Show desktop (Super+D) |
| 3-finger swipe left  | Next workspace |
| 3-finger swipe right | Previous workspace |
| 4-finger swipe right | Alt+Tab popup, highlight moves right |
| 4-finger swipe left  | Alt+Tab popup, highlight moves left |
| 3-finger tap         | Middle click |

The 2-finger swipe shares the touchpad with libinput's 2-finger scroll, so
it requires a deliberately long & strongly horizontal motion (≥90 px and a
3:1 horizontal-to-vertical ratio) to fire — normal scrolling won't trigger
it.

The 4-finger horizontal gesture is **live**: the popup tracks your fingers in
real time, you reverse direction by reversing the swipe, and lifting commits
the highlighted app.

Edit [config.py](config.py) to remap gestures.

## Requirements

- Ubuntu 22.04 / 24.04 (or any GNOME Wayland desktop)
- Python 3.10+
- A multitouch touchpad (most laptops)

## Install

```bash
git clone https://github.com/jobint001/gesture-app.git
cd gesture-app
bash install.sh
```

`install.sh` will:

- Install `python3-venv`, `dbus`, and `libglib2.0-bin` via apt
- Create a Python venv in `.venv/` and install `evdev`
- Load the `uinput` kernel module at boot
- Drop two udev rules:
  - `/etc/udev/rules.d/99-uinput.rules` — makes `/dev/uinput` writable so the
    app can synthesize key presses for Alt+Tab, etc.
  - `/etc/udev/rules.d/99-touchpad-uaccess.rules` — makes the touchpad
    `/dev/input/eventN` readable so the app can grab raw multitouch events
- Bind `Super+D` to `show-desktop` via `gsettings`
- Install a systemd **user** service `~/.config/systemd/user/gesture-app.service`
  that auto-starts on login

After install, gestures should work immediately.

## Project layout

```
gesture-app/
├── main.py                       # async entry point + signal handling
├── config.py                     # gesture → action mapping (edit this)
├── requirements.txt
├── install.sh
├── detector/
│   ├── device_finder.py          # locates the touchpad in /dev/input/
│   ├── touch_reader.py           # async evdev MT-protocol-B reader
│   └── gesture_recognizer.py     # classifies frames into gestures (live + one-shot)
└── actions/
    ├── gnome_actions.py          # high-level actions (D-Bus + key fallback)
    └── key_actions.py            # uinput virtual keyboard (Alt+Tab, Super, …)
```

### How it works

1. **Touch reader** opens the touchpad as an evdev device and tracks each MT
   slot's `tracking_id`, `position_x`, `position_y`. After every `SYN_REPORT`
   it hands a snapshot of all active slots to the recognizer.
2. **Recognizer** maintains per-gesture state (start centroid, peak finger
   count, anchors). It fires:
   - `on_gesture(name, info)` once when all fingers lift, for one-shot gestures
     like swipe-up or 3-finger swipe-left.
   - `on_live(name, phase, info)` for live gestures (currently 4-finger
     horizontal). Phases are `begin`, `update` (one per `LIVE_STEP_DISTANCE`
     of motion), and `end`.
3. **Actions** translate gesture names into either D-Bus calls
   (`org.gnome.Shell.Eval`) or uinput key chords. Some Shell actions are
   silently restricted on GNOME 45+ (`Eval` is sandboxed), so workspace switch
   and show-desktop go straight to keyboard shortcuts.

## Service management

```bash
systemctl --user status  gesture-app.service
systemctl --user restart gesture-app.service
systemctl --user stop    gesture-app.service
journalctl --user -u     gesture-app.service -f
```

## Manual run / debugging

```bash
.venv/bin/python main.py --list      # list /dev/input devices the app can see
.venv/bin/python main.py --debug     # run in foreground with verbose logging
```

The `--debug` log shows each `SYN_REPORT` with the live finger count and
every gesture as it's detected, which is useful when tuning thresholds.

## Tuning

Constants in [detector/gesture_recognizer.py](detector/gesture_recognizer.py):

- `SWIPE_MIN_DISTANCE` — minimum swipe length to fire one-shot gestures
- `SWIPE_LOCK_RATIO` — how dominant the primary axis must be (rejects diagonals)
- `LIVE_ENTRY_DISTANCE` — px of horizontal motion before the live Alt+Tab popup appears
- `LIVE_STEP_DISTANCE` — px between Tab presses while the popup is live
- `TAP_MAX_MOVE` / `TAP_MAX_DURATION` — what counts as a tap vs. a swipe

## Known limitations

- **GNOME's built-in 3-finger horizontal swipe** also switches workspaces, so
  the default config maps the 3-finger swipe to the same workspace switch — it
  appears as a single action rather than a double-fire. To remap 3-finger
  horizontal to something else, you need to install a GNOME extension that
  disables the built-in (e.g. *Disable Gestures 2024*).
- 4-finger gestures have no GNOME built-in, so they're conflict-free.
- `Shell.Eval` is restricted on GNOME 45+. We rely on it for the activities
  overview only; everything else goes through keyboard shortcuts.

## License

MIT
