from pathlib import Path
import json

project = Path(__file__).parent.resolve()
report_file = project / "evidence" / "impact-report.json"

report = json.loads(report_file.read_text(encoding="utf-8"))

# Test cases for the payment threshold change in our demo.
test_cases = [
    {
        "amount": 0,
        "expectedBefore": False,
        "expectedAfter": False,
        "purpose": "Verify zero-value payments remain rejected"
    },
    {
        "amount": 99,
        "expectedBefore": True,
        "expectedAfter": False,
        "purpose": "Verify an amount below the new threshold is rejected"
    },
    {
        "amount": 100,
        "expectedBefore": True,
        "expectedAfter": True,
        "purpose": "Verify the exact threshold is accepted"
    },
    {
        "amount": 101,
        "expectedBefore": True,
        "expectedAfter": True,
        "purpose": "Verify an amount above the threshold is accepted"
    }
]

report["recommendedTests"] = test_cases

report_file.write_text(
    json.dumps(report, indent=2),
    encoding="utf-8"
)

print("Test recommendations added:")
for test in report["recommendedTests"]:
    print(
        f'Amount: {test["amount"]} | '
        f'Before: {test["expectedBefore"]} | '
        f'After: {test["expectedAfter"]} | '
        f'{test["purpose"]}'
    )

print("\nUpdated report:", report_file)
