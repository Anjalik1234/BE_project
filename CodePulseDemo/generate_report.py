from pathlib import Path
import difflib
import json
import re
import subprocess

project = Path(r"F:\PythonProjects\CodePulseDemo")

before_file = project / "evidence" / "PaymentService.before.java"
after_file = (
    project / "src" / "main" / "java" / "com" / "codepulse"
    / "demo" / "service" / "PaymentService.java"
)
graph = project / "graphify-out" / "graph.json"

before = before_file.read_text(encoding="utf-8")
after = after_file.read_text(encoding="utf-8")

diff = list(difflib.unified_diff(
    before.splitlines(),
    after.splitlines(),
    fromfile="before",
    tofile="after",
    lineterm=""
))

pattern = re.compile(
    r"return\s+order\.getAmount\(\)\s*(>=|>|<=|<|==|!=)\s*(\d+(?:\.\d+)?)\s*;"
)

old_match = pattern.search(before)
new_match = pattern.search(after)

if not old_match or not new_match:
    raise SystemExit("Could not recognize both payment conditions.")

old_operator, old_value = old_match.groups()
new_operator, new_value = new_match.groups()
old_value, new_value = float(old_value), float(new_value)

def evaluate(amount, operator, threshold):
    if operator == ">": return amount > threshold
    if operator == ">=": return amount >= threshold
    if operator == "<": return amount < threshold
    if operator == "<=": return amount <= threshold
    if operator == "==": return amount == threshold
    if operator == "!=": return amount != threshold
    raise ValueError(operator)

sample_amounts = [0, 1, 50, 99, 100, 101, 150]
behavior_changes = []

for amount in sample_amounts:
    old_result = evaluate(amount, old_operator, old_value)
    new_result = evaluate(amount, new_operator, new_value)

    if old_result != new_result:
        behavior_changes.append({
            "amount": amount,
            "before": old_result,
            "after": new_result
        })

impact = subprocess.run(
    [
        "uv", "run", "graphify", "affected",
        ".processPayment()",
        "--depth", "2",
        "--graph", str(graph)
    ],
    cwd=r"F:\PythonProjects\graphify",
    capture_output=True,
    text=True
)

if impact.returncode != 0:
    raise SystemExit(impact.stderr or impact.stdout)

report = {
    "file": str(after_file.relative_to(project)),
    "method": "processPayment",
    "changeType": "BUSINESS_RULE_CHANGE",
    "diff": diff,
    "oldCondition": f"order.getAmount() {old_operator} {old_value:g}",
    "newCondition": f"order.getAmount() {new_operator} {new_value:g}",
    "behaviorChangesForSampleAmounts": behavior_changes,
    "dependencyAnalysis": impact.stdout.strip(),
    "limitations": [
        "Method name is currently configured for this controlled experiment.",
        "Behavioral comparison uses selected sample inputs.",
        "Dependency results come from Graphify and do not prove runtime effects."
    ]
}

output = project / "evidence" / "impact-report.json"
output.write_text(json.dumps(report, indent=2), encoding="utf-8")

print("Impact report saved to:", output)
print(json.dumps(report, indent=2))
