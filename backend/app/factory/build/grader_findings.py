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
* Every item is redacted before any part of its row is cut from it, so no
  row field (ledger, log or card) can hold a key or a piece of one. The one
  part kept whole is a feature slot holding an id the verdict's own grade
  names: that id is the Factory's own name for a feature, not a secret, and
  the redaction rules (long runs of letters) must not erase it.
"""

from __future__ import annotations

import logging
from typing import Any, Collection, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: The grader's tier for "could not grade" (``grader.ERROR``; drift-tested).
ERROR_TIER = "ERROR"

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
        " -- ",
        CAPABILITY,
    ),
    (
        "UI step FAIL: ",
        "screen_walk",
        "This screen step did not work when the quality check walked through the app.",
        " -- ",
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

#: The sentence for an item in a feature slot that names no feature the
#: verdict graded (the grader's own words about the features together) when it
#: is billed to the writer: the product is at fault, not the check.
NOT_ONE_FEATURE = "The features did not pass the quality check as a whole."

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


def finding_row(item: str, owner: str, features: Optional[Collection[str]] = None) -> Dict[str, str]:
    """One grader item as ``{rule, what, where, place, owner, quote}``.

    ``where`` is the grader's own locator (machine text); ``place`` is the
    same locator in plain words for the card face, or empty for a file.
    ``features`` are the capability ids the verdict graded: a feature slot
    names a feature only when it holds one of them. Without that list (a
    verdict that carries no grade) a slot holding no whitespace is an id."""
    from app.factory.build.sanitize import sanitize_for_status

    text = _redacted(str(item or "").strip(), features, sanitize_for_status)
    prefix, rule, what, sep, names = _shape(text)
    rest = text[len(prefix):]
    where = ((rest.split(sep, 1)[0] if sep else rest).strip() or "-") if prefix else "-"
    if owner == RUN and rule == FALLBACK[1]:
        rule, what = "not_checked", NOT_CHECKED
    elif names == CAPABILITY and where != "-" and not _names_feature(where, features):
        # The grader's own words in a feature slot ("too few gradeable
        # functions: ...") name no single feature. Whose it is decides the
        # sentence: the writer's is a fault of the product, else the check's.
        what = NOT_CHECKED if owner == RUN else NOT_ONE_FEATURE
        names = QUESTION
    return {
        "rule": rule,
        "what": what,
        "where": where,
        "place": plain_place(where, names),
        "owner": owner,
        "quote": text,
    }


def _redacted(raw: str, features: Optional[Collection[str]], sanitize: Any) -> str:
    """``raw`` redacted, except a feature slot holding one of ``features``
    (the ids the verdict's grade names), which is kept whole; the words
    after the slot are redacted as usual."""
    prefix, _rule, _what, sep, names = _shape(raw)
    if prefix and names == CAPABILITY and features:
        head, cut, tail = raw[len(prefix):].partition(sep) if sep else (raw[len(prefix):], "", "")
        if head.strip() in features:
            return prefix + head + cut + sanitize(tail)
    return sanitize(raw)


def names_one_feature(row: Mapping[str, Any]) -> bool:
    """A row names one feature when its quote has a feature-slot shape and
    its sentence is still that shape's own: :func:`finding_row` replaces the
    sentence whenever the slot names no feature the verdict graded."""
    prefix, _rule, what, _sep, names = _shape(str(row.get("quote") or ""))
    return bool(prefix) and names == CAPABILITY and row.get("what") == what


def redact_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """``row`` with every string redacted again (a row read back from the
    ledger is redacted on every read), keeping whole the feature id that
    :func:`finding_row` already judged to be one: in ``where``, in the
    ``place`` the card shows (derived again from that ``where``, never read
    from the stored row) and in the slot of ``quote``. Every other word, the
    rest of the quote included, is redacted as usual."""
    from app.factory.build.sanitize import sanitize_for_status

    out: Dict[str, Any] = {
        key: sanitize_for_status(value) if isinstance(value, str) else value for key, value in row.items()
    }
    if names_one_feature(row):
        where = str(row.get("where") or "")
        out["where"] = where
        out["place"] = plain_place(where, CAPABILITY)
        out["quote"] = _redacted(str(row.get("quote") or ""), {where}, sanitize_for_status)
    return out


def _names_feature(where: str, features: Optional[Collection[str]]) -> bool:
    if features is not None:
        return where in features
    return not any(ch.isspace() for ch in where)


def plain_place(where: str, names: str) -> str:
    """``where`` as the card face may show it: a capability id as words, a
    step or a question in words, nothing for a file path or for no locator.
    A long place is cut at a word and says so."""
    if where == "-" or names in (FILE, NONE):
        return ""
    if names == CAPABILITY:
        return _cut(_words(where))
    if names == STEP:
        return _cut(step_place(where))
    return _cut(plain_words(where))


def _cut(text: str, limit: int = PLACE_CHARS) -> str:
    """``text`` within ``limit`` characters, cut at a word with an ellipsis,
    never inside a word."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    if text[limit - 1] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;.-") + ELLIPSIS


