"""Main loop — Article 2's six steps are the control flow, not a prompt suggestion."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from typing import Any, Dict, List

from . import change_request, dna, engine, envelope, gates, protocols, report, stop

ALLOWED_COMMANDS = ("pytest", "python -m pytest", "npm test", "npm run", "git status", "git diff", "git add", "git commit", "git checkout -b")


def _max_turns() -> int:
    return int(os.getenv("AGENT_MAX_TURNS", "12"))


def _system(law: protocols.Law) -> str:
    return ("You are the Factory's coding agent. You act ONLY on the approved change-request, inside the declared envelope. Reply with JSON only.\n\n"
            f"STOP conditions (law):\n{law.stop_conditions}\n\nAnti-patterns (banned):\n{law.anti_patterns}")


def _json_reply(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):  # tolerate fenced JSON
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except (json.JSONDecodeError, ValueError):
        stop.halt("model_not_json", {"reply_head": text[:400]}, "The model must answer with a JSON object only.")
    return doc


def _declare(eng: engine.Engine, law, cr, bundle) -> Dict[str, Any]:
    user = (f"Change-request ({cr.kind}) for product {cr.product_id}:\n{json.dumps(cr.raw, indent=2)[:4000]}\n\n"
            f"DNA bundle files available: {sorted(bundle.files)}\n\n"
            'Step 3 of the loop: DECLARE the files you intend to touch. Reply: {"files": ["relative/path", ...]}')
    return _json_reply(eng.complete([{"role": "system", "content": _system(law)}, {"role": "user", "content": user}]))


def _run_command(cmd: str, product_root: str) -> str:
    try:
        argv = shlex.split(cmd)
    except ValueError:
        argv = []
    allowed = any(argv[:len(shlex.split(a))] == shlex.split(a) for a in ALLOWED_COMMANDS)
    if not argv or not allowed:
        return f"BLOCKED: command not on the allowlist ({cmd!r})"
    try:
        proc = subprocess.run(argv, shell=False, cwd=product_root, capture_output=True, text=True, timeout=600)
        return ((proc.stdout or "") + (proc.stderr or "")).strip()[-4000:] or f"(exit {proc.returncode}, no output)"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def _tool_result(action: Dict[str, Any], env: envelope.Envelope, product_root: str) -> str:
    tool, path = action.get("tool"), str(action.get("path", ""))
    if tool == "read_file":
        try:
            return env.read_path(path).read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError as exc:
            return f"ERROR: {exc}"
    if tool == "write_file":
        target = env.check_write(path)  # halts on violation — by design
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(action.get("content", "")), encoding="utf-8")
        env.record_write(path)
        return f"wrote {path}"
    if tool == "run_command":
        return _run_command(str(action.get("command", "")), product_root)
    if tool == "git_pr":
        return "PARKED: this runtime opens no PRs (no credentials by design) — push the branch and open the PR from CI."
    return f"ERROR: unknown tool {tool!r}"


def _implement(eng, law, cr, bundle, env, product_root, hint: str = "") -> None:
    dna_excerpts = "\n".join(f"--- {n} ---\n{t[:1500]}" for n, t in sorted(bundle.files.items())[:6])
    user = (f"Change-request:\n{json.dumps(cr.raw, indent=2)[:4000]}\n\nDeclared files: {sorted(env.declared)}\n\nRelevant DNA excerpts:\n{dna_excerpts}\n\n{hint}\n\n"
            'Step 4: IMPLEMENT. Reply JSON: {"actions": [{"tool": "read_file|write_file|run_command|done", "path": "...", "content": "...", "command": "..."}], "notes": "..."}. '
            'Finish with {"actions": [{"tool": "done"}]}. Writes outside the declared files halt the run.')
    messages = [{"role": "system", "content": _system(law)}, {"role": "user", "content": user}]
    for _turn in range(_max_turns()):
        reply = _json_reply(eng.complete(messages))
        actions = reply.get("actions")
        if not isinstance(actions, list):
            stop.halt("model_not_json", {"reply": reply}, 'Expected {"actions": [...]}.')
        messages.append({"role": "assistant", "content": json.dumps(reply)})
        results, done = [], False
        for action in actions:
            if not isinstance(action, dict):
                continue
            if action.get("tool") == "done":
                done = True
                continue
            results.append(f"[{action.get('tool')} {action.get('path') or action.get('command') or ''}]\n{_tool_result(action, env, product_root)}")
        if results:
            messages.append({"role": "user", "content": "Tool results:\n" + "\n\n".join(results)})
        if done:
            return
    stop.halt("max_turns_exceeded", {"turns": _max_turns()}, "Raise AGENT_MAX_TURNS or split the change-request.")


def run(cr_path: str, product_root: str) -> report.Report:
    cr = law = eng = rep = None
    try:
        cr = change_request.load(cr_path)  # 1. READ — validated, signed
        bundle = dna.load(product_root)  # 2. LOAD — checksums FIRST
        law = protocols.load()  #        law version stamped
        eng = engine.resolve()  #        ENGINE_PROFILE -> complete()
        rep = report.Report(request_id=cr.request_id, product_id=cr.product_id, protocol_version=law.version, engine_profile=eng.profile)
        env = envelope.Envelope(product_root)
        env.guard(_declare(eng, law, cr, bundle))  # 3. DECLARE
        _implement(eng, law, cr, bundle, env, product_root)  # 4. IMPLEMENT (writes guarded)
        results = gates.run_all(product_root, gates.GateResults())  # 5. GATES
        if results.failed:  # one repair round, per the gate-failed-twice law
            _implement(eng, law, cr, bundle, env, product_root, hint=f"The gates failed:\n{json.dumps(results.as_list())[:2000]}")
            gates.run_all(product_root, results)
        if results.failed:
            stop.halt("gate_failed_twice", {"gate_results": results.as_list()}, "Fix the failing gate locally, then re-run the change-request.")
        rep.outcome, rep.files_changed, rep.gate_results, rep.deviations = "completed", env.actual_writes(), results.as_list(), env.deviations()
        return rep
    except stop.Halt as exc:
        if rep is None:
            rep = report.Report(request_id=cr.request_id if cr else "unknown", product_id=cr.product_id if cr else "unknown", protocol_version=law.version if law else "unknown", engine_profile=eng.profile if eng else "unknown")
        rep.outcome, rep.halt = "halted", exc.as_dict()
        rep.parked_items.append(exc.as_dict())
        return rep
    finally:
        if rep is not None:  # 6. REPORT — contract or bust, even on halt
            report.emit(rep)


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m runtime <change-request.json> <product_root>", file=sys.stderr)
        return 2
    rep = run(argv[1], argv[2])
    print(json.dumps(rep.as_dict(), indent=2, sort_keys=True))
    return 0 if rep.ok else 1
