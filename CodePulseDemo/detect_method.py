"""
Detects which Java method contains the change, by diffing the current source
file of the active business-rule process against its baseline snapshot.
"""
from pathlib import Path
import difflib
import re

from scanner import detect_current_process, baseline_path

project = Path(__file__).parent.resolve()
proc = detect_current_process(project)
if proc is None:
    print("No changeable business-rule method found.")
    raise SystemExit(1)

bp = baseline_path(project, proc["file"])
before = bp.read_text(encoding="utf-8").splitlines() if bp.exists() else []
after  = Path(proc["absPath"]).read_text(encoding="utf-8").splitlines()

matcher = difflib.SequenceMatcher(a=before, b=after)
changed_line_numbers = []
for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    if tag in ("replace", "insert"):
        changed_line_numbers.extend(range(j1 + 1, j2 + 1))

method_pattern = re.compile(
    r"\b(?:public|private|protected)\s+[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{"
)
methods = []
for number, line in enumerate(after, start=1):
    m = method_pattern.search(line)
    if m:
        methods.append((number, m.group(1)))

changed_method = None
if changed_line_numbers:
    first_changed_line = min(changed_line_numbers)
    candidates = [(n, name) for n, name in methods if n < first_changed_line]
    if candidates:
        changed_method = max(candidates, key=lambda item: item[0])[1]

print("Active process file:", proc["file"])
print("Changed line numbers:", changed_line_numbers)
print("Detected method:", changed_method or "Not identified (no change from baseline)")
print("Scanner-detected method:", proc["method"])
