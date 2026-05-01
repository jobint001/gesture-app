"""
Keyboard injection via the Linux uinput kernel module.
Uses evdev.UInput.

Two layers:
  - low-level: press(), release(), tap()  — raw key control
  - high-level: send_alt_tab(), send_super(), etc. — fire-and-forget actions

Requires write access to /dev/uinput (handled by udev rule in install.sh).
"""

import asyncio
import logging
import time
from typing import Optional

from evdev import ecodes, UInput

log = logging.getLogger(__name__)

_ui: Optional[UInput] = None


def _get_ui() -> Optional[UInput]:
    global _ui
    if _ui is None:
        try:
            _ui = UInput(
                {
                    ecodes.EV_KEY: [
                        ecodes.KEY_LEFTALT,
                        ecodes.KEY_LEFTSHIFT,
                        ecodes.KEY_TAB,
                        ecodes.KEY_LEFTMETA,
                        ecodes.KEY_PAGEUP,
                        ecodes.KEY_PAGEDOWN,
                        ecodes.KEY_LEFTCTRL,
                        ecodes.KEY_D,
                        ecodes.KEY_LEFT,
                        ecodes.KEY_RIGHT,
                    ]
                },
                name="gesture-app-vkbd",
            )
            log.info("uinput virtual keyboard ready")
        except Exception as e:
            log.error("Failed to create uinput device: %s", e)
    return _ui


# ── Low-level primitives ──────────────────────────────────────────────────────

def _press_sync(key: int):
    ui = _get_ui()
    if ui is None:
        return
    ui.write(ecodes.EV_KEY, key, 1)
    ui.syn()


def _release_sync(key: int):
    ui = _get_ui()
    if ui is None:
        return
    ui.write(ecodes.EV_KEY, key, 0)
    ui.syn()


async def _to_thread(fn, *args):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, fn, *args)


async def press(key: int):
    await _to_thread(_press_sync, key)


async def release(key: int):
    await _to_thread(_release_sync, key)


async def tap(key: int, hold: float = 0.04):
    await press(key)
    await asyncio.sleep(hold)
    await release(key)


def _chord_sync(keys: list):
    ui = _get_ui()
    if ui is None:
        return
    for k in keys:
        ui.write(ecodes.EV_KEY, k, 1)
    ui.syn()
    time.sleep(0.05)
    for k in reversed(keys):
        ui.write(ecodes.EV_KEY, k, 0)
    ui.syn()


async def chord(*keys):
    await _to_thread(_chord_sync, list(keys))


# ── High-level actions ───────────────────────────────────────────────────────

async def send_super():
    """Tap the Super key (opens Activities overview)."""
    await tap(ecodes.KEY_LEFTMETA)


async def send_super_pagedown():
    """Super+Page Down → next workspace."""
    await chord(ecodes.KEY_LEFTMETA, ecodes.KEY_PAGEDOWN)


async def send_super_pageup():
    """Super+Page Up → previous workspace."""
    await chord(ecodes.KEY_LEFTMETA, ecodes.KEY_PAGEUP)


async def send_super_d():
    """Super+D → show desktop (must be bound in keyboard settings)."""
    await chord(ecodes.KEY_LEFTMETA, ecodes.KEY_D)


async def send_alt_left():
    """Alt+Left → browser back."""
    await chord(ecodes.KEY_LEFTALT, ecodes.KEY_LEFT)


async def send_alt_right():
    """Alt+Right → browser forward."""
    await chord(ecodes.KEY_LEFTALT, ecodes.KEY_RIGHT)


def close():
    global _ui
    if _ui is not None:
        _ui.close()
        _ui = None