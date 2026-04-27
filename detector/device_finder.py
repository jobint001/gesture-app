"""Finds the touchpad device in /dev/input/ using evdev capabilities."""

import evdev
from evdev import ecodes


def find_touchpad():
    """Return the first evdev device that looks like a multitouch touchpad."""
    candidates = []

    permission_denied = []

    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            permission_denied.append(path)
            continue
        except OSError:
            continue

        caps = dev.capabilities()

        # Must support absolute axes
        if ecodes.EV_ABS not in caps:
            continue

        abs_axes = [code for code, _ in caps[ecodes.EV_ABS]]

        # Multitouch touchpad: needs MT_POSITION_X and MT_POSITION_Y
        has_mt = (ecodes.ABS_MT_POSITION_X in abs_axes and
                  ecodes.ABS_MT_POSITION_Y in abs_axes)

        if not has_mt:
            continue

        name_lower = dev.name.lower()
        # Prefer devices whose name contains touchpad-related keywords
        is_touchpad = any(kw in name_lower for kw in
                          ('touchpad', 'trackpad', 'touch pad', 'synaptics',
                           'elan', 'alps', 'goodix'))

        candidates.append((dev, is_touchpad))

    # Prefer named touchpads, then fall back to any MT device
    for dev, is_named in sorted(candidates, key=lambda x: not x[1]):
        return dev

    if permission_denied:
        import logging
        logging.getLogger(__name__).error(
            "Found %d input device(s) but permission was denied: %s\n"
            "  Fix:  sudo udevadm trigger --subsystem-match=input --action=change\n"
            "  Then: systemctl --user restart gesture-app.service\n"
            "  Or log out and back in so the 'input' group takes effect.",
            len(permission_denied), ", ".join(permission_denied)
        )
    return None


def list_input_devices():
    """Print all input devices (useful for debugging)."""
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            print(f"{path}: {dev.name}")
        except (PermissionError, OSError) as e:
            print(f"{path}: (error: {e})")
