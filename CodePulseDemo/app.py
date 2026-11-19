from pathlib import Path
import json
import subprocess
import sys
import difflib
import re
import time

from flask import Flask, render_template, jsonify, request

from scanner import (
    scan_processes, detect_current_process, resolve_process,
    baseline_path, read_condition, rewrite_condition,
    condition_string, behavior_rows, recommend_tests,
    parse_condition, evaluate, sample_amounts, ALIASES,
)

app = Flask(__name__)


# ── prevent the browser from caching API responses ─────────────────────────
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
GRAPH_FILE   = PROJECT_DIR / "codepulse-out" / "graph.json"
HISTORY_FILE = EVIDENCE_DIR / "history.json"

VALID_OPS = {">=", ">", "<=", "<", "==", "!="}

PIPELINE_STEPS = [
    ("detect_method",    "detect_method.py",    "Detect Changed Method"),
    ("analyze_impact",   "analyze_impact.py",   "Analyze Dependencies"),
    ("analyze_behavior", "analyze_behavior.py", "Compare Behavior"),
    ("generate_report",  "generate_report.py",  "Generate Impact Report"),
    ("recommend_tests",  "recommend_tests.py",  "Recommend Tests"),
    ("validate_report",  "validate_report.py",  "Validate Report"),
]


# ════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ════════════════════════════════════════════════════════════════════════════

def _load_report():
    if not REPORT_FILE.exists():
        return None
    return json.loads(REPORT_FILE.read_text(encoding="utf-8"))


def _parse_affected_lines(dependency_output: str) -> list[str]:
    return [line[2:] for line in dependency_output.splitlines() if line.startswith("- ")]


def _load_graph():
    if not GRAPH_FILE.exists():
        return None
    try:
        from codepulse_graph.paths import load_node_link_graph
        return load_node_link_graph(json.loads(GRAPH_FILE.read_text(encoding="utf-8")))
    except Exception:
        return None


def _query_affected(method_name: str, depth: int = 2) -> list[dict]:
    """Reverse-BFS walk of the knowledge graph to find dependents of a method."""
    try:
        from codepulse_graph.affected import affected_nodes, resolve_seed
        G = _load_graph()
        if G is None:
            return []
        seed = resolve_seed(G, f".{method_name}()")
        if seed is None:
            return []
        out = []
        for h in affected_nodes(G, seed, depth=depth):
            d = G.nodes.get(h.node_id, {})
            out.append({
                "nodeId":   h.node_id,
                "label":    d.get("label", h.node_id),
                "depth":    h.depth,
                "relation": h.via_relation,
                "file":     h.via_file or d.get("source_file", ""),
                "location": h.via_location or d.get("source_location", ""),
            })
        return out
    except Exception as exc:
        return [{"error": str(exc)}]


# ════════════════════════════════════════════════════════════════════════════
# Command parser (process-aware)
# ════════════════════════════════════════════════════════════════════════════

def _is_op(t):  return t in VALID_OPS
def _is_num(t): return re.fullmatch(r"\d+(?:\.\d+)?", t) is not None


def _parse_command(prompt: str):
    """
    Parse a structured command. The first token may name the process to change
    (by field, e.g. 'amount'/'quantity', or by method, e.g. 'processPayment'),
    OR a legacy keyword (threshold/operator/condition/reset) that targets the
    current (default) process.

    Examples:
      amount >= 500          stock >= 5         quantity threshold 5
      amount operator >       processPayment >= 250     reset quantity
      threshold 500           operator >        condition >= 250    reset
    """
    p = prompt.strip().lower()
    t = p.split()

    # reset [field]
    if t and t[0] == "reset":
        return {"intent": "reset", "field": t[1] if len(t) > 1 else None}

    # <field> threshold <num>   /   <field> operator <op>   /   <field> <op> <num>
    if len(t) >= 3:
        field = t[0]
        if t[1] == "threshold" and _is_num(t[2]):
            return {"intent": "set_threshold", "field": field, "value": float(t[2])}
        if t[1] == "operator" and _is_op(t[2]):
            return {"intent": "set_operator", "field": field, "operator": t[2]}
        if _is_op(t[1]) and _is_num(t[2]):
            return {"intent": "set_condition", "field": field, "operator": t[1], "value": float(t[2])}

    # legacy "change X to Y" forms → target the current (default) process
    if len(t) >= 2 and t[0] == "change":
        if t[1] == "threshold" and len(t) == 4 and t[2] == "to" and _is_num(t[3]):
            return {"intent": "set_threshold", "field": None, "value": float(t[3])}
        if t[1] == "operator" and len(t) == 4 and t[2] == "to" and _is_op(t[3]):
            return {"intent": "set_operator", "field": None, "operator": t[3]}
        if t[1] == "condition" and len(t) == 5 and t[2] == "to" and _is_op(t[3]) and _is_num(t[4]):
            return {"intent": "set_condition", "field": None, "operator": t[3], "value": float(t[4])}

    # legacy bare forms → current (default) process
    if t[0] == "threshold" and len(t) == 2 and _is_num(t[1]):
        return {"intent": "set_threshold", "field": None, "value": float(t[1])}
    if t[0] == "operator" and len(t) == 2 and _is_op(t[1]):
        return {"intent": "set_operator", "field": None, "operator": t[1]}
    if t[0] == "condition" and len(t) == 3 and _is_op(t[1]) and _is_num(t[2]):
        return {"intent": "set_condition", "field": None, "operator": t[1], "value": float(t[2])}

    return {
        "error": (
            "Command not recognised. Try:\n"
            "  amount >= 500\n  quantity >= 5\n  stock threshold 5\n"
            "  amount operator >\n  reset quantity\n  reset"
        )
    }


