"""
Queries the CodePulse knowledge graph for every method that depends on the
active business-rule method (auto-detected), using a reverse BFS.
"""
from pathlib import Path
import json

from scanner import detect_current_process
from codepulse_graph.paths import load_node_link_graph
from codepulse_graph.affected import affected_nodes, resolve_seed

project   = Path(__file__).parent.resolve()
graph_file = project / "codepulse-out" / "graph.json"

proc = detect_current_process(project)
if proc is None:
    print("No changeable business-rule method found.")
    raise SystemExit(1)

changed_method = proc["method"]
print("Detected process:", changed_method)
print("\n=== Dependency Impact ===")

raw  = json.loads(graph_file.read_text(encoding="utf-8"))
G    = load_node_link_graph(raw)
seed = resolve_seed(G, f".{changed_method}()")

if seed is None:
    print(f"Could not find '{changed_method}' in the knowledge graph.")
    raise SystemExit(1)

hits = affected_nodes(G, seed, depth=2)
print(f"Affected nodes for .{changed_method}()")
print("Depth: 2")
for h in hits:
    node_data = G.nodes.get(h.node_id, {})
    label     = node_data.get("label", h.node_id)
    location  = f"{h.via_file}:{h.via_location}" if h.via_file else ""
    print(f"- {label} [{h.via_relation}] {location}")
