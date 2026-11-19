"""
Auto-detects every changeable business-rule method in the CodePulse Java project.

A "changeable process" is any method whose body contains a numeric return
condition of the form:

        return <receiver>.<getter>() <op> <number>;

e.g.  return order.getAmount() >= 100;     // processPayment
      return order.getQuantity() > 0;      // hasStock

This module is the single source of truth used by the Flask app and by the
pipeline scripts, so the system is not hardcoded to any one method.
"""
from __future__ import annotations

import re
from pathlib import Path

# return <receiver>.<getter>() <op> <num> ;
COND_RE = re.compile(
    r"return\s+(\w+)\.(\w+)\(\)\s*(>=|>|<=|<|==|!=)\s*(\d+(?:\.\d+)?)\s*;"
)
# method declaration: public/private/protected <type> <name>(...) {
METHOD_RE = re.compile(
    r"\b(?:public|private|protected)\s+[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{"
)

# friendly aliases a user might type instead of the raw field name
ALIASES = {
    "amount":   "amount",
    "payment":  "amount",
    "pay":      "amount",
    "quantity": "quantity",
    "qty":      "quantity",
    "stock":    "quantity",
    "inventory":"quantity",
}


def _field_name(getter: str) -> str:
    """getAmount -> amount, getQuantity -> quantity."""
    name = getter[3:] if getter.startswith("get") else getter
    return name[:1].lower() + name[1:] if name else name


def scan_processes(project_dir: Path) -> list[dict]:
    """Return every changeable process found under <project_dir>/src."""
    project_dir = Path(project_dir)
    src = project_dir / "src"
    processes: list[dict] = []
    if not src.exists():
        return processes

    for f in sorted(src.rglob("*.java")):
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines()

        # map line-number -> method name for every declared method
        method_at: dict[int, str] = {}
        for i, line in enumerate(lines, start=1):
            m = METHOD_RE.search(line)
            if m:
                method_at[i] = m.group(1)

        for m in COND_RE.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            # method that owns this line = the last declaration above it
            owner = None
            for ml, name in method_at.items():
                if ml < line_no:
                    owner = name
                else:
                    break
            receiver, getter = m.group(1), m.group(2)
            processes.append({
                "file":      str(f.relative_to(project_dir)),
                "absPath":   str(f),
                "method":    owner,
                "receiver":  receiver,
                "getter":    getter,
                "field":     _field_name(getter),
                "operator":  m.group(3),
                "value":     float(m.group(4)),
                "line":      line_no,
            })
    return processes


def baseline_path(project_dir: Path, file: str) -> Path:
    """evidence/<ClassName>.before.java for a given source file."""
    project_dir = Path(project_dir)
    return project_dir / "evidence" / (Path(file).stem + ".before.java")


def ensure_baselines(project_dir: Path) -> None:
    """Create a baseline snapshot for any changeable file that lacks one.

    The first time a file is seen, its current content IS the baseline, so the
    snapshot must be taken before any prompt changes the file.
    """
    project_dir = Path(project_dir)
    for p in scan_processes(project_dir):
        bp = baseline_path(project_dir, p["file"])
        if not bp.exists():
            bp.parent.mkdir(parents=True, exist_ok=True)
            bp.write_text(Path(p["absPath"]).read_text(encoding="utf-8"), encoding="utf-8")


def read_condition(source: str, getter: str):
    """Return (op, float_value) for `return <receiver>.<getter>() op val;`."""
    pat = re.compile(
        r"return\s+\w+\." + re.escape(getter) + r"\(\)\s*"
        r"(>=|>|<=|<|==|!=)\s*(\d+(?:\.\d+)?)\s*;"
    )
    m = pat.search(source)
    if not m:
        return None, None
    return m.group(1), float(m.group(2))


def rewrite_condition(source: str, receiver: str, getter: str, new_op: str, new_val: float) -> str:
    """Replace the condition for a specific receiver/getter with a new op/value."""
    pat = re.compile(
        r"(return\s+" + re.escape(receiver) + r"\." + re.escape(getter) + r"\(\)\s*)"
        r"(>=|>|<=|<|==|!=)\s*\d+(?:\.\d+)?\s*;"
    )
    return pat.sub(lambda m: f"{m.group(1)}{new_op} {new_val:g};", source, count=1)