def _describe_intent(intent, proc):
    name = intent.get("intent", "")
    field = (proc or {}).get("field", "the process")
    method = (proc or {}).get("method", "")
    if name == "reset":
        return f"Restore {method} ({field}) to its original baseline rule."
    if name == "set_threshold":
        return f"Change the {field} threshold of {method} to {intent['value']:g}."
    if name == "set_operator":
        return f"Change the {field} comparison operator of {method} to '{intent['operator']}'."
    if name == "set_condition":
        return f"Change the {field} rule of {method} to '{intent['operator']} {intent['value']:g}'."
    return "Apply the requested change."


# ════════════════════════════════════════════════════════════════════════════
# Pages
# ════════════════════════════════════════════════════════════════════════════

@app.route("/")
def dashboard():
    return render_template("index.html")


# ════════════════════════════════════════════════════════════════════════════
# API: detected processes (auto-detected from the project)
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/processes")
def api_processes():
    procs = scan_processes(PROJECT_DIR)
    current = detect_current_process(PROJECT_DIR)
    current_method = current["method"] if current else None
    out = []
    for p in procs:
        bp = baseline_path(PROJECT_DIR, p["file"])
        base_op, base_val = (None, None)
        if bp.exists():
            base_op, base_val = read_condition(bp.read_text(encoding="utf-8"), p["getter"])
        out.append({
            "method":     p["method"],
            "file":       p["file"],
            "field":      p["field"],
            "receiver":   p["receiver"],
            "getter":     p["getter"],
            "operator":   p["operator"],
            "value":      p["value"],
            "currentCondition": condition_string(p["receiver"], p["getter"], p["operator"], p["value"]),
            "baselineCondition": condition_string(p["receiver"], p["getter"], base_op, base_val) if base_op else None,
            "isCurrent":  p["method"] == current_method,
        })
    return jsonify(out)


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

    cur = detect_current_process(PROJECT_DIR)
    before_content = ""
    after_content = ""
    before_rel = None
    after_rel = None
    if cur:
        bp = baseline_path(PROJECT_DIR, cur["file"])
        if bp.exists():
            before_content = bp.read_text(encoding="utf-8")
            before_rel = str(bp.relative_to(PROJECT_DIR))
        after_content = Path(cur["absPath"]).read_text(encoding="utf-8")
        after_rel = cur["file"]

    return jsonify({
        "projectDir":     str(PROJECT_DIR),
        "currentMethod":  cur["method"] if cur else None,
        "currentFile":    cur["file"] if cur else None,
        "beforeFile":     before_rel,
        "afterFile":      after_rel,
        "graphExists":    GRAPH_FILE.exists(),
        "reportExists":   REPORT_FILE.exists(),
        "javaFiles":      java_files,
        "beforeContent":  before_content,
        "afterContent":   after_content,
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

@app.route("/api/pipeline/steps")
def api_pipeline_steps():
    return jsonify([{"id": s[0], "script": s[1], "label": s[2]} for s in PIPELINE_STEPS])


@app.route("/api/pipeline/run", methods=["POST"])
def api_pipeline_run():
    results = []
    for step_id, script, label in PIPELINE_STEPS:
        r = subprocess.run([sys.executable, str(PROJECT_DIR / script)],
                           cwd=str(PROJECT_DIR), capture_output=True, text=True)
        results.append({"id": step_id, "label": label, "script": script,
                        "returncode": r.returncode, "stdout": r.stdout,
                        "stderr": r.stderr, "success": r.returncode == 0})
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
    r = subprocess.run([sys.executable, str(PROJECT_DIR / step[1])],
                       cwd=str(PROJECT_DIR), capture_output=True, text=True)
    return jsonify({"id": step[0], "label": step[2], "script": step[1],
                    "returncode": r.returncode, "stdout": r.stdout,
                    "stderr": r.stderr, "success": r.returncode == 0})


# ════════════════════════════════════════════════════════════════════════════
# API: inline analysis (auto-detected current process)
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/analyze/diff")
def api_analyze_diff():
    cur = detect_current_process(PROJECT_DIR)
    if cur is None:
        return jsonify({"error": "No changeable process found."}), 404
    bp = baseline_path(PROJECT_DIR, cur["file"])
    before = bp.read_text(encoding="utf-8").splitlines() if bp.exists() else []
    after  = Path(cur["absPath"]).read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(before, after,
                fromfile=Path(cur["file"]).name + " (baseline)",
                tofile=Path(cur["file"]).name + " (current)", lineterm=""))
    return jsonify({"diff": diff, "method": cur["method"]})


@app.route("/api/analyze/method")
def api_analyze_method():
    cur = detect_current_process(PROJECT_DIR)
    if cur is None:
        return jsonify({"error": "No changeable process found."}), 404
    bp = baseline_path(PROJECT_DIR, cur["file"])
    before = bp.read_text(encoding="utf-8").splitlines() if bp.exists() else []
    after  = Path(cur["absPath"]).read_text(encoding="utf-8").splitlines()
    matcher = difflib.SequenceMatcher(a=before, b=after)
    changed_lines = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            changed_lines.extend(range(j1 + 1, j2 + 1))
    method_pattern = re.compile(
        r"\b(?:public|private|protected)\s+[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{")
    methods = []
    for number, line in enumerate(after, start=1):
        m = method_pattern.search(line)
        if m:
            methods.append({"line": number, "name": m.group(1)})
    changed_method = None
    if changed_lines:
        first = min(changed_lines)
        cands = [m for m in methods if m["line"] < first]
        if cands:
            changed_method = max(cands, key=lambda x: x["line"])["name"]
    return jsonify({"changedLines": changed_lines, "methods": methods,
                    "detectedMethod": changed_method or cur["method"]})


@app.route("/api/analyze/behavior")
def api_analyze_behavior():
    cur = detect_current_process(PROJECT_DIR)
    if cur is None:
        return jsonify({"error": "No changeable process found."}), 404
    bp = baseline_path(PROJECT_DIR, cur["file"])
    base_src = bp.read_text(encoding="utf-8") if bp.exists() else Path(cur["absPath"]).read_text(encoding="utf-8")
    cur_src  = Path(cur["absPath"]).read_text(encoding="utf-8")
    old_op, old_val = read_condition(base_src, cur["getter"])
    new_op, new_val = read_condition(cur_src, cur["getter"])
    if old_op is None or new_op is None:
        return jsonify({"error": "Could not parse conditions."}), 422
    return jsonify({
        "method":        cur["method"],
        "oldCondition":  condition_string(cur["receiver"], cur["getter"], old_op, old_val),
        "newCondition":  condition_string(cur["receiver"], cur["getter"], new_op, new_val),
        "sampleResults": behavior_rows(cur["receiver"], cur["getter"], old_op, old_val, new_op, new_val),
    })


# ════════════════════════════════════════════════════════════════════════════
# API: Prompt Lab — PREVIEW
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/prompt/preview", methods=["POST"])
def api_prompt_preview():
    prompt = (request.json or {}).get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "No prompt provided."}), 400

    intent = _parse_command(prompt)
    if "error" in intent:
        return jsonify({"error": intent["error"]}), 422

    # resolve the targeted process from the prompt's field token
    proc, err = resolve_process(intent.get("field"), PROJECT_DIR)
    if err:
        return jsonify({"error": err}), 422

    cur_src = Path(proc["absPath"]).read_text(encoding="utf-8")
    cur_op, cur_val = read_condition(cur_src, proc["getter"])

    # compute the proposed source (no disk write)
    if intent["intent"] == "reset":
        bp = baseline_path(PROJECT_DIR, proc["file"])
        if not bp.exists():
            return jsonify({"error": "No baseline snapshot to reset to."}), 422
        new_src = bp.read_text(encoding="utf-8")
        new_op, new_val = read_condition(new_src, proc["getter"])
    else:
        if intent["intent"] == "set_threshold":
            new_op, new_val = cur_op, intent["value"]
        elif intent["intent"] == "set_operator":
            new_op, new_val = intent["operator"], cur_val
        else:  # set_condition
            new_op, new_val = intent["operator"], intent["value"]
        new_src = rewrite_condition(cur_src, proc["receiver"], proc["getter"], new_op, new_val)

    diff = list(difflib.unified_diff(
        cur_src.splitlines(), new_src.splitlines(),
        fromfile="current/" + Path(proc["file"]).name,
        tofile="proposed/" + Path(proc["file"]).name, lineterm=""))

    affected = _query_affected(proc["method"], depth=2)
    behavior = behavior_rows(proc["receiver"], proc["getter"], cur_op, cur_val, new_op, new_val)
    tests = recommend_tests(cur_op, cur_val, new_op, new_val)
    description = _describe_intent(intent, proc)

    # persist to history
    entry_id = f"h_{int(time.time() * 1000)}"
    entry = {
        "id":                entry_id,
        "timestamp":         time.strftime("%Y-%m-%d %H:%M:%S"),
        "command":           prompt,
        "intent":            intent,
        "process":           proc["method"],
        "field":             proc["field"],
        "file":              proc["file"],
        "description":       description,
        "currentCondition":  condition_string(proc["receiver"], proc["getter"], cur_op, cur_val) if cur_op else "–",
        "proposedCondition": condition_string(proc["receiver"], proc["getter"], new_op, new_val) if new_op else "–",
        "flippedCount":      sum(1 for r in behavior if r["changed"]),
        "behaviorPreview":   behavior,
        "affectedMethods":   affected,
        "recommendedTests":  tests,
        "diff":              diff,
        "status":            "previewed",
    }
    _save_history_entry(entry)

    return jsonify({
        "intent":            intent,
        "process":           {"method": proc["method"], "field": proc["field"], "file": proc["file"]},
        "description":       description,
        "currentSource":     cur_src,
        "proposedSource":    new_src,
        "targetFile":        proc["file"],
        "diff":              diff,
        "currentCondition":  entry["currentCondition"],
        "proposedCondition": entry["proposedCondition"],
        "affectedMethods":   affected,
        "behaviorPreview":   behavior,
        "recommendedTests":  tests,
        "flippedCount":      entry["flippedCount"],
        "historyId":         entry_id,
    })


