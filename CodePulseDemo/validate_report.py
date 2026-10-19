from pathlib import Path
import json

report_path = (
    Path(r"F:\PythonProjects\CodePulseDemo")
    / "evidence" / "impact-report.json"
)

report = json.loads(report_path.read_text(encoding="utf-8"))

print("=== CodePulse AI Report Validation ===")

required_sections = [
    "file",
    "method",
    "changeType",
    "diff",
    "oldCondition",
    "newCondition",
    "behaviorChangesForSampleAmounts",
    "dependencyAnalysis",
    "recommendedTests"
]

all_present = True

for section in required_sections:
    present = section in report
    print(f"{section}: {'OK' if present else 'MISSING'}")
    all_present = all_present and present

print("\nRecommended test count:", len(report.get("recommendedTests", [])))
print("Validation:", "PASSED" if all_present else "FAILED")
