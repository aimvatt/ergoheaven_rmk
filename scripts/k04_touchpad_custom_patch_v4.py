#!/usr/bin/env python3
from pathlib import Path
import runpy

# Start from the hardware-tested v3 native-scroll behavior, then remove the
# touchpad's idle/suspend latency without changing the gesture/scroll math.
runpy.run_path("scripts/k04_touchpad_custom_patch_v3.py", run_name="__main__")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


touch_path = Path("keyboards/k04/src/touchpad.rs")
touch = touch_path.read_text()

# Keep both the IQS5xx scan cadence and the host polling cadence at 8 ms in all
# autonomous controller modes. We deliberately do NOT force MANUAL_CONTROL:
# Azoteq requires the host to manage reference values in manual mode, so leaving
# automatic mode management intact is safer while still eliminating slow scans.
touch = replace_once(
    touch,
    "const REPORT_RATE_IDLE_MS: u16 = 20;",
    "const REPORT_RATE_IDLE_MS: u16 = 8;",
    "always-on idle report rate",
)
touch = replace_once(
    touch,
    "const REPORT_RATE_LP1_MS: u16 = 40;",
    "const REPORT_RATE_LP1_MS: u16 = 8;",
    "always-on LP1 report rate",
)
touch = replace_once(
    touch,
    "const REPORT_RATE_LP2_MS: u16 = 40;",
    "const REPORT_RATE_LP2_MS: u16 = 8;",
    "always-on LP2 report rate",
)

# The touchpad must remain a wake source for the keyboard. The old loop stopped
# polling it when the keyboard-wide sleep flag was set, which made a touch event
# impossible and created a wake deadlock. Ignore ordinary keyboard sleep here;
# only an actual module deselection is allowed to deactivate/suspend the pad.
touch = replace_once(
    touch,
    "use embassy_futures::select::{select, select3, Either, Either3};",
    "use embassy_futures::select::{select, Either};",
    "remove sleep select imports",
)

old_loop = '''            let sleeping = module_settings::module_sleeping();
            if sleeping {
                self.enter_sleep().await;
                match select(
                    module_settings::wait_for_module_selection_change(
                        self.side,
                        module_settings::ModuleSelection::Touchpad,
                    ),
                    module_settings::wait_for_module_sleep_change(sleeping),
                )
                .await
                {
                    Either::First(_) => self.deactivate().await,
                    Either::Second(_) => self.resume_from_sleep().await,
                }
                continue;
            }

            if self.suspended {
                self.resume_from_sleep().await;
            }

            let deadline = if self.ready { self.next_poll } else { self.next_probe };
            match select3(
                Timer::at(deadline),
                module_settings::wait_for_module_selection_change(
                    self.side,
                    module_settings::ModuleSelection::Touchpad,
                ),
                module_settings::wait_for_module_sleep_change(sleeping),
            )
            .await
            {
                Either3::First(_) => {
                    if module_settings::module_sleeping() {
                        self.enter_sleep().await;
                    } else {
                        self.poll_once().await;
                    }
                }
                Either3::Second(_) => self.deactivate().await,
                Either3::Third(next_sleeping) => {
                    if next_sleeping {
                        self.enter_sleep().await;
                    } else {
                        self.resume_from_sleep().await;
                    }
                }
            }
'''
new_loop = '''            if self.suspended {
                self.resume_from_sleep().await;
            }

            let deadline = if self.ready { self.next_poll } else { self.next_probe };
            match select(
                Timer::at(deadline),
                module_settings::wait_for_module_selection_change(
                    self.side,
                    module_settings::ModuleSelection::Touchpad,
                ),
            )
            .await
            {
                Either::First(_) => self.poll_once().await,
                Either::Second(_) => self.deactivate().await,
            }
'''
touch = replace_once(touch, old_loop, new_loop, "keep touchpad alive through keyboard sleep")

old_poll = '''fn touch_poll_interval(idle_for: Duration) -> Duration {
    let interval_ms = if idle_for < TOUCH_ACTIVE_POLL_WINDOW {
        REPORT_RATE_ACTIVE_MS
    } else if idle_for < TOUCH_IDLE_POLL_WINDOW {
        REPORT_RATE_IDLE_MS
    } else if idle_for < TOUCH_LP1_POLL_WINDOW {
        REPORT_RATE_LP1_MS
    } else {
        REPORT_RATE_LP2_MS
    };
    Duration::from_millis(interval_ms as u64)
}
'''
new_poll = '''fn touch_poll_interval(_idle_for: Duration) -> Duration {
    // Always poll at the active cadence so a touch remains an immediate wake
    // source even after arbitrarily long keyboard inactivity.
    Duration::from_millis(REPORT_RATE_ACTIVE_MS as u64)
}
'''
touch = replace_once(touch, old_poll, new_poll, "fixed 8 ms host polling")

touch_path.write_text(touch)
print("K:04 custom touchpad v4 always-on patch applied successfully")
