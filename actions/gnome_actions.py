"""
GNOME Shell actions via D-Bus (Wayland-safe — no xdotool/ydotool).

Primary path: org.gnome.Shell.Eval — runs arbitrary JS inside the Shell
  process.  Works on Ubuntu GNOME 22.04–24.04.  Falls back to uinput
  keyboard shortcuts if the call fails (e.g. restricted on newer shells).
"""

import asyncio
import logging
import subprocess
from typing import Optional

log = logging.getLogger(__name__)


# ── Low-level gdbus helper ────────────────────────────────────────────────────

def _gdbus_sync(dest: str, path: str, iface: str, method: str,
                gvariant_arg: Optional[str] = None) -> str:
    """
    Call a D-Bus method synchronously via the `gdbus` CLI.
    `gvariant_arg` must be a valid GVariant literal, e.g. '"my string"'.
    """
    cmd = ["gdbus", "call", "--session",
           "--dest", dest,
           "--object-path", path,
           "--method", f"{iface}.{method}"]
    if gvariant_arg is not None:
        cmd.append(gvariant_arg)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"exit code {r.returncode}")
    return r.stdout.strip()


async def _gdbus(dest: str, path: str, iface: str, method: str,
                 gvariant_arg: Optional[str] = None) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _gdbus_sync, dest, path, iface, method, gvariant_arg)


# ── Shell.Eval helper ─────────────────────────────────────────────────────────

async def _shell_eval(js: str):
    """
    Evaluate JS inside the GNOME Shell process.
    The JS must not contain double-quote characters.
    """
    # GVariant string literal uses double quotes
    gvariant = '"' + js + '"'
    await _gdbus(
        "org.gnome.Shell", "/org/gnome/Shell",
        "org.gnome.Shell", "Eval",
        gvariant,
    )


# ── Actions ───────────────────────────────────────────────────────────────────

async def show_activities():
    """Toggle the Activities overview (3-finger swipe up)."""
    log.info("Action: show_activities")
    try:
        await _shell_eval("Main.overview.toggle();")
    except Exception as e:
        log.warning("Shell.Eval failed (%s) — falling back to Super key", e)
        from actions.key_actions import send_super
        await send_super()


async def show_desktop():
    """Show desktop (Super+D — must be bound to 'show-desktop' in GNOME)."""
    log.info("Action: show_desktop")
    # Shell.Eval is silently restricted on GNOME 45+ — go straight to keybind.
    from actions.key_actions import send_super_d
    await send_super_d()


async def next_workspace():
    """Switch to the next workspace (Super+Page Down)."""
    log.info("Action: next_workspace")
    # Shell.Eval is restricted on GNOME 45+ — it silently succeeds without
    # running the JS — so we go straight to the keybinding via uinput.
    from actions.key_actions import send_super_pagedown
    await send_super_pagedown()


async def prev_workspace():
    """Switch to the previous workspace (Super+Page Up)."""
    log.info("Action: prev_workspace")
    from actions.key_actions import send_super_pageup
    await send_super_pageup()


class _LiveAltTabState:
    """Tracks whether Alt is currently held by an in-progress live gesture."""
    def __init__(self):
        self.alt_held = False
        self.lock = asyncio.Lock()


_alt_state = _LiveAltTabState()


async def _tap_directional(direction: int):
    """Tab (right) or Shift+Tab (left) — assumes Alt already held."""
    from actions import key_actions
    from evdev import ecodes
    if direction < 0:
        await key_actions.press(ecodes.KEY_LEFTSHIFT)
        await key_actions.tap(ecodes.KEY_TAB, hold=0.04)
        await key_actions.release(ecodes.KEY_LEFTSHIFT)
    else:
        await key_actions.tap(ecodes.KEY_TAB, hold=0.04)


async def live_alt_tab(phase: str, info: dict):
    """
    Interactive Alt+Tab driven by live gesture phases.

      begin  → press Alt, briefly wait, tap Tab in initial direction.
               The app switcher popup appears with the next/prev app highlighted.
      update → tap Tab (or Shift+Tab) in the direction the user is currently
               swiping.  Each step moves the highlight by one app.
      end    → release Alt.  Whatever app is highlighted becomes focused.

    The recognizer fires `update` once per LIVE_STEP_DISTANCE px of horizontal
    motion, so the popup tracks the user's swipe in near-real-time and they can
    reverse direction by reversing their swipe.
    """
    from actions import key_actions
    from evdev import ecodes

    async with _alt_state.lock:
        if phase == "begin":
            direction = info.get("direction", 1)
            log.info("live_alt_tab BEGIN dir=%+d", direction)
            await key_actions.press(ecodes.KEY_LEFTALT)
            _alt_state.alt_held = True
            # Give Mutter ~60ms to see Alt as a held modifier before Tab arrives
            await asyncio.sleep(0.06)
            await _tap_directional(direction)

        elif phase == "update":
            if not _alt_state.alt_held:
                # Defensive: shouldn't happen, but handle out-of-order events
                await key_actions.press(ecodes.KEY_LEFTALT)
                _alt_state.alt_held = True
                await asyncio.sleep(0.06)
            direction = info.get("direction", 1)
            log.debug("live_alt_tab UPDATE dir=%+d", direction)
            await _tap_directional(direction)
            # Give Mutter time to finish animating the switcher before the
            # next Tab arrives — firing too fast corrupts workspaceAnimation.js
            # (record is undefined crash) and eventually stalls the compositor.
            await asyncio.sleep(0.08)

        elif phase == "end":
            log.info("live_alt_tab END (committing selection)")
            if _alt_state.alt_held:
                # Brief settle so the last Tab is fully processed by Mutter
                # before Alt is released — prevents the commit racing past the
                # final highlight position.
                await asyncio.sleep(0.08)
                await key_actions.release(ecodes.KEY_LEFTALT)
                _alt_state.alt_held = False


async def alt_tab(reverse: bool = False, info: Optional[dict] = None, **_):
    """
    Fallback Alt+Tab for any 4-finger swipe that didn't engage live mode
    (i.e., very short swipes that didn't reach the live-entry threshold).
    Just sends a single Alt+Tab / Alt+Shift+Tab and releases.
    """
    log.info("Action: alt_tab (fallback) reverse=%s", reverse)
    from actions import key_actions
    from evdev import ecodes

    await key_actions.press(ecodes.KEY_LEFTALT)
    if reverse:
        await key_actions.press(ecodes.KEY_LEFTSHIFT)
    await asyncio.sleep(0.06)
    await key_actions.tap(ecodes.KEY_TAB, hold=0.04)
    await asyncio.sleep(0.20)  # let the popup render briefly
    if reverse:
        await key_actions.release(ecodes.KEY_LEFTSHIFT)
    await key_actions.release(ecodes.KEY_LEFTALT)


async def middle_click():
    """Simulate a middle click via a Clutter virtual pointer (3-finger tap)."""
    log.info("Action: middle_click")
    js = (
        "var seat=Clutter.get_default_backend().get_default_seat();"
        "var dev=seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);"
        "var [x,y]=global.get_pointer();"
        "var t=Clutter.get_current_event_time();"
        "dev.notify_button(t,x,y,2,Clutter.ButtonState.PRESSED);"
        "dev.notify_button(t,x,y,2,Clutter.ButtonState.RELEASED);"
    )
    try:
        await _shell_eval(js)
    except Exception as e:
        log.warning("middle_click failed: %s", e)
