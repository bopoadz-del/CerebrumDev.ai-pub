"""The one customer brief the writer, the grader and the tester read.

A brief is a JSON object (or a mapping of the same shape). Required fields:

- ``ask`` — the customer's own words, a non-empty string. Stages insert this
  record's text verbatim and do not rewrite it.
- ``documents`` — whether the customer uploads documents, and which file types:
  ``{"uploads": <bool>, "file_types": [<str>, ...]}``.
  ``uploads`` true requires a non-empty ``file_types`` list.
  ``uploads`` false requires ``file_types`` to be a list, empty.

Blueprint text and the Factory contract are not this record. A value that is
not this shape cannot be read. A shape that omits a required field is
incomplete. Either one is reported and the build does not start.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "customer_brief.v1"
REQUIRED_FIELDS = ("ask", "documents")
DOCUMENT_FIELDS = ("uploads", "file_types")

#: What a stage says when the brief cannot be read or a required field is
#: missing. The missing fields are named after the colon.
REPORT_PREFIX = "brief incomplete/unreadable"

#: Stages that must quote the same text.
STAGES = ("writer", "grader", "tester")


@dataclass(frozen=True)
class CustomerBrief:
    """A brief that parsed and has every required field."""

    ask: str
    uploads: bool
    file_types: Tuple[str, ...]
    #: The text stages quote. The original string when the brief was given
    #: as text; the canonical JSON when it was given as a mapping.
    text: str

    def quote(self) -> str:
        return self.text


def format_report(problems: Sequence[str]) -> str:
    """The gate report. ``problems`` names what is missing or ``unreadable``."""
    shown = ", ".join(str(item) for item in problems if str(item).strip()) or "unreadable"
    return f"{REPORT_PREFIX}: {shown}"


def _canonical(ask: str, uploads: bool, file_types: Sequence[str]) -> str:
    body = {
        "ask": ask,
        "documents": {"file_types": list(file_types), "uploads": uploads},
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def _problems_for_documents(value: Any) -> Tuple[Optional[bool], Tuple[str, ...], List[str]]:
    if not isinstance(value, Mapping):
        return None, (), ["documents"]
    missing: List[str] = []
    if "uploads" not in value:
        missing.append("documents.uploads")
    if "file_types" not in value:
        missing.append("documents.file_types")
    if missing:
        return None, (), missing
    uploads = value.get("uploads")
    types = value.get("file_types")
    if not isinstance(uploads, bool):
        missing.append("documents.uploads")
    if not isinstance(types, list) or not all(isinstance(item, str) and item.strip() for item in types):
        missing.append("documents.file_types")
    if missing:
        return None, (), missing
    file_types = tuple(item.strip().lower() for item in types)
    if uploads and not file_types:
        return None, (), ["documents.file_types"]
    if not uploads and file_types:
        return None, (), ["documents.file_types"]
    return uploads, file_types, []


def _from_mapping(value: Mapping[str, Any], *, text: Optional[str]) -> Tuple[Optional[CustomerBrief], List[str]]:
    missing: List[str] = []
    if "ask" not in value:
        missing.append("ask")
    if "documents" not in value:
        missing.append("documents")
    ask = value.get("ask")
    if "ask" in value and (not isinstance(ask, str) or not ask.strip()):
        missing.append("ask")
    uploads, file_types, doc_missing = _problems_for_documents(value.get("documents"))
    # A missing documents object is already named; don't also name its fields.
    if "documents" not in missing:
        missing.extend(doc_missing)
    if missing or uploads is None:
        # de-dupe, keep order
        seen: List[str] = []
        for item in missing:
            if item not in seen:
                seen.append(item)
        return None, seen or ["unreadable"]
    ask_text = ask.strip()
    quoted = text if text is not None else _canonical(ask_text, uploads, file_types)
    return CustomerBrief(ask_text, uploads, file_types, quoted), []


def read_brief(raw: Any) -> Tuple[Optional[CustomerBrief], List[str]]:
    """``(record, problems)``. ``record`` is set only when every required
    field is present. ``problems`` is ``["unreadable"]`` when ``raw`` is not
    this format, otherwise the missing field names."""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None, ["unreadable"]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None, ["unreadable"]
        if not isinstance(parsed, Mapping):
            return None, ["unreadable"]
        return _from_mapping(parsed, text=text)
    if isinstance(raw, Mapping):
        return _from_mapping(raw, text=None)
    return None, ["unreadable"]


def stage_quote(raw: Any) -> Optional[str]:
    """The verbatim brief text, or None when ``raw`` is not a complete brief."""
    record, _problems = read_brief(raw)
    if record is None:
        return None
    return record.quote()


def stage_quotes(raw: Any) -> Mapping[str, str]:
    """The same verbatim text for the writer, the grader and the tester.
    Empty when ``raw`` is not a complete brief."""
    text = stage_quote(raw)
    if text is None:
        return {}
    return {stage: text for stage in STAGES}


def customer_words(raw: str) -> str:
    """The customer's ask when ``raw`` is a customer brief; otherwise ``raw``.

    A feature list shows the words. The runner still receives the whole record.
    """
    record, _problems = read_brief(raw)
    if record is None:
        return raw
    return record.ask


def file_types_of(names: Sequence[Any]) -> List[str]:
    """The type of each uploaded file, taken from the name the session stored.

    The type is that name's own suffix. Nothing here is a list of types to
    search for in the customer's words.
    """
    found: List[str] = []
    for name in names:
        leaf = str(name).replace("\\", "/").strip().rsplit("/", 1)[-1]
        dot = leaf.rfind(".")
        if dot <= 0 or dot == len(leaf) - 1:
            continue
        suffix = leaf[dot + 1:].strip().lower()
        if not suffix or suffix in found:
            continue
        found.append(suffix)
    return found


def emit_brief(ask: str, source_files: Optional[Sequence[Any]] = None) -> str:
    """``customer_brief.v1`` as text.

    ``ask`` is the customer's words, unchanged. ``documents.uploads`` is true
    when the session stored a file, and ``documents.file_types`` are those
    files' own suffixes. No file stored means uploads false and an empty list.
    """
    names = [item for item in (source_files or []) if str(item).strip()]
    body = {
        "ask": ask if isinstance(ask, str) else str(ask),
        "documents": {
            "file_types": file_types_of(names),
            "uploads": bool(names),
        },
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True)
