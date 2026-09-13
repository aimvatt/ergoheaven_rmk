#!/usr/bin/env python3
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# 1) Expose the existing Entropy scroll sensitivity field to the K:04 touchpad driver.
module_path = Path("keyboards/k04/src/module_settings.rs")
module = module_path.read_text()
module = replace_once(
    module,
    "pub fn touch_gestures_enabled(side: u8) -> bool {",
    """pub fn touch_scroll_sens(side: u8) -> u8 {
    byte(if side == 0 {
        IDX_LEFT_SCROLL_SENS
    } else {
        IDX_RIGHT_SCROLL_SENS
    })
    .max(1)
}

pub fn touch_gestures_enabled(side: u8) -> bool {""",
    "module_settings touch_scroll_sens",
)
module_path.write_text(module)


# 2) K:04 touchpad: accumulated, adjustable native two-finger scrolling + three-finger gestures.
touch_path = Path("keyboards/k04/src/touchpad.rs")
touch = touch_path.read_text()

touch = replace_once(
    touch,
    """const SCROLL_DIVISOR: i16 = 8;
const BUTTON_LEFT: u8 = 1 << 0;
const BUTTON_RIGHT: u8 = 1 << 1;""",
    """const SCROLL_DIVISOR: i32 = 8;
const BUTTON_LEFT: u8 = 1 << 0;
const BUTTON_RIGHT: u8 = 1 << 1;
const BUTTON_MIDDLE: u8 = 1 << 2;
const TOUCH_ACTION_THREE_SWIPE_UP: u8 = 16;
const TOUCH_ACTION_THREE_SWIPE_DOWN: u8 = 17;
const TOUCH_ACTION_THREE_SWIPE_LEFT: u8 = 18;
const TOUCH_ACTION_THREE_SWIPE_RIGHT: u8 = 19;
const THREE_FINGER_SWIPE_THRESHOLD: i32 = 180;
const THREE_FINGER_TAP_MAX_MOTION: i32 = 60;
const THREE_FINGER_TAP_MAX_TIME: Duration = Duration::from_millis(500);""",
    "touchpad constants",
)

touch = replace_once(
    touch,
    """    multi_finger_samples: u8,
    acc_x: i32,
    acc_y: i32,
    last_report: Instant,""",
    """    multi_finger_samples: u8,
    acc_x: i32,
    acc_y: i32,
    scroll_acc_x: i32,
    scroll_acc_y: i32,
    three_finger_active: bool,
    three_finger_fired: bool,
    three_finger_acc_x: i32,
    three_finger_acc_y: i32,
    three_finger_started: Instant,
    last_report: Instant,""",
    "touchpad struct state",
)

touch = replace_once(
    touch,
    """            multi_finger_samples: 0,
            acc_x: 0,
            acc_y: 0,
            last_report: Instant::MIN,""",
    """            multi_finger_samples: 0,
            acc_x: 0,
            acc_y: 0,
            scroll_acc_x: 0,
            scroll_acc_y: 0,
            three_finger_active: false,
            three_finger_fired: false,
            three_finger_acc_x: 0,
            three_finger_acc_y: 0,
            three_finger_started: Instant::MIN,
            last_report: Instant::MIN,""",
    "touchpad constructor state",
)

old_multi = """        self.multi_finger_samples = if number_of_fingers >= 2 {
            self.multi_finger_samples.saturating_add(1)
        } else {
            0
        };
        let two_fingers_settled = self.multi_finger_samples >= TOUCH_SCROLL_CONFIRM_SAMPLES;

        let gestures_enabled = module_settings::touch_gestures_enabled(self.side);
"""
new_multi = """        let gestures_enabled = module_settings::touch_gestures_enabled(self.side);

        // Three-finger gestures are recognized in firmware because IQS5xx reports
        // the contact count but does not expose Windows Precision Touchpad gestures.
        // Once a three-finger gesture starts, suppress ordinary two-finger scrolling
        // until all fingers are released so the two paths cannot fire together.
        if gestures_enabled {
            if let Some(action) = self.handle_three_finger(number_of_fingers, x, y) {
                return TouchReadResult::Gesture { buttons: action };
            }
            if self.three_finger_active {
                return TouchReadResult::Contact;
            }
        } else if self.three_finger_active {
            self.reset_three_finger();
        }

        self.multi_finger_samples = if number_of_fingers >= 2 {
            self.multi_finger_samples.saturating_add(1)
        } else {
            0
        };
        let two_fingers_settled = self.multi_finger_samples >= TOUCH_SCROLL_CONFIRM_SAMPLES;
"""
touch = replace_once(touch, old_multi, new_multi, "touchpad three-finger dispatch")

