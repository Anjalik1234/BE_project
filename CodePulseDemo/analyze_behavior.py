"""
Compares the baseline condition vs the current condition of the active
business-rule method across sample inputs, printing where behaviour flips.
"""
from pathlib import Path

from scanner import (
    detect_current_process, baseline_path, read_condition,
    condition_string, evaluate, sample_amounts,
)

project = Path(__file__).parent.resolve()
proc = detect_current_process(project)
if proc is None:
    print("No changeable business-rule method found.")
    raise SystemExit(1)

receiver, getter = proc["receiver"], proc["getter"]
cur_src = Path(proc["absPath"]).read_text(encoding="utf-8")
bp      = baseline_path(project, proc["file"])
base_src = bp.read_text(encoding="utf-8") if bp.exists() else cur_src

old_op, old_val = read_condition(base_src, getter)
new_op, new_val = read_condition(cur_src, getter)

if old_op is None or new_op is None:
    print("Could not recognise the conditions.")
    raise SystemExit(1)

print("Old condition:", condition_string(receiver, getter, old_op, old_val))
print("New condition:", condition_string(receiver, getter, new_op, new_val))
print("\n=== Behavioral Comparison ===")
for amount in sample_amounts(old_val, new_val):
    old_result = evaluate(amount, old_op, old_val)
    new_result = evaluate(amount, new_op, new_val)
    if old_result != new_result:
        print(f"Value {amount}: before={old_result}, after={new_result} <-- behaviour changed")