def detect_current_process(project_dir: Path) -> dict | None:
    """
    The process the analysis pages should currently reflect.

    Rule: among processes whose current condition differs from their baseline,
    the most recently modified source file wins. If nothing has changed yet,
    default to processPayment (the primary demo), else the first process.
    """
    project_dir = Path(project_dir)
    procs = scan_processes(project_dir)
    if not procs:
        return None

    changed = []
    for p in procs:
        bp = baseline_path(project_dir, p["file"])
        if not bp.exists():
            continue
        b_op, b_val = read_condition(bp.read_text(encoding="utf-8"), p["getter"])
        if b_op is None:
            continue
        if (b_op, b_val) != (p["operator"], p["value"]):
            changed.append(p)

    if changed:
        changed.sort(key=lambda p: Path(p["absPath"]).stat().st_mtime, reverse=True)
        return changed[0]

    # nothing changed -> default to processPayment if present
    for p in procs:
        if p["method"] == "processPayment":
            return p
    return procs[0]


def resolve_process(prompt_field: str | None, project_dir: Path):
    """Match a prompt's field/method token to a detected process."""
    procs = scan_processes(project_dir)
    if not procs:
        return None, "No changeable processes found in the project."
    if not prompt_field:
        return detect_current_process(project_dir), None

    key = ALIASES.get(prompt_field.lower(), prompt_field.lower())
    # match by field name
    for p in procs:
        if p["field"].lower() == key:
            return p, None
    # match by method name
    for p in procs:
        if p["method"].lower() == key:
            return p, None
    fields = ", ".join(sorted({p["field"] for p in procs}))
    return None, f"Unknown process '{prompt_field}'. Detected: {fields}"


# ── shared condition helpers ────────────────────────────────────────────────

def evaluate(amount, operator, threshold):
    return {
        ">":  amount >  threshold,
        ">=": amount >= threshold,
        "<":  amount <  threshold,
        "<=": amount <= threshold,
        "==": amount == threshold,
        "!=": amount != threshold,
    }[operator]


def sample_amounts(old_val, new_val) -> list[int]:
    """Build a tailored set of sample inputs around both thresholds."""
    vals = {0, 1}
    for t in (old_val, new_val):
        if t and t > 0:
            vals |= {int(t) - 1, int(t), int(t) + 1}
    return sorted(vals)


def parse_condition(cond_str: str):
    """'order.getAmount() >= 100' -> ('>=', 100.0). Returns (None, None) if no match."""
    m = re.search(r"(>=|>|<=|<|==|!=)\s*(\d+(?:\.\d+)?)", cond_str or "")
    if not m:
        return None, None
    return m.group(1), float(m.group(2))


def condition_string(receiver: str, getter: str, op: str, val: float) -> str:
    return f"{receiver}.{getter}() {op} {val:g}"


def behavior_rows(receiver, getter, old_op, old_val, new_op, new_val):
    rows = []
    for amount in sample_amounts(old_val, new_val):
        b = evaluate(amount, old_op, old_val)
        a = evaluate(amount, new_op, new_val)
        rows.append({"amount": amount, "before": b, "after": a, "changed": b != a})
    return rows


def recommend_tests(old_op, old_val, new_op, new_val):
    """Boundary-aware test cases around the new threshold."""
    threshold = int(new_val)
    candidates = sorted({0, threshold - 1, threshold, threshold + 1})
    tests = []
    for amount in candidates:
        if amount < 0:
            continue
        eb = evaluate(amount, old_op, old_val)
        ea = evaluate(amount, new_op, new_val)
        if amount == 0:
            purpose = "Verify a zero value"
        elif amount == threshold - 1:
            purpose = f"Verify value just below threshold ({amount}) is rejected"
        elif amount == threshold:
            purpose = f"Verify exact threshold ({amount}) is accepted"
        else:
            purpose = f"Verify value above threshold ({amount}) is accepted"
        tests.append({"amount": amount, "expectedBefore": eb, "expectedAfter": ea, "purpose": purpose})
    return tests
