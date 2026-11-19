from pathlib import Path
import json
import subprocess
import sys
import difflib
import re
import time

from flask import Flask, render_template, jsonify, request

app = Flask(__name__)


# ── prevent the browser from caching API responses ─────────────────────────
# Without this, the analysis pages show stale data after a change is applied
# because the browser reuses the cached JSON from the previous request.
@app.after_request
def _no_cache(resp):
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

# ── paths (resolved relative to this file — no machine-specific strings) ────
PROJECT_DIR  = Path(__file__).parent.resolve()
EVIDENCE_DIR = PROJECT_DIR / "evidence"
REPORT_FILE  = EVIDENCE_DIR / "impact-report.json"
BEFORE_FILE  = EVIDENCE_DIR / "PaymentService.before.java"
AFTER_FILE   = (
    PROJECT_DIR / "src" / "main" / "java" / "com"
    / "codepulse" / "demo" / "service" / "PaymentService.java"
)
GRAPH_FILE   = PROJECT_DIR / "codepulse-out" / "graph.json"
HISTORY_FILE = EVIDENCE_DIR / "history.json"

# ── condition regex ──────────────────────────────────────────────────────────
_COND_RE = re.compile(
    r"(return\s+order\.getAmount\(\)\s*)"
    r"(>=|>|<=|<|==|!=)"
    r"(\s*\d+(?:\.\d+)?)"
    r"(\s*;)"
)

# ── sample amounts used for behavior comparison ──────────────────────────────
SAMPLE_AMOUNTS = [0, 1, 50, 99, 100, 101, 150, 200, 500]

# ── valid operators ──────────────────────────────────────────────────────────
VALID_OPS = {">=", ">", "<=", "<", "==", "!="}


# ════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ════════════════════════════════════════════════════════════════════════════

def _load_report():
    if not REPORT_FILE.exists():
        return None
    return json.loads(REPORT_FILE.read_text(encoding="utf-8"))


def _parse_affected_lines(dependency_output: str) -> list[str]:
    return [
        line[2:]
        for line in dependency_output.splitlines()
        if line.startswith("- ")
    ]


def _evaluate(amount, operator, threshold):
    return {
        ">":  amount >  threshold,
        ">=": amount >= threshold,
        "<":  amount <  threshold,
        "<=": amount <= threshold,
        "==": amount == threshold,
        "!=": amount != threshold,
    }[operator]


def _read_condition(source: str):
    """Extract (operator, float_value) from a Java payment condition string."""
    m = _COND_RE.search(source)
    if not m:
        return None, None
    return m.group(2), float(m.group(3).strip())


def _behavior_rows(old_op, old_val, new_op, new_val):
    rows = []
    for amount in SAMPLE_AMOUNTS:
        b = _evaluate(amount, old_op, old_val)
        a = _evaluate(amount, new_op, new_val)
        rows.append({"amount": amount, "before": b, "after": a, "changed": b != a})
    return rows


def _recommend_tests(old_op, old_val, new_op, new_val):
    """Generate boundary-aware test cases around the new threshold."""
    threshold = int(new_val)
    candidates = sorted({0, threshold - 1, threshold, threshold + 1})
    tests = []
    for amount in candidates:
        if amount < 0:
            continue
        eb = _evaluate(amount, old_op, old_val)
        ea = _evaluate(amount, new_op, new_val)
        if amount == 0:
            purpose = "Verify zero-value payments"
        elif amount == threshold - 1:
            purpose = f"Verify amount just below threshold ({amount}) is rejected"
        elif amount == threshold:
            purpose = f"Verify exact threshold ({amount}) is accepted"
        else:
            purpose = f"Verify amount above threshold ({amount}) is accepted"
        tests.append({
            "amount": amount,
            "expectedBefore": eb,
            "expectedAfter": ea,
            "purpose": purpose,
        })
    return tests


# ════════════════════════════════════════════════════════════════════════════
# Persistent command history
# ════════════════════════════════════════════════════════════════════════════

def _load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_history(entries):
    HISTORY_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _add_history_entry(entry):
    entries = _load_history()
    entries.append(entry)
    _save_history(entries)
    return entry


def _update_history_entry(entry_id, updates):
    entries = _load_history()
    for e in entries:
        if e.get("id") == entry_id:
            e.update(updates)
            break
    _save_history(entries)


# ════════════════════════════════════════════════════════════════════════════
# Graphify graph query (pure Python, no subprocess)
# ════════════════════════════════════════════════════════════════════════════