# ════════════════════════════════════════════════════════════════════════════
# API: Prompt Lab — CONFIRM (apply change + run pipeline)
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/prompt/confirm", methods=["POST"])
def api_prompt_confirm():
    body = request.json or {}
    proposed_source = body.get("proposedSource", "")
    target_file     = body.get("targetFile", "")
    history_id      = body.get("historyId")
    if not proposed_source or not target_file:
        return jsonify({"error": "No proposed source / target file provided."}), 400

    # write the new Java file
    (PROJECT_DIR / target_file).write_text(proposed_source, encoding="utf-8")

    # run the full pipeline (scripts auto-detect the changed process)
    step_results = []
    for step_id, script, label in PIPELINE_STEPS:
        r = subprocess.run([sys.executable, str(PROJECT_DIR / script)],
                           cwd=str(PROJECT_DIR), capture_output=True, text=True)
        step_results.append({"id": step_id, "label": label, "returncode": r.returncode,
                             "stdout": r.stdout, "stderr": r.stderr, "success": r.returncode == 0})
        if r.returncode != 0:
            break

    report = _load_report()
    if report:
        report["affectedLines"] = _parse_affected_lines(report.get("dependencyAnalysis", ""))

    success = all(s["success"] for s in step_results)
    if history_id:
        _update_history_entry(history_id, {"status": "applied" if success else "failed", "appliedReport": report})

    return jsonify({"steps": step_results, "report": report, "success": success})


# ════════════════════════════════════════════════════════════════════════════
# API: command reference + history
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/prompt/commands")
def api_prompt_commands():
    return jsonify([
        {"example": "amount >= 500",      "description": "Change the payment rule (processPayment)"},
        {"example": "quantity >= 5",     "description": "Change the stock rule (hasStock)"},
        {"example": "stock threshold 5", "description": "Change only the stock threshold"},
        {"example": "amount operator >", "description": "Change only the payment operator"},
        {"example": "threshold 500",     "description": "Legacy: change the current process threshold"},
        {"example": "reset",             "description": "Restore the current process to its baseline"},
        {"example": "reset quantity",    "description": "Restore the stock rule to its baseline"},
    ])


# ── history persistence ─────────────────────────────────────────────────────

def _load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_history(entries):
    HISTORY_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _save_history_entry(entry):
    entries = _load_history()
    entries.append(entry)
    _save_history(entries)


def _update_history_entry(entry_id, updates):
    for e in _load_history():
        if e.get("id") == entry_id:
            e.update(updates)
            break
    _save_history(_load_history())


@app.route("/api/history")
def api_history():
    entries = _load_history()
    entries.reverse()
    return jsonify(entries)


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    _save_history([])
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
