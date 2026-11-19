from pathlib import Path
import difflib
import json
import re

from codepulse_graph.paths import load_node_link_graph
from codepulse_graph.affected import affected_nodes, resolve_seed

project = Path(__file__).parent.resolve()

before_file = project / "evidence" / "PaymentService.before.java"
after_file  = (
    project / "src" / "main" / "java" / "com" / "codepulse"
    / "demo" / "service" / "PaymentService.java"
)
graph_file  = project / "codepulse-out" / "graph.json"

before = before_file.read_text(encoding="utf-8")
after  = after_file.read_text(encoding="utf-8")

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
    if operator == ">":  return amount >  threshold
    if operator == ">=": return amount >= threshold
    if operator == "<":  return amount <  threshold
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
            "after":  new_result
        })

# Query the CodePulse knowledge graph
raw  = json.loads(graph_file.read_text(encoding="utf-8"))
G    = load_node_link_graph(raw)
seed = resolve_seed(G, ".processPayment()")

if seed is None:
    raise SystemExit("Could not find processPayment in the knowledge graph.")

hits = affected_nodes(G, seed, depth=2)

dep_lines = [f"Affected nodes for .processPayment()", "Depth: 2"]
for h in hits:
    node_data = G.nodes.get(h.node_id, {})
    label     = node_data.get("label", h.node_id)
    location  = f"{h.via_file}:{h.via_location}" if h.via_file else ""
    dep_lines.append(f"- {label} [{h.via_relation}] {location}")

dependency_analysis = "\n".join(dep_lines)

report = {
    "file":        str(after_file.relative_to(project)),
    "method":      "processPayment",
    "changeType":  "BUSINESS_RULE_CHANGE",
    "diff":        diff,
    "oldCondition": f"order.getAmount() {old_operator} {old_value:g}",
    "newCondition": f"order.getAmount() {new_operator} {new_value:g}",
    "behaviorChangesForSampleAmounts": behavior_changes,
    "dependencyAnalysis": dependency_analysis,
    "limitations": [
        "Method name is currently configured for this controlled experiment.",
        "Behavioral comparison uses selected sample inputs.",
        "Dependency results do not prove runtime effects."
    ]
}

output = project / "evidence" / "impact-report.json"
output.write_text(json.dumps(report, indent=2), encoding="utf-8")

print("Impact report saved to:", output)
print(json.dumps(report, indent=2))