def _load_graph():
    """Load graph.json into a networkx DiGraph."""
    if not GRAPH_FILE.exists():
        return None
    try:
        from codepulse_graph.paths import load_node_link_graph
        raw = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
        return load_node_link_graph(raw)
    except Exception:
        return None


def _query_affected(method_name: str, depth: int = 2) -> list[dict]:
    """
    Walk the CodePulse knowledge graph (reverse BFS) to find all methods
    that depend on the given method up to the specified depth.
    """
    try:
        from codepulse_graph.affected import affected_nodes, resolve_seed
        G = _load_graph()
        if G is None:
            return []
        seed = resolve_seed(G, f".{method_name}()")
        if seed is None:
            return []
        hits = affected_nodes(G, seed, depth=depth)
        result = []
        for h in hits:
            node_data = G.nodes.get(h.node_id, {})
            label = node_data.get("label", h.node_id)
            result.append({
                "nodeId":      h.node_id,
                "label":       label,
                "depth":       h.depth,
                "relation":    h.via_relation,
                "file":        h.via_file or node_data.get("source_file", ""),
                "location":    h.via_location or node_data.get("source_location", ""),
            })
        return result
    except Exception as exc:
        return [{"error": str(exc)}]


# ════════════════════════════════════════════════════════════════════════════
# Command parser
# ════════════════════════════════════════════════════════════════════════════

def _parse_command(prompt: str):
    """
    Parse a structured command into an intent dict.

    Supported patterns:
      threshold <N>                      → set_threshold
      change threshold to <N>            → set_threshold
      operator <OP>                      → set_operator
      change operator to <OP>            → set_operator
      condition <OP> <N>                 → set_condition
      change condition to <OP> <N>       → set_condition
      reset                              → reset
      swap                               → swap
    """
    p = prompt.strip().lower()

    # reset
    if re.match(r"^reset$", p):
        return {"intent": "reset"}

    # swap
    if re.match(r"^swap$", p):
        return {"intent": "swap"}

    # threshold <N>  /  change threshold to <N>
    m = re.match(r"^(?:change\s+)?threshold(?:\s+to)?\s+(\d+(?:\.\d+)?)$", p)
    if m:
        return {"intent": "set_threshold", "value": float(m.group(1))}

    # operator <OP>  /  change operator to <OP>
    m = re.match(r"^(?:change\s+)?operator(?:\s+to)?\s+([><=!]{1,2})$", p)
    if m:
        op = m.group(1)
        if op not in VALID_OPS:
            return {"error": f"Unknown operator '{op}'. Valid: {', '.join(sorted(VALID_OPS))}"}
        return {"intent": "set_operator", "operator": op}

    # condition <OP> <N>  /  change condition to <OP> <N>
    m = re.match(
        r"^(?:change\s+)?condition(?:\s+to)?\s+([><=!]{1,2})\s+(\d+(?:\.\d+)?)$", p
    )
    if m:
        op, val = m.group(1), float(m.group(2))
        if op not in VALID_OPS:
            return {"error": f"Unknown operator '{op}'. Valid: {', '.join(sorted(VALID_OPS))}"}
        return {"intent": "set_condition", "operator": op, "value": val}

    return {
        "error": (
            "Command not recognised. Try:\n"
            "  threshold 500\n"
            "  operator >\n"
            "  condition >= 250\n"
            "  reset\n"
            "  swap"
        )
    }


def _apply_intent_to_source(intent: dict, source: str):
    """
    Return the new Java source after applying the intent.
    Does NOT write to disk — pure string transformation.
    """
    cur_op, cur_val = _read_condition(source)
    if cur_op is None:
        return None, "Could not locate payment condition in source file."

    intent_name = intent["intent"]

    if intent_name == "set_threshold":
        new_op, new_val = cur_op, intent["value"]
    elif intent_name == "set_operator":
        new_op, new_val = intent["operator"], cur_val
    elif intent_name == "set_condition":
        new_op, new_val = intent["operator"], intent["value"]
    elif intent_name == "reset":
        if not BEFORE_FILE.exists():
            return None, "Before-file not found."
        return BEFORE_FILE.read_text(encoding="utf-8"), None
    elif intent_name == "swap":
        if not BEFORE_FILE.exists():
            return None, "Before-file not found."
        return BEFORE_FILE.read_text(encoding="utf-8"), None
    else:
        return None, f"Unknown intent: {intent_name}"

    val_str = f"{new_val:g}"
    new_source = _COND_RE.sub(
        lambda mo: f"{mo.group(1)}{new_op} {val_str}{mo.group(4)}",
        source,
    )
    return new_source, None