touch = replace_once(
    touch,
    """            return match scroll_delta(x, y) {
                Some((h, v)) => TouchReadResult::Scroll { h, v },
                None => TouchReadResult::Contact,
            };""",
    """            return match self.scroll_delta(x, y) {
                Some((h, v)) => TouchReadResult::Scroll { h, v },
                None => TouchReadResult::Contact,
            };""",
    "touchpad adjustable scroll call",
)

touch = replace_once(
    touch,
    """    fn clear_motion_state(&mut self) {
        self.multi_finger_samples = 0;
        self.acc_x = 0;
        self.acc_y = 0;
    }

    fn schedule_next_probe(&mut self, now: Instant) {""",
    """    fn clear_motion_state(&mut self) {
        self.multi_finger_samples = 0;
        self.acc_x = 0;
        self.acc_y = 0;
        self.scroll_acc_x = 0;
        self.scroll_acc_y = 0;
        self.reset_three_finger();
    }

    fn reset_three_finger(&mut self) {
        self.three_finger_active = false;
        self.three_finger_fired = false;
        self.three_finger_acc_x = 0;
        self.three_finger_acc_y = 0;
        self.three_finger_started = Instant::MIN;
    }

    fn handle_three_finger(&mut self, number_of_fingers: u8, x: i16, y: i16) -> Option<u8> {
        if number_of_fingers >= 3 {
            if !self.three_finger_active {
                self.three_finger_active = true;
                self.three_finger_fired = false;
                self.three_finger_acc_x = 0;
                self.three_finger_acc_y = 0;
                self.three_finger_started = Instant::now();
                self.multi_finger_samples = 0;
                self.scroll_acc_x = 0;
                self.scroll_acc_y = 0;
            }

            self.three_finger_acc_x = self.three_finger_acc_x.saturating_add(i32::from(x));
            self.three_finger_acc_y = self.three_finger_acc_y.saturating_add(i32::from(y));

            if self.three_finger_fired {
                return None;
            }

            let ax = self.three_finger_acc_x.abs();
            let ay = self.three_finger_acc_y.abs();
            let action = if ax >= THREE_FINGER_SWIPE_THRESHOLD && ax >= ay {
                Some(if self.three_finger_acc_x > 0 {
                    TOUCH_ACTION_THREE_SWIPE_RIGHT
                } else {
                    TOUCH_ACTION_THREE_SWIPE_LEFT
                })
            } else if ay >= THREE_FINGER_SWIPE_THRESHOLD {
                // K:04 negates raw Y for cursor motion, so raw +Y is a physical
                // swipe up and raw -Y is a physical swipe down.
                Some(if self.three_finger_acc_y > 0 {
                    TOUCH_ACTION_THREE_SWIPE_UP
                } else {
                    TOUCH_ACTION_THREE_SWIPE_DOWN
                })
            } else {
                None
            };

            if action.is_some() {
                self.three_finger_fired = true;
            }
            return action;
        }

        if !self.three_finger_active {
            return None;
        }

        // Keep the gesture captured while fingers are being lifted one by one.
        if number_of_fingers != 0 {
            return None;
        }

        let was_fired = self.three_finger_fired;
        let movement = self.three_finger_acc_x.abs() + self.three_finger_acc_y.abs();
        let elapsed = if self.three_finger_started == Instant::MIN {
            Duration::MAX
        } else {
            Instant::now().duration_since(self.three_finger_started)
        };
        self.reset_three_finger();

        if !was_fired && movement <= THREE_FINGER_TAP_MAX_MOTION && elapsed <= THREE_FINGER_TAP_MAX_TIME {
            Some(BUTTON_MIDDLE)
        } else {
            None
        }
    }

    fn scroll_delta(&mut self, x: i16, y: i16) -> Option<(i16, i16)> {
        // Reuse Entropy's existing Scroll sensitivity value in Normal mode.
        // 1 preserves the old fixed /8 behavior; 2 is 2x slower; 4 is 4x slower.
        let divisor = SCROLL_DIVISOR * i32::from(module_settings::touch_scroll_sens(self.side));

        // Preserve the original horizontal-priority behavior, but accumulate the
        // fractional remainder so large divisors do not discard fine movement.
        if x != 0 {
            self.scroll_acc_y = 0;
            self.scroll_acc_x = self.scroll_acc_x.saturating_add(i32::from(x));
            let out = self.scroll_acc_x / divisor;
            self.scroll_acc_x -= out * divisor;
            let h = out.clamp(i32::from(i16::MIN), i32::from(i16::MAX)) as i16;
            return (h != 0).then_some((h, 0));
        }
        if y != 0 {
            self.scroll_acc_x = 0;
            self.scroll_acc_y = self.scroll_acc_y.saturating_add(i32::from(y));
            let out = self.scroll_acc_y / divisor;
            self.scroll_acc_y -= out * divisor;
            let v = out.clamp(i32::from(i16::MIN), i32::from(i16::MAX)) as i16;
            return (v != 0).then_some((0, v));
        }
        None
    }

    fn schedule_next_probe(&mut self, now: Instant) {""",
    "touchpad helper methods",
)

