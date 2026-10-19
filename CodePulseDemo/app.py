from pathlib import Path
import json
from flask import Flask, render_template

app = Flask(__name__)

PROJECT_DIR = Path(r"F:\PythonProjects\CodePulseDemo")
REPORT_FILE = PROJECT_DIR / "evidence" / "impact-report.json"


@app.route("/")
def dashboard():
    if not REPORT_FILE.exists():
        return "Impact report not found. Run CodePulse AI analysis first.", 404

    report = json.loads(REPORT_FILE.read_text(encoding="utf-8"))

    dependency_output = report.get("dependencyAnalysis", "")
    affected_lines = []

    for line in dependency_output.splitlines():
        if line.startswith("- "):
            affected_lines.append(line[2:])

    return render_template(
        "index.html",
        report=report,
        affected_lines=affected_lines
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
