#!/usr/bin/env python3
from pathlib import Path

path = Path("rmk/src/input_device/pointing.rs")
text = path.read_text()

old_any = "event.axes.iter().any(|axis| axis.typ == AxisValType::Abs)"
new_any = "event.axes.iter().any(|axis| matches!(axis.typ, AxisValType::Abs))"
old_if = "if axis_event.typ != AxisValType::Abs {"
new_if = "if !matches!(axis_event.typ, AxisValType::Abs) {"

if text.count(old_any) != 1:
    raise SystemExit(f"PTP AxisValType any(): expected 1 match, found {text.count(old_any)}")
if text.count(old_if) != 1:
    raise SystemExit(f"PTP AxisValType filter: expected 1 match, found {text.count(old_if)}")

text = text.replace(old_any, new_any, 1).replace(old_if, new_if, 1)
path.write_text(text)
print("K:04 PTP AxisValType match fix applied")