# ════════════════════════════════════════════════════════════════════════════
# Pages
# ════════════════════════════════════════════════════════════════════════════

@app.route("/")
def dashboard():
    return render_template("index.html")


# ════════════════════════════════════════════════════════════════════════════
# API: project info
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/project-info")
def api_project_info():
    java_files = []
    src_root = PROJECT_DIR / "src"
    if src_root.exists():
        for f in sorted(src_root.rglob("*.java")):
            java_files.append(str(f.relative_to(PROJECT_DIR)))

    before_content = BEFORE_FILE.read_text(encoding="utf-8") if BEFORE_FILE.exists() else ""
    after_content  = AFTER_FILE.read_text(encoding="utf-8")  if AFTER_FILE.exists()  else ""

    return jsonify({
        "projectDir":    str(PROJECT_DIR),
        "beforeFile":    str(BEFORE_FILE.relative_to(PROJECT_DIR)) if BEFORE_FILE.exists() else None,
        "afterFile":     str(AFTER_FILE.relative_to(PROJECT_DIR))  if AFTER_FILE.exists()  else None,
        "graphExists":   GRAPH_FILE.exists(),
        "reportExists":  REPORT_FILE.exists(),
        "javaFiles":     java_files,
        "beforeContent": before_content,
        "afterContent":  after_content,
    })


# ════════════════════════════════════════════════════════════════════════════
# API: report
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/report")
def api_report():
    report = _load_report()
    if report is None:
        return jsonify({"error": "Report not found. Run the pipeline first."}), 404
    report["affectedLines"] = _parse_affected_lines(report.get("dependencyAnalysis", ""))
    return jsonify(report)


# ════════════════════════════════════════════════════════════════════════════
# API: pipeline
# ════════════════════════════════════════════════════════════════════════════

PIPELINE_STEPS = [
    ("detect_method",    "detect_method.py",    "Detect Changed Method"),
    ("analyze_impact",   "analyze_impact.py",   "Analyze Dependencies"),
    ("analyze_behavior", "analyze_behavior.py", "Compare Behavior"),
    ("generate_report",  "generate_report.py",  "Generate Impact Report"),
    ("recommend_tests",  "recommend_tests.py",  "Recommend Tests"),
    ("validate_report",  "validate_report.py",  "Validate Report"),
]


@app.route("/api/pipeline/steps")
def api_pipeline_steps():
    return jsonify([{"id": s[0], "script": s[1], "label": s[2]} for s in PIPELINE_STEPS])


@app.route("/api/pipeline/run", methods=["POST"])
def api_pipeline_run():
    results = []
    for step_id, script, label in PIPELINE_STEPS:
        r = subprocess.run(
            [sys.executable, str(PROJECT_DIR / script)],
            cwd=str(PROJECT_DIR), capture_output=True, text=True,
        )
        results.append({
            "id": step_id, "label": label, "script": script,
            "returncode": r.returncode, "stdout": r.stdout,
            "stderr": r.stderr, "success": r.returncode == 0,
        })
        if r.returncode != 0:
            break

    report = _load_report()
    if report:
        report["affectedLines"] = _parse_affected_lines(report.get("dependencyAnalysis", ""))
    return jsonify({"steps": results, "report": report})


@app.route("/api/pipeline/run-step", methods=["POST"])
def api_pipeline_run_step():
    step_id = (request.json or {}).get("stepId")
    step = next((s for s in PIPELINE_STEPS if s[0] == step_id), None)
    if not step:
        return jsonify({"error": f"Unknown step: {step_id}"}), 400
    r = subprocess.run(
        [sys.executable, str(PROJECT_DIR / step[1])],
        cwd=str(PROJECT_DIR), capture_output=True, text=True,
    )
    return jsonify({
        "id": step[0], "label": step[2], "script": step[1],
        "returncode": r.returncode, "stdout": r.stdout,
        "stderr": r.stderr, "success": r.returncode == 0,
    })


