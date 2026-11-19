"""
Appends boundary-aware test recommendations to the impact report, generated
dynamically from the report's old/new conditions (not hardcoded values).
"""
from pathlib import Path
import json

from scanner import parse_condition, recommend_tests

project     = Path(__file__).parent.resolve()
report_file = project / "evidence" / "impact-report.json"

report = json.loads(report_file.read_text(encoding="utf-8"))

old_op, old_val = parse_condition(report.get("oldCondition", ""))
new_op, new_val = parse_condition(report.get("newCondition", ""))

if old_op is None or new_op is None:
    # fall back to structured fields if present
    old_op, old_val = report.get("oldOperator"), report.get("oldValue")
    new_op, new_val = report.get("newOperator"), report.get("newValue")

if None in (old_op, new_op, old_val, new_val):
    raise SystemExit("Could not read conditions from the report.")

report["recommendedTests"] = recommend_tests(old_op, old_val, new_op, new_val)

report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

print("Test recommendations added:")
for test in report["recommendedTests"]:
    print(
        f"Value: {test['amount']} | "
        f"Before: {test['expectedBefore']} | "
        f"After: {test['expectedAfter']} | "
        f"{test['purpose']}"
    )
print("\nUpdated report:", report_file)
