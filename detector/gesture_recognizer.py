"""
Converts a stream of TouchState frames into named gesture events.

Gesture lifecycle:
  - BEGAN   when N fingers first touch
  - UPDATE  on each subsequent sync (used internally)
  - ENDED   when all fingers lift (gesture fires here)
"""

import logging
import math
import time
from typing import Optional, Callable, Awaitable

from detector.touch_reader import TouchState

log = logging.getLogger(__name__)

# ── tunables ─────────────────────────────────────────────────────────────────
SWIPE_MIN_DISTANCE = 70       # px: minimum net displacement to count as swipe
SWIPE_LOCK_RATIO   = 2.2      # axis lock: primary/secondary must exceed this
# 2-finger swipes share the touchpad with libinput's 2-finger scroll, so we
# require a longer & much more horizontally-dominant motion to call it a swipe.
SWIPE_2F_MIN_DISTANCE = 200
SWIPE_2F_LOCK_RATIO   = 4.5
PINCH_MIN_RATIO    = 0.25     # scale change fraction to fire pinch
TAP_MAX_MOVE       = 12       # px: max finger travel for a tap
TAP_MAX_DURATION   = 0.35     # seconds: max duration for a tap
HOLD_MIN_DURATION  = 0.6      # seconds: minimum hold for long-press (future)

# Live (interactive) gesture tunables — currently used for 4-finger horizontal
# swipes that drive an interactive Alt+Tab popup.
LIVE_ENTRY_DISTANCE = 60      # px of horizontal motion to enter live mode
LIVE_STEP_DISTANCE  = 100     # px between live "step" events (= one Tab press)
# ─────────────────────────────────────────────────────────────────────────────

GestureHandler     = Callable[[str, dict], Awaitable[None]]
LiveGestureHandler = Callable[[str, str, dict], Awaitable[None]]


def _centroid(slots):
    """Return (x, y) centroid of active slots that have position data."""
    valid = [s for s in slots if s.x is not None and s.y is not None]
    if not valid:
        return None, None
    return (sum(s.x for s in valid) / len(valid),
            sum(s.y for s in valid) / len(valid))


def _avg_distance(slots):
    """Mean pairwise distance – proxy for pinch spread."""
    valid = [s for s in slots if s.x is not None and s.y is not None]
    if len(valid) < 2:
        return 0.0
    cx, cy = _centroid(valid)
    return sum(math.hypot(s.x - cx, s.y - cy) for s in valid) / len(valid)