# ════════════════════════════════════════════════════════════════════════════
# API: inline analysis
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/analyze/diff")
def api_analyze_diff():
    if not BEFORE_FILE.exists() or not AFTER_FILE.exists():
        return jsonify({"error": "Source files not found."}), 404
    before = BEFORE_FILE.read_text(encoding="utf-8").splitlines()
    after  = AFTER_FILE.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(
        before, after,
        fromfile=BEFORE_FILE.name, tofile=AFTER_FILE.name, lineterm="",
    ))
    return jsonify({"diff": diff})


@app.route("/api/analyze/method")
def api_analyze_method():
    if not BEFORE_FILE.exists() or not AFTER_FILE.exists():
        return jsonify({"error": "Source files not found."}), 404
    before = BEFORE_FILE.read_text(encoding="utf-8").splitlines()
    after  = AFTER_FILE.read_text(encoding="utf-8").splitlines()
    matcher = difflib.SequenceMatcher(a=before, b=after)
    changed_lines = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            changed_lines.extend(range(j1 + 1, j2 + 1))
    method_pattern = re.compile(
        r"\b(?:public|private|protected)\s+[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{"
    )
    methods = []
    for number, line in enumerate(after, start=1):
        m = method_pattern.search(line)
        if m:
            methods.append({"line": number, "name": m.group(1)})
    changed_method = None
    if changed_lines:
        first_changed = min(changed_lines)
        candidates = [m for m in methods if m["line"] < first_changed]
        if candidates:
            changed_method = max(candidates, key=lambda x: x["line"])["name"]
    return jsonify({"changedLines": changed_lines, "methods": methods, "detectedMethod": changed_method})


@app.route("/api/analyze/behavior")
def api_analyze_behavior():
    if not BEFORE_FILE.exists() or not AFTER_FILE.exists():
        return jsonify({"error": "Source files not found."}), 404
    before = BEFORE_FILE.read_text(encoding="utf-8")
    after  = AFTER_FILE.read_text(encoding="utf-8")
    old_op, old_val = _read_condition(before)
    new_op, new_val = _read_condition(after)
    if old_op is None or new_op is None:
        return jsonify({"error": "Could not parse payment conditions."}), 422
    return jsonify({
        "oldCondition":  f"order.getAmount() {old_op} {old_val:g}",
        "newCondition":  f"order.getAmount() {new_op} {new_val:g}",
        "sampleResults": _behavior_rows(old_op, old_val, new_op, new_val),
    })


# ════════════════════════════════════════════════════════════════════════════
# API: Prompt Lab — PREVIEW
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/prompt/preview", methods=["POST"])
def api_prompt_preview():
    """
    Parse the command, compute all changes in-memory (no files written),
    return a full preview: code diff, behavior changes, affected methods,
    test recommendations.
    """
    prompt = (request.json or {}).get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "No prompt provided."}), 400

    # 1. parse intent
    intent = _parse_command(prompt)
    if "error" in intent:
        return jsonify({"error": intent["error"]}), 422

    # 2. read current source
    if not AFTER_FILE.exists():
        return jsonify({"error": "PaymentService.java not found."}), 404
    current_source = AFTER_FILE.read_text(encoding="utf-8")

    # 3. compute new source (no disk write)
    new_source, err = _apply_intent_to_source(intent, current_source)
    if err:
        return jsonify({"error": err}), 422

    # 4. read conditions from both
    cur_op, cur_val = _read_condition(current_source)
    new_op, new_val = _read_condition(new_source)

    # 5. compute unified diff (current → proposed)
    diff = list(difflib.unified_diff(
        current_source.splitlines(),
        new_source.splitlines(),
        fromfile="current/PaymentService.java",
        tofile="proposed/PaymentService.java",
        lineterm="",
    ))

    # 6. query graph for affected methods (uses existing graph.json)
    affected = _query_affected("processPayment", depth=2)

    # 7. behavior comparison (current → proposed)
    behavior = _behavior_rows(cur_op, cur_val, new_op, new_val)

    # 8. recommended tests for new condition
    tests = _recommend_tests(cur_op, cur_val, new_op, new_val)

    # 9. describe what will change in plain language
    description = _describe_intent(intent, cur_op, cur_val, new_op, new_val)

    # 10. save this analysis as a persistent history entry
    entry_id = f"h_{int(time.time() * 1000)}"
    entry = {
        "id":                  entry_id,
        "timestamp":           time.strftime("%Y-%m-%d %H:%M:%S"),
        "command":             prompt,
        "intent":              intent,
        "description":         description,
        "currentCondition":    f"order.getAmount() {cur_op} {cur_val:g}" if cur_op else "–",
        "proposedCondition":   f"order.getAmount() {new_op} {new_val:g}" if new_op else "–",
        "flippedCount":        sum(1 for r in behavior if r["changed"]),
        "behaviorPreview":     behavior,
        "affectedMethods":     affected,
        "recommendedTests":    tests,
        "diff":                diff,
        "status":              "previewed",
    }
    _add_history_entry(entry)

    return jsonify({
        "intent":         intent,
        "description":    description,
        "currentSource":  current_source,
        "proposedSource": new_source,
        "diff":           diff,
        "currentCondition":  f"order.getAmount() {cur_op} {cur_val:g}" if cur_op else "–",
        "proposedCondition": f"order.getAmount() {new_op} {new_val:g}" if new_op else "–",
        "affectedMethods":   affected,
        "behaviorPreview":   behavior,
        "recommendedTests":  tests,
        "flippedCount":  sum(1 for r in behavior if r["changed"]),
        "historyId":     entry_id,
    })


