#!/usr/bin/env python3
from pathlib import Path
import re
import runpy

# Start from the known-good v3 touch timing + native-scroll changes.  The PTP
# path below then bypasses legacy mouse/scroll generation for the touchpad.
runpy.run_path("scripts/k04_touchpad_custom_patch_v3.py", run_name="__main__")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, repl: str, label: str) -> str:
    out, count = re.subn(pattern, repl, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return out

# ---------------------------------------------------------------------------
# K:04 TPS43/IQS525: publish raw absolute contacts instead of mouse gestures.
# One PointingEvent carries one contact over the already-existing split link:
# X/Y = absolute position, Z = slot/tip/confidence/frame-count metadata.
# ---------------------------------------------------------------------------
touch_path = Path("keyboards/k04/src/touchpad.rs")
touch = touch_path.read_text()

touch = replace_once(
    touch,
    "const REG_PREVIOUS_CYCLE_TIME: u16 = 0x000c;\n",
    "const REG_PREVIOUS_CYCLE_TIME: u16 = 0x000c;\n"
    "const REG_SYSTEM_INFO_0_PTP: u16 = 0x000f;\n"
    "const PTP_FRAME_LEN: usize = 42;\n"
    "const PTP_FINGER_COUNT: usize = 5;\n"
    "const PTP_FINGER0_OFFSET: usize = 7;\n"
    "const PTP_FINGER_STRIDE: usize = 7;\n",
    "PTP register constants",
)

touch = replace_once(
    touch,
    "    multi_finger_samples: u8,\n",
    "    multi_finger_samples: u8,\n"
    "    ptp_active_mask: u8,\n"
    "    ptp_last_x: [u16; PTP_FINGER_COUNT],\n"
    "    ptp_last_y: [u16; PTP_FINGER_COUNT],\n",
    "PTP state fields",
)

touch = replace_once(
    touch,
    "            multi_finger_samples: 0,\n",
    "            multi_finger_samples: 0,\n"
    "            ptp_active_mask: 0,\n"
    "            ptp_last_x: [0; PTP_FINGER_COUNT],\n"
    "            ptp_last_y: [0; PTP_FINGER_COUNT],\n",
    "PTP state init",
)

new_poll = r'''    async fn poll_once(&mut self) {
        if !self.ready {
            if !self.init().await {
                self.schedule_next_probe(Instant::now());
                return;
            }
            self.last_report = Instant::now();
        }

        let active_sample = match self.read_precision_frame().await {
            Ok(active) => {
                self.read_failures = 0;
                active
            }
            Err(()) => {
                self.read_failures = self.read_failures.saturating_add(1);
                if self.read_failures >= TOUCH_READ_FAILURE_REINIT_THRESHOLD {
                    self.reset();
                    Timer::after(Duration::from_millis(50)).await;
                } else {
                    self.next_poll = Instant::now() + self.poll_interval;
                }
                return;
            }
        };

        let now = Instant::now();
        if active_sample {
            self.last_activity = now;
        }
        self.poll_interval = touch_poll_interval(now.duration_since(self.last_activity));
        self.next_poll = now + self.poll_interval;
    }

    async fn read_precision_frame(&mut self) -> Result<bool, ()> {
        let mut data = [0u8; PTP_FRAME_LEN];
        if !self.read(REG_SYSTEM_INFO_0_PTP, &mut data).await {
            return Err(());
        }
        if !self.end_session().await {
            return Err(());
        }

        let system_info_0 = data[0];
        let charging_mode = system_info_0 & SYSTEM_INFO_0_CHARGING_MODE;
        let scanning_for_touch =
            charging_mode == CHARGING_MODE_ACTIVE || charging_mode == CHARGING_MODE_IDLE_TOUCH;
        let valid_frame = scanning_for_touch
            && (system_info_0 & (SYSTEM_INFO_0_ATI_ERROR | SYSTEM_INFO_0_REATI_OCCURRED)) == 0;

        let mut active_mask = 0u8;
        let mut xs = [0u16; PTP_FINGER_COUNT];
        let mut ys = [0u16; PTP_FINGER_COUNT];

        if valid_frame {
            for slot in 0..PTP_FINGER_COUNT {
                let base = PTP_FINGER0_OFFSET + slot * PTP_FINGER_STRIDE;
                let x = u16::from_be_bytes([data[base], data[base + 1]]);
                let y = u16::from_be_bytes([data[base + 2], data[base + 3]]);
                let strength = u16::from_be_bytes([data[base + 4], data[base + 5]]);
                let area = data[base + 6];
                xs[slot] = x;
                ys[slot] = y;
                if strength != 0 || area != 0 {
                    active_mask |= 1u8 << slot;
                }
            }
        }

        // Include just-released contacts for one frame with Tip Switch clear.
        // The slot index is stable in IQS5xx, so it is also the PTP Contact ID.
        let report_mask = active_mask | self.ptp_active_mask;
        let report_count = report_mask.count_ones().min(5) as u8;
        let mut first = true;

        for slot in 0..PTP_FINGER_COUNT {
            let bit = 1u8 << slot;
            if report_mask & bit == 0 {
                continue;
            }
            let tip = active_mask & bit != 0;
            let (x, y) = if tip {
                (xs[slot], ys[slot])
            } else {
                (self.ptp_last_x[slot], self.ptp_last_y[slot])
            };
            if tip {
                self.ptp_last_x[slot] = x;
                self.ptp_last_y[slot] = y;
            }

            // bits 0..2 slot id, bit 3 tip, bit 4 confidence,
            // bits 5..7 total contacts in this hybrid frame (first report only).
            let count = if first { report_count } else { 0 };
            let meta = (slot as u8) | ((tip as u8) << 3) | (1 << 4) | (count << 5);
            first = false;
            publish_event(PointingEvent {
                device_id: self.device_id,
                axes: [
                    AxisEvent { typ: AxisValType::Abs, axis: Axis::X, value: x.min(i16::MAX as u16) as i16 },
                    AxisEvent { typ: AxisValType::Abs, axis: Axis::Y, value: y.min(i16::MAX as u16) as i16 },
                    AxisEvent { typ: AxisValType::Abs, axis: Axis::Z, value: meta as i16 },
                ],
            });
        }

        self.ptp_active_mask = active_mask;
        Ok(active_mask != 0 || report_mask != 0)
    }

'''

touch = regex_once(
    touch,
    r"    async fn poll_once\(&mut self\) \{.*?\n    \}\n\n    async fn init\(&mut self\) -> bool \{",
    new_poll + "    async fn init(&mut self) -> bool {",
    "replace touchpad poll path",
)
touch_path.write_text(touch)

# ---------------------------------------------------------------------------
# HID: add an 8-byte hybrid PTP input report and append Microsoft's required
# Touch Pad + Configuration collections to the BLE report map.
# ---------------------------------------------------------------------------
hid_path = Path("rmk/src/hid.rs")
hid = hid_path.read_text()

ptp_hid = r'''
/// One contact in Windows Precision Touchpad hybrid-reporting mode.
/// Report ID lives in the BLE Report Reference descriptor, not this payload.
#[derive(Debug, Clone, Copy, Default)]
pub struct PrecisionTouchpadReport {
    pub flags_id: u8,
    pub x: u16,
    pub y: u16,
    pub scan_time: u16,
    pub contact_count: u8,
}

impl AsInputReport for PrecisionTouchpadReport {
    fn serialize(&self, buffer: &mut [u8]) -> Result<usize, usbd_hid::descriptor::BufferOverflow> {
        if buffer.len() < 8 {
            return Err(usbd_hid::descriptor::BufferOverflow);
        }
        buffer[0] = self.flags_id;
        buffer[1..3].copy_from_slice(&self.x.to_le_bytes());
        buffer[3..5].copy_from_slice(&self.y.to_le_bytes());
        buffer[5..7].copy_from_slice(&self.scan_time.to_le_bytes());
        buffer[7] = self.contact_count;
        Ok(8)
    }
}

#[cfg(all(feature = "_ble", feature = "host"))]
pub(crate) const PRECISION_TOUCHPAD_REPORT_MAP: [u8; 215] = [
    // Digitizers / Touch Pad TLC, hybrid one-contact input report (ID 6).
    0x05,0x0d,0x09,0x05,0xa1,0x01,0x85,0x06,
    0x09,0x22,0xa1,0x02,0x15,0x00,0x25,0x01,0x09,0x47,0x09,0x42,
    0x95,0x02,0x75,0x01,0x81,0x02,
    0x25,0x07,0x09,0x51,0x75,0x03,0x95,0x01,0x81,0x02,
    0x75,0x03,0x95,0x01,0x81,0x03,
    // TPS43 X: 0..2047 over 43 mm (1.69 in).
    0x05,0x01,0x15,0x00,0x26,0xff,0x07,0x75,0x10,0x55,0x0e,0x65,0x13,
    0x09,0x30,0x35,0x00,0x46,0xa9,0x00,0x95,0x01,0x81,0x02,
    // TPS43 Y: 0..1791 over 40 mm (1.57 in).
    0x26,0xff,0x06,0x09,0x31,0x46,0x9d,0x00,0x81,0x02,0xc0,
    // Frame-level scan time (100 us units) and contact count.
    0x55,0x0c,0x66,0x01,0x10,0x47,0xff,0xff,0x00,0x00,
    0x27,0xff,0xff,0x00,0x00,0x75,0x10,0x95,0x01,0x05,0x0d,0x09,0x56,0x81,0x02,
    0x09,0x54,0x25,0x7f,0x95,0x01,0x75,0x08,0x81,0x02,
    // Device capabilities (ID 7): max contacts + pad type.
    0x85,0x07,0x09,0x55,0x09,0x59,0x75,0x04,0x95,0x02,0x25,0x0f,0xb1,0x02,
    // Certification status blob (ID 8), required even when unsigned on Win10+.
    0x06,0x00,0xff,0x85,0x08,0x09,0xc5,0x15,0x00,0x26,0xff,0x00,
    0x75,0x08,0x96,0x00,0x01,0xb1,0x02,
    // Optional/recommended latency mode (ID 11).
    0x05,0x0d,0x85,0x0b,0x09,0x60,0x75,0x01,0x95,0x01,0x15,0x00,0x25,0x01,
    0xb1,0x02,0x95,0x07,0xb1,0x03,0xc0,
    // Digitizers / Configuration TLC.
    0x05,0x0d,0x09,0x0e,0xa1,0x01,
    // Input Mode (ID 9): Windows writes 3 for PTP.
    0x85,0x09,0x09,0x22,0xa1,0x02,0x09,0x52,0x15,0x00,0x25,0x0a,
    0x75,0x08,0x95,0x01,0xb1,0x02,0xc0,
    // Selective reporting (ID 10): surface + button switches.
    0x09,0x22,0xa1,0x00,0x85,0x0a,0x09,0x57,0x09,0x58,0x75,0x01,0x95,0x02,
    0x25,0x01,0xb1,0x02,0x95,0x06,0xb1,0x03,0xc0,0xc0,
];

'''

hid = replace_once(
    hid,
    "#[derive(Debug, Clone)]\npub enum Report {\n",
    ptp_hid + "#[derive(Debug, Clone)]\npub enum Report {\n",
    "insert PTP HID types",
)
hid = replace_once(
    hid,
    "    SystemControlReport(SystemControlReport),\n",
    "    SystemControlReport(SystemControlReport),\n    /// Windows Precision Touchpad contact report (BLE host path).\n    PrecisionTouchpadReport(PrecisionTouchpadReport),\n",
    "PTP report enum variant",
)
hid = replace_once(
    hid,
    "            Report::SystemControlReport(r) => r.serialize(buffer),\n",
    "            Report::SystemControlReport(r) => r.serialize(buffer),\n            Report::PrecisionTouchpadReport(r) => r.serialize(buffer),\n",
    "PTP report serialization",
)
hid = replace_once(
    hid,
    "pub(crate) const BLE_REPORT_MAP_LEN: usize = 207;",
    "pub(crate) const BLE_REPORT_MAP_LEN: usize = 422;",
    "BLE report map length",
)
hid = replace_once(
    hid,
    "    let vial = BleViaReport::desc();\n    assert_eq!(composite.len() + vial.len(), BLE_REPORT_MAP_LEN);\n\n    let mut report_map = [0u8; BLE_REPORT_MAP_LEN];\n    report_map[..vial.len()].copy_from_slice(vial);\n    report_map[vial.len()..].copy_from_slice(composite);\n",
    "    let vial = BleViaReport::desc();\n    let ptp = &PRECISION_TOUCHPAD_REPORT_MAP;\n    assert_eq!(composite.len() + vial.len() + ptp.len(), BLE_REPORT_MAP_LEN);\n\n    let mut report_map = [0u8; BLE_REPORT_MAP_LEN];\n    report_map[..vial.len()].copy_from_slice(vial);\n    let composite_end = vial.len() + composite.len();\n    report_map[vial.len()..composite_end].copy_from_slice(composite);\n    report_map[composite_end..].copy_from_slice(ptp);\n",
    "append PTP BLE report map",
)
hid_path.write_text(hid)

# ---------------------------------------------------------------------------
# BLE HOGP service: report characteristics for PTP input + required features.
# ---------------------------------------------------------------------------
ble_path = Path("rmk/src/ble/ble_server.rs")
ble = ble_path.read_text()

ble = replace_once(
    ble,
    "    #[descriptor(uuid = \"2908\", read, value = [CompositeReportType::Vial as u8, 2u8])]\n    #[characteristic(uuid = \"2a4d\", read, write, write_without_response)]\n    pub(crate) vial_output: [u8; 32],\n",
    "    #[descriptor(uuid = \"2908\", read, value = [CompositeReportType::Vial as u8, 2u8])]\n    #[characteristic(uuid = \"2a4d\", read, write, write_without_response)]\n    pub(crate) vial_output: [u8; 32],\n"
    "    #[descriptor(uuid = \"2908\", read, value = [6u8, 1u8])]\n"
    "    #[characteristic(uuid = \"2a4d\", read, notify)]\n"
    "    pub(crate) precision_touchpad_input: [u8; 8],\n"
    "    #[descriptor(uuid = \"2908\", read, value = [7u8, 3u8])]\n"
    "    #[characteristic(uuid = \"2a4d\", read, value = [0x25u8])]\n"
    "    pub(crate) precision_touchpad_caps: [u8; 1],\n"
    "    #[descriptor(uuid = \"2908\", read, value = [8u8, 3u8])]\n"
    "    #[characteristic(uuid = \"2a4d\", read, value = [0u8; 256])]\n"
    "    pub(crate) precision_touchpad_cert: [u8; 256],\n"
    "    #[descriptor(uuid = \"2908\", read, value = [9u8, 3u8])]\n"
    "    #[characteristic(uuid = \"2a4d\", read, write, value = [0u8])]\n"
    "    pub(crate) precision_touchpad_input_mode: [u8; 1],\n"
    "    #[descriptor(uuid = \"2908\", read, value = [10u8, 3u8])]\n"
    "    #[characteristic(uuid = \"2a4d\", read, write, value = [0x03u8])]\n"
    "    pub(crate) precision_touchpad_selective: [u8; 1],\n"
    "    #[descriptor(uuid = \"2908\", read, value = [11u8, 3u8])]\n"
    "    #[characteristic(uuid = \"2a4d\", read, write, value = [0u8])]\n"
    "    pub(crate) precision_touchpad_latency: [u8; 1],\n",
    "PTP GATT characteristics",
)

ble = replace_once(
    ble,
    "    system_report: Characteristic<[u8; 1]>,\n",
    "    system_report: Characteristic<[u8; 1]>,\n    #[cfg(feature = \"host\")]\n    precision_touchpad_input: Characteristic<[u8; 8]>,\n",
    "PTP BLE writer field",
)
ble = replace_once(
    ble,
    "            system_report: server.hid_service.system_report,\n",
    "            system_report: server.hid_service.system_report,\n            #[cfg(feature = \"host\")]\n            precision_touchpad_input: server.hid_service.precision_touchpad_input,\n",
    "PTP BLE writer init",
)
ble = replace_once(
    ble,
    "            Report::SystemControlReport(r) => self.notify_report(self.system_report, r).await,\n",
    "            Report::SystemControlReport(r) => self.notify_report(self.system_report, r).await,\n"
    "            #[cfg(feature = \"host\")]\n"
    "            Report::PrecisionTouchpadReport(r) => self.notify_report(self.precision_touchpad_input, r).await,\n"
    "            #[cfg(not(feature = \"host\"))]\n"
    "            Report::PrecisionTouchpadReport(_) => Ok(0),\n",
    "PTP BLE writer match",
)
ble_path.write_text(ble)

# USB is deliberately unchanged as a device interface. If a PTP report is ever
# routed there (for example while flashing over a data cable), drop only that
# report rather than extending the USB descriptor in this experiment.
usb_path = Path("rmk/src/usb/mod.rs")
usb = usb_path.read_text()
usb = replace_once(
    usb,
    "            Report::SystemControlReport(r) => self.write_composite(CompositeReportType::System, r).await,\n",
    "            Report::SystemControlReport(r) => self.write_composite(CompositeReportType::System, r).await,\n            Report::PrecisionTouchpadReport(_) => Ok(0),\n",
    "drop PTP reports on USB",
)
usb_path.write_text(usb)

# ---------------------------------------------------------------------------
# Central Qube processor: consume the absolute contact transport before legacy
# cursor/scroll processing, convert metadata to the 8-byte PTP HID input report.
# ---------------------------------------------------------------------------
pointing_path = Path("rmk/src/input_device/pointing.rs")
pointing = pointing_path.read_text()
pointing = replace_once(
    pointing,
    "use crate::hid::{KeyboardReport, Report};",
    "use crate::hid::{KeyboardReport, PrecisionTouchpadReport, Report};",
    "PTP report import",
)
pointing = replace_once(
    pointing,
    "    last_auto_motion_ms: u32,\n",
    "    last_auto_motion_ms: u32,\n    ptp_scan_time: u16,\n    ptp_last_frame: Instant,\n",
    "PTP central state",
)
pointing = replace_once(
    pointing,
    "            last_auto_motion_ms: 0,\n",
    "            last_auto_motion_ms: 0,\n            ptp_scan_time: 0,\n            ptp_last_frame: Instant::MIN,\n",
    "PTP central state init",
)
needle = """        #[cfg(feature = \"_ble\")]
        crate::ble::sleep::report_pointing_activity(&event);

        let mut x = 0i16;
"""
insert = """        #[cfg(feature = \"_ble\")]
        crate::ble::sleep::report_pointing_activity(&event);

        // Experimental PTP transport: K:04 touchpads publish absolute X/Y/Z
        // events. Consume them here so the legacy Qube mouse path never sees
        // absolute coordinates as relative cursor motion.
        if source.kind == QubePointingKind::Touch
            && event.axes.iter().any(|axis| axis.typ == AxisValType::Abs)
        {
            let mut x = 0u16;
            let mut y = 0u16;
            let mut meta = 0u8;
            for axis_event in event.axes.iter() {
                if axis_event.typ != AxisValType::Abs {
                    continue;
                }
                match axis_event.axis {
                    Axis::X => x = axis_event.value.max(0) as u16,
                    Axis::Y => y = axis_event.value.max(0) as u16,
                    Axis::Z => meta = axis_event.value as u8,
                    _ => {}
                }
            }
            let contact_id = meta & 0x07;
            let tip = (meta & (1 << 3)) != 0;
            let confidence = (meta & (1 << 4)) != 0;
            let contact_count = (meta >> 5) & 0x07;
            if contact_count != 0 {
                let now = Instant::now();
                let elapsed_100us = if self.ptp_last_frame == Instant::MIN {
                    80u64
                } else {
                    now.duration_since(self.ptp_last_frame).as_micros() / 100
                };
                self.ptp_scan_time = self.ptp_scan_time.wrapping_add(elapsed_100us.min(u16::MAX as u64) as u16);
                self.ptp_last_frame = now;
            }
            let flags_id = (confidence as u8) | ((tip as u8) << 1) | (contact_id << 2);
            send_hid_report(Report::PrecisionTouchpadReport(PrecisionTouchpadReport {
                flags_id,
                x: x.min(2047),
                y: y.min(1791),
                scan_time: self.ptp_scan_time,
                contact_count,
            }))
            .await;
            return;
        }

        let mut x = 0i16;
"""
pointing = replace_once(pointing, needle, insert, "PTP absolute event consumer")
pointing_path.write_text(pointing)

print("K:04 experimental Windows Precision Touchpad patch applied")
