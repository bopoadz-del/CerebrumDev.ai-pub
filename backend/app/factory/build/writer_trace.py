"""Writer trace: where one writer pass spent its time, read off its exec stream.

Observability only. Nothing here changes what the writer is asked, allowed
or budgeted; it reads the exec-stream events the worker already keeps with
the second each line ARRIVED on its stdout (the Factory's clock) and says:

* one action per ``tool_use`` event: the tool, the command line it ran (after
  the shared secret redaction), when it started, how long it ran, and its
  exit code when the stream reported one;
* a summary: seconds the model spent between a tool's answer and its next
  call, seconds per tool (a shell tool is split by the program its command
  starts with, so the split comes from the commands themselves, not from a
  list of names), the slowest actions, and every command line run more than
  once with its count.

A tool's run ends at its ``tool_result``: the one that carries the same id
when both carry one, otherwise the earliest result still open. A tool whose
result never arrived has no run time of its own; the time to the next call
is then counted as ``unsplit_s`` and never guessed into model or tool time.
"""

from __future__ import annotations

import shlex
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from app.factory.build.codewhale_worker import (
    _AGENT_ID_KEYS,
    _TOOL_NAME_KEYS,
    _command_text,
    _first_present,
)
from app.factory.build.sanitize import sanitize_for_status

#: Keys a tool_result may carry the command's exit code under.
_EXIT_CODE_KEYS = ("exit_code", "returncode", "exit_status")
#: How many of the slowest actions the summary lists.
SLOWEST_SHOWN = 10


def _program(command: str) -> str:
    """The program a command line starts with (its base name), '' when none."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if "=" in token and not token.startswith(("/", ".")):
            continue  # a leading VAR=value assignment, not the program
        return PurePosixPath(token).name
    return ""


def _ids(event: Dict[str, Any]) -> set:
    return {str(event[k]) for k in _AGENT_ID_KEYS if event.get(k) not in (None, "")}


def _exit_code(event: Dict[str, Any]) -> Optional[int]:
    for key in _EXIT_CODE_KEYS:
        value = event.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def writer_trace(
    timed_events: List[Tuple[float, Dict[str, Any]]],
    start_t: float,
    end_t: float,
) -> Dict[str, Any]:
    """The actions and the time summary of one writer pass.

    ``start_t`` is the second the pass was launched and ``end_t`` the second
    it ended, on the same clock as the events.
    """
    ordered = sorted(
        ((float(t), e) for t, e in timed_events if isinstance(e, dict)),
        key=lambda pair: pair[0],
    )
    actions: List[Dict[str, Any]] = []
    open_calls: List[int] = []
    for t, event in ordered:
        kind = event.get("type")
        if kind == "tool_use":
            command = sanitize_for_status(_command_text(event))
            name = _first_present(event, _TOOL_NAME_KEYS)
            action: Dict[str, Any] = {
                "at_s": round(t - start_t, 1),
                "tool": str(name) if name is not None else "",
                "_t": t,
                "_ids": _ids(event),
            }
            if command:
                action["command"] = command
            actions.append(action)
            open_calls.append(len(actions) - 1)
        elif kind == "tool_result" and open_calls:
            ids = _ids(event)
            pick = next(
                (i for i in open_calls if ids and actions[i]["_ids"] & ids),
                open_calls[0],
            )
            open_calls.remove(pick)
            actions[pick]["_end"] = t
            actions[pick]["run_s"] = round(t - actions[pick]["_t"], 1)
            code = _exit_code(event)
            if code is not None:
                actions[pick]["exit_code"] = code

    model_s = unsplit_s = 0.0
    by_tool: Counter = Counter()
    cursor = start_t
    for index, action in enumerate(actions):
        nxt = actions[index + 1]["_t"] if index + 1 < len(actions) else end_t
        if action["_t"] > cursor:
            model_s += action["_t"] - cursor
        if "_end" in action:
            group = action["tool"]
            program = _program(action.get("command", ""))
            if program:
                group = f"{group}: {program}"
            by_tool[group] += action["_end"] - action["_t"]
            cursor = max(cursor, action["_end"])
        else:
            # No result arrived: the time to the next call cannot be split.
            unsplit_s += max(0.0, nxt - action["_t"])
            cursor = max(cursor, nxt)
    tail_s = max(0.0, end_t - cursor)

    timed = [a for a in actions if "run_s" in a]
    slowest = sorted(timed, key=lambda a: a["run_s"], reverse=True)[:SLOWEST_SHOWN]
    runs = Counter(a["command"] for a in actions if a.get("command"))
    public = [
        {k: v for k, v in a.items() if not k.startswith("_")} for a in actions
    ]
    summary = {
        "wall_s": round(max(0.0, end_t - start_t), 1),
        "actions": len(actions),
        "model_s": round(model_s, 1),
        "tool_s": {k: round(v, 1) for k, v in by_tool.most_common()},
        "unsplit_s": round(unsplit_s, 1),
        "after_last_action_s": round(tail_s, 1),
        "slowest": [
            {k: v for k, v in a.items() if not k.startswith("_")} for a in slowest
        ],
        "repeated": {cmd: n for cmd, n in runs.most_common() if n > 1},
    }
    return {"actions": public, "summary": summary}