def _describe_intent(intent, cur_op, cur_val, new_op, new_val):
    name = intent.get("intent", "")
    if name == "reset":
        return "Restore PaymentService.java to the original baseline (before any changes)."
    if name == "swap":
        return "Swap to the original baseline version."
    if name == "set_threshold":
        return (
            f"Change the payment threshold from {cur_val:g} to {intent['value']:g}. "
            f"The operator stays '{cur_op}'."
        )
    if name == "set_operator":
        return (
            f"Change the comparison operator from '{cur_op}' to '{intent['operator']}'. "
            f"The threshold stays {cur_val:g}."
        )
    if name == "set_condition":
        return (
            f"Change the full condition from '{cur_op} {cur_val:g}' "
            f"to '{intent['operator']} {intent['value']:g}'."
        )
    return "Apply the requested change."


# ════════════════════════════════════════════════════════════════════════════
# API: Prompt Lab — CONFIRM (apply change + run pipeline)
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/prompt/confirm", methods=["POST"])
def api_prompt_confirm():
    """
    Write the proposed source to disk, run the full pipeline,
    return the new report.
    """
    proposed_source = (request.json or {}).get("proposedSource", "")
    history_id      = (request.json or {}).get("historyId")
    if not proposed_source:
        return jsonify({"error": "No proposed source provided."}), 400

    # write the new Java file
    AFTER_FILE.write_text(proposed_source, encoding="utf-8")

    # run full pipeline
    step_results = []
    for step_id, script, label in PIPELINE_STEPS:
        r = subprocess.run(
            [sys.executable, str(PROJECT_DIR / script)],
            cwd=str(PROJECT_DIR), capture_output=True, text=True,
        )
        step_results.append({
            "id": step_id, "label": label,
            "returncode": r.returncode,
            "stdout": r.stdout, "stderr": r.stderr,
            "success": r.returncode == 0,
        })
        if r.returncode != 0:
            break

    report = _load_report()
    if report:
        report["affectedLines"] = _parse_affected_lines(report.get("dependencyAnalysis", ""))

    success = all(s["success"] for s in step_results)

    # mark the history entry as applied
    if history_id:
        _update_history_entry(history_id, {
            "status": "applied" if success else "failed",
            "appliedReport": report,
        })

    return jsonify({
        "steps":  step_results,
        "report": report,
        "success": success,
    })


# ════════════════════════════════════════════════════════════════════════════
# API: command reference
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/prompt/commands")
def api_prompt_commands():
    return jsonify([
        {"command": "threshold <N>",         "example": "threshold 500",      "description": "Change the numeric threshold"},
        {"command": "operator <OP>",          "example": "operator >",         "description": "Change the comparison operator"},
        {"command": "condition <OP> <N>",     "example": "condition >= 250",   "description": "Change operator and threshold together"},
        {"command": "change threshold to <N>","example": "change threshold to 200", "description": "Alternative threshold syntax"},
        {"command": "change operator to <OP>","example": "change operator to <=",   "description": "Alternative operator syntax"},
        {"command": "reset",                  "example": "reset",              "description": "Restore the original baseline file"},
    ])


# ════════════════════════════════════════════════════════════════════════════
# API: command history (persistent)
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/history")
def api_history():
    """Return every saved command analysis, newest first."""
    entries = _load_history()
    entries.reverse()  # newest first
    return jsonify(entries)


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    _save_history([])
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
