from pathlib import Path
import difflib
import json
import re
import sys

project = Path(__file__).parent.resolve()
before_file = project / "evidence" / "PaymentService.before.java"
after_file = (
    project / "src" / "main" / "java" / "com" / "codepulse"
    / "demo" / "service" / "PaymentService.java"
)
graph_file = project / "codepulse-out" / "graph.json"

before = before_file.read_text(encoding="utf-8").splitlines()
after  = after_file.read_text(encoding="utf-8").splitlines()

matcher = difflib.SequenceMatcher(a=before, b=after)
changed_lines = []

for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    if tag in ("replace", "insert"):
        changed_lines.extend(range(j1 + 1, j2 + 1))

method_pattern = re.compile(
    r"\b(?:public|private|protected)\s+"
    r"[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{"
)

methods = []
for number, line in enumerate(after, start=1):
    match = method_pattern.search(line)
    if match:
        methods.append((number, match.group(1)))

if not changed_lines:
    print("No source changes detected.")
    raise SystemExit(0)

first_changed = min(changed_lines)
candidates = [(n, name) for n, name in methods if n < first_changed]

if not candidates:
    print("Could not identify the changed method.")
    raise SystemExit(1)

changed_method = max(candidates, key=lambda item: item[0])[1]
print("Detected method:", changed_method)
print("\n=== Dependency Impact ===")

# Query the knowledge graph using the codepulse_graph module
from codepulse_graph.paths import load_node_link_graph
from codepulse_graph.affected import affected_nodes, resolve_seed
import networkx as nx

raw   = json.loads(graph_file.read_text(encoding="utf-8"))
G     = load_node_link_graph(raw)
seed  = resolve_seed(G, f".{changed_method}()")

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