touch = replace_once(
    touch,
    """fn scroll_delta(x: i16, y: i16) -> Option<(i16, i16)> {
    // IQS5xx and the original K:04 backend report only one scroll direction
    // per sample, with horizontal motion taking priority.
    if x != 0 {
        let h = x / SCROLL_DIVISOR;
        return (h != 0).then_some((h, 0));
    }
    if y != 0 {
        let v = y / SCROLL_DIVISOR;
        return (v != 0).then_some((0, v));
    }
    None
}

""",
    "",
    "remove old fixed scroll_delta",
)

touch_path.write_text(touch)


# 3) Central pointing processor: interpret custom Axis::Z actions and emit Windows shortcuts.
pointing_path = Path("rmk/src/input_device/pointing.rs")
pointing = pointing_path.read_text()

pointing = replace_once(
    pointing,
    """const QUBE_TOUCH_LEFT_BUTTON: u8 = 1 << 0;
const QUBE_TOUCH_RIGHT_BUTTON: u8 = 1 << 1;""",
    """const QUBE_TOUCH_LEFT_BUTTON: u8 = 1 << 0;
const QUBE_TOUCH_RIGHT_BUTTON: u8 = 1 << 1;
const QUBE_TOUCH_MIDDLE_BUTTON: u8 = 1 << 2;
const QUBE_TOUCH_THREE_SWIPE_UP: i16 = 16;
const QUBE_TOUCH_THREE_SWIPE_DOWN: i16 = 17;
const QUBE_TOUCH_THREE_SWIPE_LEFT: i16 = 18;
const QUBE_TOUCH_THREE_SWIPE_RIGHT: i16 = 19;
const HID_MOD_LEFT_CTRL: u8 = 1 << 0;
const HID_MOD_LEFT_GUI: u8 = 1 << 3;
const HID_KEY_D: u8 = 0x07;
const HID_KEY_TAB: u8 = 0x2b;
const HID_KEY_RIGHT: u8 = 0x4f;
const HID_KEY_LEFT: u8 = 0x50;""",
    "pointing gesture constants",
)

pointing = replace_once(
    pointing,
    """        let mut wheel = 0i16;
        let mut pan = 0i16;
        let mut gesture_buttons = 0u8;""",
    """        let mut wheel = 0i16;
        let mut pan = 0i16;
        let mut gesture_action = 0i16;""",
    "pointing gesture variable",
)

