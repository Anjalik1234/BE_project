"""
Builds evidence/impact-report.json for whichever business-rule method has
changed in the project (auto-detected, not hardcoded to processPayment).
"""
from pathlib import Path
import difflib
import json

from scanner import (
    detect_current_process, baseline_path, read_condition,
    condition_string, behavior_rows, recommend_tests,
)
from codepulse_graph.paths import load_node_link_graph
from codepulse_graph.affected import affected_nodes, resolve_seed

project   = Path(__file__).parent.resolve()
graph_file = project / "codepulse-out" / "graph.json"

proc = detect_current_process(project)
if proc is None:
    raise SystemExit("No changeable business-rule method found in the project.")

method   = proc["method"]
receiver = proc["receiver"]
getter   = proc["getter"]
cur_src  = Path(proc["absPath"]).read_text(encoding="utf-8")

bp      = baseline_path(project, proc["file"])
base_src = bp.read_text(encoding="utf-8") if bp.exists() else cur_src

old_op, old_val = read_condition(base_src, getter)
new_op, new_val = read_condition(cur_src, getter)

if old_op is None or new_op is None:
    raise SystemExit("Could not read the payment/stock condition.")

diff = list(difflib.unified_diff(
    base_src.splitlines(), cur_src.splitlines(),
    fromfile="before", tofile="after", lineterm="",
))

behavior_changes = [
    {"amount": r["amount"], "before": r["before"], "after": r["after"]}
    for r in behavior_rows(receiver, getter, old_op, old_val, new_op, new_val)
    if r["changed"]
]

# query the knowledge graph for whoever depends on this method
raw  = json.loads(graph_file.read_text(encoding="utf-8"))
G    = load_node_link_graph(raw)
seed = resolve_seed(G, f".{method}()")
dep_lines = [f"Affected nodes for .{method}()", "Depth: 2"]
if seed is not None:
    for h in affected_nodes(G, seed, depth=2):
        node_data = G.nodes.get(h.node_id, {})
        label     = node_data.get("label", h.node_id)
        location  = f"{h.via_file}:{h.via_location}" if h.via_file else ""
        dep_lines.append(f"- {label} [{h.via_relation}] {location}")

report = {
    "file":            proc["file"],
    "method":          method,
    "changeType":      "BUSINESS_RULE_CHANGE",
    "diff":            diff,
    "oldCondition":    condition_string(receiver, getter, old_op, old_val),
    "newCondition":    condition_string(receiver, getter, new_op, new_val),
    "oldOperator":     old_op,
    "oldValue":        old_val,
    "newOperator":     new_op,
    "newValue":        new_val,
    "behaviorChangesForSampleAmounts": behavior_changes,
    "dependencyAnalysis": "\n".join(dep_lines),
    "limitations": [
        "Method name is auto-detected from the changed source file.",
        "Behavioral comparison uses sample inputs around the thresholds.",
        "Dependency results do not prove runtime effects.",
    ]
}

output = project / "evidence" / "impact-report.json"
output.write_text(json.dumps(report, indent=2), encoding="utf-8")

print("Impact report saved to:", output)
print(json.dumps(report, indent=2))
