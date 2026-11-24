"""The grader's findings, one row each, in plain words.

A grader verdict quotes every failing item as text (``GraderVerdict.items``).
This module turns each item into one row -- what is wrong, where, which rule,
who owns it -- so the ledger, the backend log and the Floor all say the same
thing. It changes nothing the grader checks or decides: it only reads the
verdict after it is made.

* ``rule`` and ``what`` come from the item's own shape: the grader writes every
  item from a fixed prefix (``ITEM_SHAPES``), and the sentence belongs to that
  prefix, never to a product, a capability or a case.
* ``where`` is the part of the item after its prefix (a capability id, a UI
  step, a file path or a review question), as the grader wrote it.
* ``owner`` is read from the verdict's own lists: ``writer_items`` are the
  writer's to fix and the only rows sent back to it; ``factory_owed`` are the
  Factory's; everything else is a part the grader could not check.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Who a row belongs to.
WRITER = "writer"
FACTORY = "factory"
RUN = "run"

#: What ``where`` names, which decides the plain ``place`` the card shows:
#: a capability id reads as words, a screen step or a review question as
#: written, a file path never reaches the card face (machine text).
CAPABILITY, STEP, QUESTION, FILE, NONE = "capability", "step", "question", "file", "none"

#: (prefix the grader writes the item with, rule id, plain sentence, separator
#: that ends ``where``, what ``where`` names). The first matching prefix wins;
#: an item no prefix matches is quoted under ``FALLBACK``.
ITEM_SHAPES: Tuple[Tuple[str, str, str, str, str], ...] = (
    (
        "function grade FAIL: ",
        "function_check",
        "This feature did not give a correct answer when the quality check called it.",
        "",
        CAPABILITY,
    ),
    (
        "UI step FAIL: ",
        "screen_walk",
        "This screen step did not work when the quality check walked through the app.",
        "",
        STEP,
    ),
    (
        "re-run writer on: ",
        "review_question",
        "A review question about this part is still open.",
        " -- ",
        QUESTION,
    ),
    (
        "de-hardwire ",
        "no_fixed_answers",
        "This feature returns a fixed answer instead of working it out from its input.",
        ": ",
        CAPABILITY,
    ),
    (
        "remove baked literals from ",
        "no_typed_in_values",
        "A file the writer wrote holds values typed in for one case instead of reading them from the data.",
        ": ",
        FILE,
    ),
    (
        "remove copied brief from ",
        "no_copied_brief",
        "A file the writer wrote copies text from the brief instead of building the feature.",
        ": ",
        FILE,
    ),
    (
        "FACTORY owes ",
        "factory_file",
        "A file the Factory wrote has a problem. The writer is not charged for it.",
        " (",
        FILE,
    ),
)

FALLBACK = (
    "",
    "grader_finding",
    "The quality check named a problem.",
    "",
    NONE,
)

#: The sentence for a part the grader could not check (owner ``RUN``).
NOT_CHECKED = "The quality check could not check this part, so it cannot pass the build."

#: Rows kept per verdict on the wire and in one log burst.
MAX_ROWS = 40

#: Characters of the grader's own words kept on one log line.
QUOTE_CHARS = 300

#: Characters of a place kept for the card face.
PLACE_CHARS = 120


def _shape(item: str) -> Tuple[str, str, str, str, str]:
    for shape in ITEM_SHAPES:
        if item.startswith(shape[0]):
            return shape
    return FALLBACK


def finding_row(item: str, owner: str) -> Dict[str, str]:
    """One grader item as ``{rule, what, where, place, owner, quote}``.

    ``where`` is the grader's own locator (machine text); ``place`` is the
    same locator in plain words for the card face, or empty for a file."""
    text = str(item or "").strip()
    prefix, rule, what, sep, names = _shape(text)
    rest = text[len(prefix):]
    where = ((rest.split(sep, 1)[0] if sep else rest).strip() or "-") if prefix else "-"
    if owner == RUN and rule == FALLBACK[1]:
        rule, what = "not_checked", NOT_CHECKED
    return {
        "rule": rule,
        "what": what,
        "where": where,
        "place": plain_place(where, names),
        "owner": owner,
        "quote": text,
    }


def plain_place(where: str, names: str) -> str:
    """``where`` as the card face may show it: a capability id as words, a
    step or a question as written, nothing for a file path or no locator."""
    if where == "-" or names in (FILE, NONE):
        return ""
    if names == CAPABILITY:
        return where.replace("_", " ").replace("-", " ").strip()[:PLACE_CHARS]
    return where[:PLACE_CHARS]


def finding_rows(verdict: Any) -> List[Dict[str, str]]:
    """Every item of a verdict as a row, in the verdict's own order."""
    writer = {str(i) for i in (getattr(verdict, "writer_items", ()) or ())}
    factory = {str(i) for i in (getattr(verdict, "factory_owed", ()) or ())}
    rows: List[Dict[str, str]] = []
    for item in getattr(verdict, "items", ()) or ():
        text = str(item)
        owner = WRITER if text in writer else FACTORY if text in factory else RUN
        rows.append(finding_row(text, owner))
    return rows[:MAX_ROWS]


def log_line(row: Mapping[str, str]) -> str:
    """One finding as one log line: what, where, which rule, who owns it, and
    the grader's own words (redacted, capped)."""
    from app.factory.build.sanitize import sanitize_for_status

    quote = sanitize_for_status(str(row.get("quote") or ""))[:QUOTE_CHARS]
    return (
        f"rule={row.get('rule')} where={row.get('where')} owner={row.get('owner')} "
        f"to_writer={'yes' if row.get('owner') == WRITER else 'no'} what={row.get('what')} "
        f"quote={quote}"
    )


def log_verdict(session_id: str, tier: str, rows: Sequence[Mapping[str, str]]) -> None:
    """The verdict and each of its findings, one line each, with the session id
    on every line, so a log filter on the session shows what the grader asked
    for and what goes back to the writer."""
    sid = str(session_id or "-")
    to_writer = sum(1 for r in rows if r.get("owner") == WRITER)
    logger.info(
        "grader verdict session=%s tier=%s findings=%d to_writer=%d",
        sid,
        tier,
        len(rows),
        to_writer,
    )
    for n, row in enumerate(rows, 1):
        logger.info("grader finding session=%s %d/%d %s", sid, n, len(rows), log_line(row))


def headline(state: str, rows: Sequence[Mapping[str, str]], sent_to_writer: int) -> str:
    """The one sentence above the findings on the build card, from the grader
    view's own state and counts. Empty when there is nothing to show."""
    if not rows or state == "certified":
        return ""
    n = len(rows)
    found = f"{n} problem{'s' if n != 1 else ''}"
    if state == "sent_back":
        back = f"{sent_to_writer} of them" if sent_to_writer != n else ("it" if n == 1 else "them")
        return f"The quality check found {found} and sent {back} back to the writer to fix:"
    return f"The quality check did not pass the build. It found {found}:"
