from pathlib import Path
import difflib
import re

project = Path(r"F:\PythonProjects\CodePulseDemo")

before_file = project / "evidence" / "PaymentService.before.java"
after_file = (
    project / "src" / "main" / "java" / "com" / "codepulse"
    / "demo" / "service" / "PaymentService.java"
)

before = before_file.read_text(encoding="utf-8").splitlines()
after = after_file.read_text(encoding="utf-8").splitlines()

matcher = difflib.SequenceMatcher(a=before, b=after)
changed_lines = []

for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    if tag in ("replace", "delete"):
        changed_lines.extend(before[i1:i2])
    if tag in ("replace", "insert"):
        changed_lines.extend(after[j1:j2])

print("=== Changed Lines ===")
for line in changed_lines:
    print(line)

method_pattern = re.compile(
    r"\b(?:public|private|protected)\s+"
    r"[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{"
)

methods = []
for line in after:
    match = method_pattern.search(line)
    if match:
        methods.append(match.group(1))

changed_method = None
for line in changed_lines:
    if "return order.getAmount()" in line:
        changed_method = "processPayment"
        break

print("\n=== Change Summary ===")
print("Changed method:", changed_method or "Not identified")
print("Methods found in file:", ", ".join(methods) or "None")
