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
changed_line_numbers = []

for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    if tag in ("replace", "insert"):
        changed_line_numbers.extend(range(j1 + 1, j2 + 1))

method_pattern = re.compile(
    r"\b(?:public|private|protected)\s+"
    r"[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{"
)

methods = []
for number, line in enumerate(after, start=1):
    match = method_pattern.search(line)
    if match:
        methods.append((number, match.group(1)))

changed_method = None
if changed_line_numbers:
    first_changed_line = min(changed_line_numbers)
    candidates = [
        (number, name)
        for number, name in methods
        if number < first_changed_line
    ]
    if candidates:
        changed_method = max(candidates, key=lambda item: item[0])[1]

print("Changed line numbers:", changed_line_numbers)
print("Detected method:", changed_method or "Not identified")
