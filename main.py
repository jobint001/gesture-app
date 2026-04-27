#!/usr/bin/env python3
"""
gesture-app – touchpad gesture recognizer for GNOME on Wayland.

Usage:
  python3 main.py              # auto-detect touchpad
  python3 main.py --device /dev/input/eventN
  python3 main.py --list       # list input devices
  python3 main.py --debug      # verbose logging
"""

import argparse
import asyncio
import logging
import signal
import sys
from typing import Optional

from detector.device_finder import find_touchpad, list_input_devices
from detector.touch_reader import TouchReader
from detector.gesture_recognizer import GestureRecognizer
from config import GESTURE_MAP


def setup_logging(debug: bool):
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)


async def on_gesture(name: str, info: dict):
    """Dispatch a recognised gesture to its configured action."""
    import inspect

    handler = GESTURE_MAP.get(name)
    if handler is None:
        logging.getLogger("dispatch").debug("No action for gesture: %s", name)
        return

    logging.getLogger("dispatch").info("Gesture: %s  → %s()", name,
                                       getattr(handler, "__name__", repr(handler)))
    try:
        # Pass info to handlers that accept it; keep the no-arg API for the rest
        sig = inspect.signature(handler)
        if "info" in sig.parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            await handler(info=info)
        else:
            await handler()
    except Exception as e:
        logging.getLogger("dispatch").error("Action %s failed: %s", name, e)


async def on_live_gesture(name: str, phase: str, info: dict):
    """
    Dispatch a live (in-progress) gesture event to its handler.
    Live gestures fire begin/update/end while the user's fingers are still
    on the touchpad — used for the interactive Alt+Tab popup.
    """
    if name == "swipe_4_horizontal":
        from actions.gnome_actions import live_alt_tab
        try:
            await live_alt_tab(phase, info)
        except Exception as e:
            logging.getLogger("dispatch").error(
                "live_alt_tab(%s) failed: %s", phase, e)


async def main(device_path: Optional[str]):
    log = logging.getLogger("main")

    if device_path:
        import evdev
        try:
            device = evdev.InputDevice(device_path)
        except (PermissionError, OSError) as e:
            log.error("Cannot open %s: %s", device_path, e)
            sys.exit(1)
    else:
        device = find_touchpad()
        if device is None:
            log.error(
                "No multitouch touchpad found in /dev/input/.\n"
                "Make sure you are in the 'input' group:  sudo usermod -aG input $USER\n"
                "Then log out and back in, or run:  newgrp input"
            )
            sys.exit(1)

    log.info("Using device: %s (%s)", device.path, device.name)

    recognizer = GestureRecognizer(on_gesture=on_gesture, on_live=on_live_gesture)
    reader = TouchReader(device=device, on_sync=recognizer.feed)

    loop = asyncio.get_running_loop()

    def _shutdown():
        log.info("Shutting down…")
        reader.stop()
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, _shutdown)
    loop.add_signal_handler(signal.SIGINT,  _shutdown)

    try:
        await reader.run()
    except asyncio.CancelledError:
        pass
    finally:
        device.close()
        log.info("Exited cleanly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GNOME touchpad gesture daemon")
    parser.add_argument("--device", metavar="PATH",
                        help="evdev device path (e.g. /dev/input/event5)")
    parser.add_argument("--list", action="store_true",
                        help="list all input devices and exit")
    parser.add_argument("--debug", action="store_true",
                        help="enable debug logging")
    args = parser.parse_args()

    setup_logging(args.debug)

    if args.list:
        list_input_devices()
        sys.exit(0)

    asyncio.run(main(device_path=args.device))
