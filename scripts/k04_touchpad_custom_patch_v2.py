#!/usr/bin/env python3
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# Minimal v2 patch: keep stock touch/gesture state machine and only route
# native two-finger scroll through the exact same settings pipeline as Scroll mode.

touch_path = Path("keyboards/k04/src/touchpad.rs")
touch = touch_path.read_text()

touch = replace_once(
    touch,
    "const SCROLL_DIVISOR: i16 = 8;\n",
    "",
    "remove legacy fixed scroll divisor",
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
    """fn scroll_delta(x: i16, y: i16) -> Option<(i16, i16)> {
    // Preserve raw logical motion for native two-finger scroll. H/V are used
    // only as a transport tag here; the central processor applies the same
    // orientation, inversion, sensitivity divisor and remainder accumulator
    // that dedicated Scroll mode uses.
    if x == 0 && y == 0 {
        None
    } else {
        Some((x, y.saturating_neg()))
    }
}
""",
    "route native two-finger scroll as raw logical motion",
)

touch_path.write_text(touch)

pointing_path = Path("rmk/src/input_device/pointing.rs")
pointing = pointing_path.read_text()

pointing = replace_once(
    pointing,
    """        if wheel != 0 || pan != 0 {
            let invert_x = if self.settings.invert_scroll_x(source.side) {
                -1
            } else {
                1
            };
            let invert_y = if self.settings.invert_scroll_y(source.side) {
                -1
            } else {
                1
            };
            self.send_mouse(0, 0, 0, wheel.saturating_mul(invert_y), pan.saturating_mul(invert_x))
                .await;
            return;
        }
""",
    """        if wheel != 0 || pan != 0 {
            // Native two-finger touchpad scroll is transported as logical XY in
            // H/V. Run it through the exact same transform/settings path as
            // dedicated Scroll mode so calibration carries over 1:1.
            let (x, y) = rotate_motion(pan, wheel, self.settings.orientation(source));
            let invert_x = if self.settings.invert_scroll_x(source.side) {
                -1
            } else {
                1
            };
            let invert_y = if self.settings.invert_scroll_y(source.side) {
                -1
            } else {
                1
            };
            let divisor = self.settings.sens(source.side, QubePointingMode::Scroll);
            let state = &mut self.sides[source.side];
            let (h, v) =
                qube_divided_motion(state, x.saturating_mul(invert_x), y.saturating_mul(invert_y), divisor);
            self.send_mouse(0, 0, 0, v, h).await;
            return;
        }
""",
    "reuse dedicated Scroll mode pipeline for native scroll",
)

pointing_path.write_text(pointing)
print("K:04 custom touchpad v2 patch applied successfully")
