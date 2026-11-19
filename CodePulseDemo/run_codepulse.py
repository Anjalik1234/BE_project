from pathlib import Path
import subprocess
import sys

project = Path(__file__).parent.resolve()

steps = [
    ("Detect changed method", "detect_method.py"),
    ("Analyze dependencies", "analyze_impact.py"),
    ("Compare behavior", "analyze_behavior.py"),
    ("Generate impact report", "generate_report.py"),
    ("Recommend tests", "recommend_tests.py"),
    ("Validate report", "validate_report.py"),
]

for label, script in steps:
    print(f"\n{'=' * 12} {label} {'=' * 12}", flush=True)

    result = subprocess.run(
        [sys.executable, str(project / script)],
        cwd=str(project),
        text=True
    )

    if result.returncode != 0:
        print(f"\nFAILED: {script}")
        raise SystemExit(result.returncode)

print("\nCodePulse AI prototype run completed.")
print("Report:", project / "evidence" / "impact-report.json")