#: The mark a cut place ends with.
ELLIPSIS = "\u2026"


def _words(ident: str) -> str:
    """An id as words: every separator (``_``, ``-``, ``.`` ...) is a space."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in ident).split())


def _machine_token(bare: str) -> bool:
    """A file path or a dotted name (``app.py``, ``rates.csv``,
    ``contract.value``): machine text, never card words. A decimal number
    (``2.5``) is not."""
    if "/" in bare or "\\" in bare:
        return True
    inner = bare.strip(".,;:!?()")
    return any(
        ch == "." and (inner[i - 1].isalpha() or inner[i + 1].isalpha())
        for i, ch in enumerate(inner)
        if 0 < i < len(inner) - 1
    )


def plain_words(text: str) -> str:
    """The grader's prose for the card face: a quoted id reads as words, a
    file path or dotted name is left out, and no ``_`` or ``:`` separator
    remains."""
    kept: List[str] = []
    for token in text.split():
        bare = token.strip("'\"`")
        if _machine_token(bare):
            continue
        kept.append(bare.replace("_", " ").replace(":", ","))
    return " ".join(" ".join(kept).split())


#: How the card names a UI step, by the step kind the UI grader writes before
#: the colon of its step id (``<kind>:<target>``). The kinds are the grader's
#: own fixed vocabulary; ``{}`` is the target in words. A kind not listed
#: reads as its target alone.
STEP_WORDS: Dict[str, str] = {
    "load": "opening the app",
    "bind": "the account check on the first page",
    "attach": "attaching a {} file",
    "parsed": "reading an attached file",
    "honest_error": "the error message of the {} feature",
    "screen": "the {} screen",
    "route": "the {} feature",
}


def step_place(step: str) -> str:
    """A UI step id (``screen:command_center``, ``route:/v1/delay_damages``)
    in words: the step kind's frame around the target's last path segment.
    Never a raw id, a path or a separator."""
    kind, sep, target = step.partition(":")
    if not sep:
        return _words(step)
    parts = [part for part in target.split("/") if part.strip("#")]
    leaf = _words(parts[-1]) if parts else ""
    frame = STEP_WORDS.get(kind.strip())
    if frame is None:
        return leaf or _words(kind)
    if "{}" in frame and not leaf:
        return ""
    return " ".join(frame.format(leaf).split())


def finding_rows(verdict: Any) -> List[Dict[str, str]]:
    """Every item of a verdict as a row, in the verdict's own order."""
    writer = {str(i) for i in (getattr(verdict, "writer_items", ()) or ())}
    factory = {str(i) for i in (getattr(verdict, "factory_owed", ()) or ())}
    features = graded_features(getattr(verdict, "grade", None))
    rows: List[Dict[str, str]] = []
    for item in getattr(verdict, "items", ()) or ():
        text = str(item)
        owner = WRITER if text in writer else FACTORY if text in factory else RUN
        rows.append(finding_row(text, owner, features))
    return rows[:MAX_ROWS]


