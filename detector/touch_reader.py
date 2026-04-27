"""Reads raw evdev multitouch (MT Protocol B) events and tracks touch slots."""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, Awaitable

from evdev import ecodes

log = logging.getLogger(__name__)


@dataclass
class Slot:
    """State for a single MT slot (one finger)."""
    tracking_id: int = -1   # -1 means unused
    x: Optional[int] = None
    y: Optional[int] = None
    start_x: Optional[int] = None
    start_y: Optional[int] = None
    active: bool = False


@dataclass
class TouchState:
    """Complete state of all active touch slots at a given moment."""
    slots: Dict[int, Slot] = field(default_factory=dict)
    current_slot: int = 0

    def active_slots(self):
        return [s for s in self.slots.values() if s.active]

    def finger_count(self):
        return len(self.active_slots())


GestureCallback = Callable[["TouchState"], Awaitable[None]]


class TouchReader:
    """
    Reads evdev events from a touchpad device.
    Awaits `on_sync` immediately after each SYN_REPORT so the recognizer
    always sees the state as it was at that exact sync boundary — no race
    between the callback and the next batch of events.
    """

    def __init__(self, device, on_sync: GestureCallback):
        self.device = device
        self.on_sync = on_sync
        self.state = TouchState()
        self._running = False

        # Pre-populate slots based on ABS_MT_SLOT max reported by the device
        caps = device.capabilities()
        num_slots = 10  # safe default
        if ecodes.EV_ABS in caps:
            for code, info in caps[ecodes.EV_ABS]:
                if code == ecodes.ABS_MT_SLOT:
                    num_slots = info.max + 1
                    break

        for i in range(num_slots):
            self.state.slots[i] = Slot()

    async def run(self):
        self._running = True
        log.info("Reading events from %s (%s)", self.device.path, self.device.name)
        async for event in self.device.async_read_loop():
            if not self._running:
                break

            if event.type == ecodes.EV_ABS:
                self._handle_abs(event)

            elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                # Await directly — ensures the recognizer processes this frame
                # before we read (and mutate state with) the next batch of events.
                n = self.state.finger_count()
                log.debug("SYN_REPORT  fingers=%d", n)
                await self.on_sync(self.state)

    def stop(self):
        self._running = False

    def _handle_abs(self, event):
        code = event.code
        val  = event.value
        slots = self.state.slots
        cur   = self.state.current_slot

        if code == ecodes.ABS_MT_SLOT:
            self.state.current_slot = val
            if val not in slots:
                slots[val] = Slot()
            return

        slot = slots.get(cur)
        if slot is None:
            return

        if code == ecodes.ABS_MT_TRACKING_ID:
            if val == -1:
                slot.active = False
                slot.tracking_id = -1
            else:
                slot.tracking_id = val
                slot.active = True
                # anchor start position (may be None if pos events come next)
                slot.start_x = slot.x
                slot.start_y = slot.y

        elif code == ecodes.ABS_MT_POSITION_X:
            if slot.start_x is None:
                slot.start_x = val
            slot.x = val

        elif code == ecodes.ABS_MT_POSITION_Y:
            if slot.start_y is None:
                slot.start_y = val
            slot.y = val
