"""
User-editable gesture → action mapping.

Gesture name format:
  swipe_<fingers>_<direction>   e.g. swipe_3_up, swipe_4_left
  tap_<fingers>                 e.g. tap_3
  pinch_in, pinch_out
"""

from actions import gnome_actions

# ── Gesture → coroutine mapping ───────────────────────────────────────────────
GESTURE_MAP = {
    # 3-finger vertical — overview / show desktop
    "swipe_3_up":    gnome_actions.show_activities,
    "swipe_3_down":  gnome_actions.show_desktop,

    # 3-finger horizontal — switch workspaces (matches GNOME's built-in,
    # so no double-action conflict).
    "swipe_3_left":  gnome_actions.next_workspace,
    "swipe_3_right": gnome_actions.prev_workspace,

    # 4-finger horizontal — interactive Alt+Tab popup.
    # Swipe direction matches popup-highlight direction:
    #   swipe LEFT  → Alt+Shift+Tab → highlight moves left
    #   swipe RIGHT → Alt+Tab       → highlight moves right
    "swipe_4_left":  lambda info=None: gnome_actions.alt_tab(reverse=True,
                                                              info=info),
    "swipe_4_right": gnome_actions.alt_tab,

    # 3-finger tap → middle click
    "tap_3": gnome_actions.middle_click,

    # 2-finger pinch — reserved
    # "pinch_in":  ...,
    # "pinch_out": ...,
}