def graded_features(grade: Any) -> Optional[FrozenSet[str]]:
    """The capability ids a grade record names (the function grade's graded
    and ungraded functions, and the hardwiring check's functions), or None
    when there is no grade record to read them from."""
    if not isinstance(grade, Mapping):
        return None
    functions = (grade.get("layer1") or {}).get("functions") or {}
    ids = {
        str(fn.get("capability_id"))
        for key in ("functions", "ungraded")
        for fn in functions.get(key) or ()
        if isinstance(fn, Mapping) and fn.get("capability_id")
    }
    ids.update(
        str(f.get("capability"))
        for f in (grade.get("hardwiring") or {}).get("findings") or ()
        if isinstance(f, Mapping) and f.get("kind") == "function" and f.get("capability")
    )
    return frozenset(ids)


def one_line(text: str) -> str:
    """Text on one line: every run of whitespace, line breaks included, is
    one space, so a multi-line quote stays on the line that names its session."""
    return " ".join(text.split())


def log_line(row: Mapping[str, str]) -> str:
    """One finding as one log line: what, where, which rule, who owns it, and
    the grader's own words. Everything the grader wrote is redacted; the quote
    is capped. Whether it went back to the writer is logged where that is
    decided (:func:`log_handback`), never guessed here."""
    clean = redact_row(row)
    quote = one_line(clean["quote"])[:QUOTE_CHARS]
    where = one_line(clean["where"])[:QUOTE_CHARS]
    return (
        f"rule={row.get('rule')} where={where} owner={row.get('owner')} "
        f"what={row.get('what')} quote={quote}"
    )


def log_verdict(session_id: str, tier: str, rows: Sequence[Mapping[str, str]]) -> None:
    """The verdict and each of its findings, one line each, with the session id
    on every line, so a log filter on the session shows what the grader asked
    for. ``writer_owned`` counts the rows that are the writer's to fix; what
    is actually sent back is logged by :func:`log_handback`."""
    sid = str(session_id or "-")
    owned = sum(1 for r in rows if r.get("owner") == WRITER)
    logger.info(
        "grader verdict session=%s tier=%s findings=%d writer_owned=%d",
        sid,
        tier,
        len(rows),
        owned,
    )
    for n, row in enumerate(rows, 1):
        logger.info("grader finding session=%s %d/%d %s", sid, n, len(rows), log_line(row))


def log_handback(session_id: str, sent: int, round_no: int, budget: int) -> None:
    """The writer was handed ``sent`` findings to fix, in round ``round_no`` of
    ``budget``: the one line that says work went back."""
    logger.info(
        "grader handback session=%s to_writer=%d round=%d/%d",
        str(session_id or "-"),
        sent,
        round_no,
        budget,
    )


def log_stop(session_id: str, reason: str) -> None:
    """The verdict ended the run: nothing goes back to the writer."""
    logger.info("grader stop session=%s to_writer=0 reason=%s", str(session_id or "-"), reason)


def headline(
    state: str, rows: Sequence[Mapping[str, str]], sent_to_writer: int, tier: str = "", found: int = 0
) -> str:
    """The one sentence above the findings on the build card, from the grader
    view's own state, tier and counts. Empty when there is nothing to show.

    ``found`` is how many items the verdict holds; the card lists at most
    MAX_ROWS of them, so the count is the verdict's, never the list's, and a
    shortened list says it is shortened."""
    if not rows or state == "certified":
        return ""
    if tier == ERROR_TIER:
        return "The quality check could not finish, so it cannot pass the build:"
    n = max(found, len(rows))
    problems = f"{n} problem{'s' if n != 1 else ''}"
    listed = f". The first {len(rows)} are listed:" if n > len(rows) else ":"
    if state == "sent_back":
        back = f"{sent_to_writer} of them" if sent_to_writer != n else ("it" if n == 1 else "them")
        return f"The quality check found {problems} and sent {back} back to the writer to fix{listed}"
    return f"The quality check did not pass the build. It found {problems}{listed}"