class GestureRecognizer:
    """
    Stateful recognizer.  Feed it TouchState frames via `feed(state)`.
    On gesture completion it calls `on_gesture(name, info)`.
    """

    def __init__(self, on_gesture: GestureHandler,
                 on_live: Optional[LiveGestureHandler] = None):
        self.on_gesture = on_gesture
        self.on_live = on_live
        self._reset()

    def _reset(self):
        self._prev_fingers = 0
        self._gesture_start_time: Optional[float] = None
        self._start_cx: Optional[float] = None
        self._start_cy: Optional[float] = None
        self._start_spread: Optional[float] = None
        self._peak_fingers = 0
        self._start_positions: dict = {}   # slot_id → (x, y) at gesture start
        self._last_cx: Optional[float] = None
        self._last_cy: Optional[float] = None
        # Live (interactive) gesture state
        self._live_active = False
        self._live_name: Optional[str] = None
        self._live_anchor_x: Optional[float] = None

    async def feed(self, state: TouchState):
        fingers = state.finger_count()
        active = state.active_slots()

        # ── Gesture BEGAN ────────────────────────────────────────────────────
        if fingers > 0 and self._prev_fingers == 0:
            log.debug("Gesture BEGAN  fingers=%d", fingers)
            self._gesture_start_time = time.monotonic()
            cx, cy = _centroid(active)
            self._start_cx = cx
            self._start_cy = cy
            self._last_cx = cx
            self._last_cy = cy
            self._start_spread = _avg_distance(active)
            self._peak_fingers = fingers
            self._start_positions = {
                id(s): (s.x, s.y) for s in active
                if s.x is not None and s.y is not None
            }

        # ── During gesture ───────────────────────────────────────────────────
        elif fingers > 0:
            cx, cy = _centroid(active)
            # Only track displacement while the full set of fingers is on the
            # pad.  Once a finger lifts the centroid jumps to the remaining
            # finger(s), which would create a large spurious displacement and
            # could fire an unintended swipe (e.g. 2-finger touch → "back").
            if fingers >= self._peak_fingers:
                self._last_cx = cx
                self._last_cy = cy

            finger_count_changed = (fingers != self._prev_fingers)

            if fingers > self._peak_fingers:
                self._peak_fingers = fingers
                # Re-anchor start to current position on finger-count change
                self._start_cx = cx
                self._start_cy = cy
                self._start_spread = _avg_distance(active)
                self._start_positions = {
                    id(s): (s.x, s.y) for s in active
                    if s.x is not None and s.y is not None
                }
                self._gesture_start_time = time.monotonic()

            # Live interactive gestures (currently: 4-finger horizontal swipe).
            # Only run while at least 4 fingers are still on the pad — once a
            # finger lifts, the centroid jumps and would generate a spurious
            # step right before commit.
            if (self.on_live is not None
                    and self._peak_fingers >= 4
                    and fingers >= 4
                    and cx is not None and self._start_cx is not None):
                # On finger-count change re-anchor instead of firing — avoids
                # treating the centroid shift from a finger lift as motion.
                if finger_count_changed and self._live_active:
                    self._live_anchor_x = cx
                else:
                    await self._update_live(cx, cy)

        # ── Gesture ENDED ────────────────────────────────────────────────────
        elif fingers == 0 and self._prev_fingers > 0:
            log.debug("Gesture ENDED  peak_fingers=%d  live=%s",
                      self._peak_fingers, self._live_active)
            if self._live_active and self.on_live is not None:
                await self.on_live(self._live_name, "end", {})
            else:
                await self._fire(state)
            self._reset()

        self._prev_fingers = fingers

    async def _update_live(self, cx: float, cy: float):
        """Drive interactive 4-finger horizontal swipe → Alt+Tab popup."""
        dx = cx - self._start_cx
        dy = cy - (self._start_cy or 0)

        if not self._live_active:
            # Need enough horizontal motion AND a clearly horizontal axis to begin
            if abs(dx) < LIVE_ENTRY_DISTANCE:
                return
            if abs(dx) < abs(dy) * SWIPE_LOCK_RATIO:
                return
            self._live_active = True
            self._live_name = "swipe_4_horizontal"
            self._live_anchor_x = cx
            direction = 1 if dx > 0 else -1
            log.debug("Live BEGIN  dir=%+d  dx=%.1f", direction, dx)
            await self.on_live(self._live_name, "begin", {"direction": direction})
            return

        # Already live — fire one update for each LIVE_STEP_DISTANCE crossed.
        # Loop in case the user moved several steps' worth between SYN events
        # (otherwise a fast swipe would under-count Tab presses).
        while True:
            dx_anchor = cx - self._live_anchor_x
            if abs(dx_anchor) < LIVE_STEP_DISTANCE:
                return
            direction = 1 if dx_anchor > 0 else -1
            log.debug("Live UPDATE dir=%+d  dx_anchor=%.1f", direction, dx_anchor)
            await self.on_live(self._live_name, "update", {"direction": direction})
            # Advance anchor by exactly one step in that direction
            self._live_anchor_x += direction * LIVE_STEP_DISTANCE

    async def _fire(self, state: TouchState):
        if self._gesture_start_time is None:
            return

        n = self._peak_fingers
        duration = time.monotonic() - self._gesture_start_time

        cx = self._last_cx
        cy = self._last_cy
        if cx is None or self._start_cx is None:
            return

        dx = cx - self._start_cx
        dy = cy - self._start_cy
        dist = math.hypot(dx, dy)

        log.debug("Gesture ended: fingers=%d dx=%.1f dy=%.1f dist=%.1f dur=%.3f",
                  n, dx, dy, dist, duration)

        # ── TAP detection ────────────────────────────────────────────────────
        if dist < TAP_MAX_MOVE and duration < TAP_MAX_DURATION:
            if n == 3:
                await self.on_gesture("tap_3", {"fingers": 3})
            return

        # ── PINCH (2-finger) ─────────────────────────────────────────────────
        if n == 2:
            end_spread = _avg_distance(state.active_slots()) or self._start_spread
            if self._start_spread and self._start_spread > 0:
                ratio = (end_spread - self._start_spread) / self._start_spread
                if abs(ratio) >= PINCH_MIN_RATIO:
                    direction = "in" if ratio < 0 else "out"
                    await self.on_gesture(f"pinch_{direction}", {"ratio": ratio})
                    return
            # No pinch detected — fall through to swipe so 2-finger horizontal
            # translation can fire swipe_2_left / swipe_2_right (browser back/fwd).

        # ── SWIPE ────────────────────────────────────────────────────────────
        # 2-finger has stricter thresholds because libinput already uses 2-finger
        # motion for scrolling — we want to fire only on deliberate horizontal swipes.
        min_dist   = SWIPE_2F_MIN_DISTANCE if n == 2 else SWIPE_MIN_DISTANCE
        lock_ratio = SWIPE_2F_LOCK_RATIO   if n == 2 else SWIPE_LOCK_RATIO

        if dist < min_dist:
            return

        adx, ady = abs(dx), abs(dy)

        # Require a dominant axis
        if adx < ady * lock_ratio and ady < adx * lock_ratio:
            log.debug("Diagonal swipe ignored (no dominant axis)")
            return

        # 2-finger gestures only fire on horizontal motion (no swipe_2_up/down)
        if n == 2 and ady > adx:
            return

        if adx >= ady:
            direction = "right" if dx > 0 else "left"
        else:
            direction = "down" if dy > 0 else "up"

        gesture_name = f"swipe_{n}_{direction}"
        await self.on_gesture(gesture_name, {
            "fingers": n,
            "direction": direction,
            "dx": dx,
            "dy": dy,
            "distance": dist,
            "duration": duration,
        })
