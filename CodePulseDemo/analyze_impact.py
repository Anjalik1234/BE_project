from pathlib import Path
import difflib
import re
import subprocess

project = Path(__file__).parent.resolve()
before_file = project / "evidence" / "PaymentService.before.java"
after_file = (
    project / "src" / "main" / "java" / "com" / "codepulse"
    / "demo" / "service" / "PaymentService.java"
)
graph = project / "graphify-out" / "graph.json"

before = before_file.read_text(encoding="utf-8").splitlines()
after = after_file.read_text(encoding="utf-8").splitlines()

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
candidates = [
    (number, name)
    for number, name in methods
    if number < first_changed
]

if not candidates:
    print("Could not identify the changed method.")
    raise SystemExit(1)

changed_method = max(candidates, key=lambda item: item[0])[1]

print("Detected method:", changed_method)
print("\n=== Dependency Impact ===")

graphify_dir = project.parent / "graphify"
result = subprocess.run(
    [
        "uv", "run", "graphify", "affected",
        f".{changed_method}()",
        "--depth", "2",
        "--graph", str(graph)
    ],
    cwd=str(graphify_dir),
    capture_output=True,
    text=True
)

print(result.stdout)

if result.returncode != 0:
    print(result.stderr)
    raise SystemExit(result.returncode)
