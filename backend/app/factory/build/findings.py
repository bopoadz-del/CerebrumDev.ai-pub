"""Typed gate findings -- the one definition every gate emits and every
consumer reads.

A finding used to be a string, and the WRITER's rework ratchet recovered
"which capability failed" by searching that string for capability ids. Text
is for people; the Factory decides from fields. Each finding is now a
:class:`Finding` carrying::

    {gate, check_id, capability_id|null, file, line|null, finding_shape, detail}

``Finding`` subclasses ``str`` (its value is :meth:`to_text`, the human
line), so every display path -- the ledger's ``findings`` list, the Floor,
the work-list text the coder reads -- keeps working unchanged. Decisions read
the fields, never the text: the ratchet reads ``capability_id``, the brief
classifier reads ``check_id``, same-failure-twice reads ``finding_shape``.

Where the fields come from is typed data the gate already holds -- the
probe's typed records, the JUnit row plus the product's own runtime record of
which capability failed under which test, the acceptance check's id -- never
a parse of the detail.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

#: The schema, in the order it is serialised.
FIELDS = ("gate", "check_id", "capability_id", "file", "line", "finding_shape", "detail")

#: Ledger payload key carrying the typed findings beside the human ``findings``.
TYPED_KEY = "typed_findings"

#: The shape of a finding that declares none.
DEFAULT_SHAPE = "failed"


def normalise_check(check: Any) -> str:
    """One spelling for a check id (the brief compiler's ``[check:<id>]``)."""
    return str(check or "").strip().lower()


def normalise_shape(shape: Any) -> str:
    """The finding SHAPE -- the typed category of a failure (a test row's
    ``failure``/``error``, a probe record's kind, an acceptance ``FAIL``).
    Same-failure-twice compares check id + this; it is the one definition."""
    text = str(shape or "").strip().lower()
    return text or DEFAULT_SHAPE


class Finding(str):
    """A gate finding: a human line (the ``str`` value) plus typed fields."""

    gate: str
    check_id: str
    capability_id: Optional[str]
    file: Optional[str]
    line: Optional[int]
    finding_shape: str

    def __new__(
        cls,
        detail: Any,
        *,
        gate: str = "",
        check_id: str = "",
        capability_id: Optional[str] = None,
        file: Optional[str] = None,
        line: Optional[int] = None,
        finding_shape: Any = None,
        check_defaulted: bool = False,
    ) -> "Finding":
        obj = super().__new__(cls, str(detail if detail is not None else ""))
        obj.gate = str(gate or "")
        obj.check_id = normalise_check(check_id or gate)
        obj.capability_id = str(capability_id) if capability_id else None
        obj.file = str(file) if file else None
        obj.line = int(line) if isinstance(line, int) or (isinstance(line, str) and line.isdigit()) else None
        obj.finding_shape = normalise_shape(finding_shape)
        # True when the check id was not named by the emitter but defaulted
        # (from the gate, or the verdict); the verdict's own check replaces
        # it, an emitter's explicitly named check id never is.
        obj._check_defaulted = bool(check_defaulted) or not check_id
        return obj

    @property
    def detail(self) -> str:
        return str.__str__(self)

    def to_text(self) -> str:
        """The human line. Display only -- never parsed."""
        return str.__str__(self)

    def to_json(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "check_id": self.check_id,
            "capability_id": self.capability_id,
            "file": self.file,
            "line": self.line,
            "finding_shape": self.finding_shape,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Finding":
        return cls(
            data.get("detail", ""),
            gate=str(data.get("gate") or ""),
            check_id=str(data.get("check_id") or ""),
            capability_id=data.get("capability_id"),
            file=data.get("file"),
            line=data.get("line"),
            finding_shape=data.get("finding_shape"),
        )

    def with_fields(self, **changes: Any) -> "Finding":
        data = self.to_json()
        data.update(changes)
        out = Finding.from_json(data)
        if "check_id" not in changes:
            out._check_defaulted = self._check_defaulted
        return out

    def __reduce__(self):  # copy / pickle keep the fields
        return (Finding.from_json, (self.to_json(),))


def coerce(
    items: Iterable[Any],
    *,
    gate: str,
    default_check: str = "",
    per_index_checks: Sequence[Any] = (),
    shape: Any = None,
) -> List[Finding]:
    """Every item as a :class:`Finding`.

    An item that is already a Finding keeps every field it was emitted with;
    a defaulted check id follows the verdict's current check. A plain string
    becomes a Finding of this gate, its check id the per-index id
    (``payload["finding_checks"]``) or the verdict's check, its capability
    unknown (``None``) -- an unlocalised finding.
    """
    own = normalise_check(default_check or gate)
    out: List[Finding] = []
    for index, item in enumerate(items or ()):
        named = normalise_check(per_index_checks[index]) if index < len(per_index_checks) else ""
        if isinstance(item, Finding):
            if item._check_defaulted and (named or own) != item.check_id:
                item = item.with_fields(check_id=named or own)
                item._check_defaulted = not named
            out.append(item)
            continue
        if isinstance(item, Mapping) and "detail" in item:
            out.append(Finding.from_json(item))
            continue
        out.append(
            Finding(
                item,
                gate=gate,
                check_id=named or own,
                finding_shape=shape,
                check_defaulted=not named,
            )
        )
    return out


def typed(items: Iterable[Any]) -> List[Dict[str, Any]]:
    """The JSON form of every typed finding in ``items`` (plain strings are
    skipped: they carry no fields to record)."""
    return [f.to_json() for f in items or () if isinstance(f, Finding)]


def from_payload(payload: Mapping[str, Any], *, key: str = "findings") -> List[Any]:
    """Rehydrate a ledger payload's findings: the typed list when it was
    recorded, else the plain strings."""
    rows = payload.get(TYPED_KEY) if isinstance(payload, Mapping) else None
    if isinstance(rows, list) and rows:
        return [Finding.from_json(r) for r in rows if isinstance(r, Mapping)]
    return [str(f) for f in (payload.get(key) or []) if str(f).strip()] if isinstance(payload, Mapping) else []


#: While the product suite runs, every Factory-emitted test that judges a
#: capability appends ``{"test": <nodeid>, "capability": <id>}`` for each
#: capability it found failing to the file this variable names. The stamped
#: conftest sets it beside pytest's JUnit report (as it does the placeholder
#: refusal log), so the gate localises a failing test by this typed record.
CAPABILITY_LOG_ENV = "FACTORY_CAPABILITY_LOG"
CAPABILITY_LOG_SUFFIX = ".capability_findings.jsonl"


def render_capability_recorder() -> List[str]:
    """Source lines defining ``_record_capability_failure(capability_id)`` for
    an emitted product test file (tests are not a package, so the one source
    is emitted into each file). A no-op outside the Factory's suite run."""
    return [
        "import json as _cap_json",
        "import os as _cap_os",
        "",
        "",
        "def _record_capability_failure(capability_id):",
        '    """Factory suite only: note the capability this running test found',
        '    failing (pytest names the test in PYTEST_CURRENT_TEST)."""',
        f"    path = _cap_os.environ.get({CAPABILITY_LOG_ENV!r})",
        "    if not path or not capability_id:",
        "        return",
        '    running = _cap_os.environ.get("PYTEST_CURRENT_TEST") or ""',
        '    test = running.rsplit(" (", 1)[0]',
        "    try:",
        '        with open(path, "a", encoding="utf-8") as fh:',
        '            fh.write(_cap_json.dumps({"test": test, "capability": str(capability_id)}) + "\\n")',
        "    except OSError:",
        "        pass",
    ]


def read_capability_log(path: Any) -> Dict[str, List[str]]:
    """``{nodeid: [capability, ...]}`` from the suite's capability log -- the
    typed records the emitted tests wrote. Missing or unreadable means none."""
    import json
    from pathlib import Path

    out: Dict[str, List[str]] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return out
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        test, cap = rec.get("test"), rec.get("capability")
        if isinstance(test, str) and test and isinstance(cap, str) and cap:
            caps = out.setdefault(test, [])
            if cap not in caps:
                caps.append(cap)
    return out


def junit_row_findings(rows: Sequence[Mapping[str, Any]], *, gate: str, limit: int = 20) -> List[Finding]:
    """Typed findings for failing JUnit rows: one per capability the row's
    test recorded (``capabilities``), or one unlocalised finding when it
    recorded none. Shape = the row's kind (failure / error)."""
    out: List[Finding] = []
    for row in rows:
        text = f"FAILED {row.get('nodeid')} - {row.get('message')}"
        caps = [c for c in (row.get("capabilities") or []) if c] or [None]
        for cap in caps:
            out.append(
                Finding(
                    text,
                    gate=gate,
                    check_id=gate,
                    capability_id=cap,
                    file=row.get("file"),
                    finding_shape=row.get("kind"),
                    check_defaulted=True,
                )
            )
    return out[:limit]


def capability_ids(items: Iterable[Any]) -> Set[str]:
    """The capability ids the typed findings name. Untyped items name none."""
    return {f.capability_id for f in items or () if isinstance(f, Finding) and f.capability_id}


def rework_targets(work_list: Sequence[Any], cap_ids: Sequence[str]) -> Set[str]:
    """Which capabilities a WRITER rework round may regenerate -- the ratchet.

    * No work list (a first pass): every capability.
    * Some finding names a capability of this plan: exactly those. A finding
      with no ``capability_id`` (an infrastructure failure, a re-check line)
      never widens the set.
    * No finding names one: every capability -- nothing could be localised,
      and regenerating nothing would end the round with the gate still red.

    Decided from ``capability_id`` fields only; the text is never read.
    """
    plan = list(cap_ids)
    if not work_list:
        return set(plan)
    named = capability_ids(work_list) & set(plan)
    return named or set(plan)