pointing = replace_once(
    pointing,
    """                Axis::Z if source.kind == QubePointingKind::Touch => {
                    gesture_buttons = qube_touch_gesture_buttons(axis_event.value)
                }""",
    """                Axis::Z if source.kind == QubePointingKind::Touch => {
                    gesture_action = qube_touch_gesture_action(axis_event.value)
                }""",
    "pointing gesture parse",
)

pointing = replace_once(
    pointing,
    """        if gesture_buttons != 0 {
            self.click(gesture_buttons).await;
            return;
        }
""",
    """        if gesture_action != 0 {
            match gesture_action {
                value if value == i16::from(QUBE_TOUCH_LEFT_BUTTON) => self.click(QUBE_TOUCH_LEFT_BUTTON).await,
                value if value == i16::from(QUBE_TOUCH_RIGHT_BUTTON) => self.click(QUBE_TOUCH_RIGHT_BUTTON).await,
                value if value == i16::from(QUBE_TOUCH_MIDDLE_BUTTON) => self.click(QUBE_TOUCH_MIDDLE_BUTTON).await,
                QUBE_TOUCH_THREE_SWIPE_UP => tap_chord(HID_MOD_LEFT_GUI, HID_KEY_TAB).await,
                QUBE_TOUCH_THREE_SWIPE_DOWN => tap_chord(HID_MOD_LEFT_GUI, HID_KEY_D).await,
                QUBE_TOUCH_THREE_SWIPE_LEFT => {
                    tap_chord(HID_MOD_LEFT_GUI | HID_MOD_LEFT_CTRL, HID_KEY_LEFT).await
                }
                QUBE_TOUCH_THREE_SWIPE_RIGHT => {
                    tap_chord(HID_MOD_LEFT_GUI | HID_MOD_LEFT_CTRL, HID_KEY_RIGHT).await
                }
                _ => {}
            }
            return;
        }
""",
    "pointing gesture actions",
)

pointing = replace_once(
    pointing,
    """fn qube_touch_gesture_buttons(value: i16) -> u8 {
    match value {
        value if value == i16::from(QUBE_TOUCH_LEFT_BUTTON) => QUBE_TOUCH_LEFT_BUTTON,
        value if value == i16::from(QUBE_TOUCH_RIGHT_BUTTON) => QUBE_TOUCH_RIGHT_BUTTON,
        _ => 0,
    }
}""",
    """fn qube_touch_gesture_action(value: i16) -> i16 {
    match value {
        value if value == i16::from(QUBE_TOUCH_LEFT_BUTTON) => value,
        value if value == i16::from(QUBE_TOUCH_RIGHT_BUTTON) => value,
        value if value == i16::from(QUBE_TOUCH_MIDDLE_BUTTON) => value,
        QUBE_TOUCH_THREE_SWIPE_UP
        | QUBE_TOUCH_THREE_SWIPE_DOWN
        | QUBE_TOUCH_THREE_SWIPE_LEFT
        | QUBE_TOUCH_THREE_SWIPE_RIGHT => value,
        _ => 0,
    }
}""",
    "pointing gesture action filter",
)

pointing = replace_once(
    pointing,
    """/// Tap a key (press and release with a short delay) - used for caret mode
/// NOTE: This is a basic implementation because at the current state Keyboard (in keyboard.rs) does not support""",
    """async fn tap_chord(modifier: u8, keycode: u8) {
    send_hid_report(Report::KeyboardReport(KeyboardReport {
        modifier,
        reserved: 0,
        leds: 0,
        keycodes: [keycode, 0, 0, 0, 0, 0],
    }))
    .await;
    Timer::after_millis(8).await;
    send_hid_report(Report::KeyboardReport(KeyboardReport {
        modifier: 0,
        reserved: 0,
        leds: 0,
        keycodes: [0, 0, 0, 0, 0, 0],
    }))
    .await;
    Timer::after_millis(5).await;
}

/// Tap a key (press and release with a short delay) - used for caret mode
/// NOTE: This is a basic implementation because at the current state Keyboard (in keyboard.rs) does not support""",
    "pointing tap_chord helper",
)

pointing_path.write_text(pointing)

print("K:04 custom touchpad patch applied successfully")
