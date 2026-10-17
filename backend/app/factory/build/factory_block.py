"""The one way a Factory stamp may touch a file the product also writes.

Rule (owner, 2026-10-07): a Factory stamp NEVER re-renders a product-authored
file. Factory content lives in Factory-owned files; where a file is
unavoidably shared (``app/jobs.py``'s kernel roster, ``requirements.txt``),
the stamp edits ONLY its marked block -- explicit begin/end lines rendered
from the constants below -- and every other byte of the file is left exactly
as the product wrote it.

Live origin: #678's roster stamp re-rendered ``app/jobs.py`` whole and wrote
``CAPABILITIES = []`` over a manifest the product computed from its models;
every capability route vanished (9de69276 vineyard repro, all 8 POSTs 404).
"""

from __future__ import annotations

from typing import Optional, Tuple

#: The marker text; the comment prefix is the file's own (``#`` for Python and
#: requirements files).
BLOCK_BEGIN = "--- BEGIN factory block: stamped by the Factory, edits here are replaced ---"
BLOCK_END = "--- END factory block ---"


def begin_line(comment: str = "#") -> str:
    return f"{comment} {BLOCK_BEGIN}"


def end_line(comment: str = "#") -> str:
    return f"{comment} {BLOCK_END}"


def split_block(text: str, comment: str = "#") -> Tuple[str, Optional[str], str]:
    """``(before, block_body_or_None, after)``; byte-exact outside the block."""
    begin, end = begin_line(comment), end_line(comment)
    start = text.find(begin)
    if start < 0:
        return text, None, ""
    body_start = text.find("\n", start)
    stop = text.find(end, start)
    if body_start < 0 or stop < 0:
        return text, None, ""
    after_start = stop + len(end)
    if text[after_start:after_start + 1] == "\n":
        after_start += 1
    return text[:start], text[body_start + 1:stop], text[after_start:]


def outside(text: str, comment: str = "#") -> str:
    """Every byte of ``text`` that is not the Factory block."""
    before, _body, after = split_block(text, comment)
    return before + after


def apply_block(text: str, body: str, comment: str = "#") -> str:
    """``text`` with its Factory block set to ``body``; nothing else changes.

    A file without a block gets one appended directly after the product's
    last line, so every product byte stays where it was. (A product file
    whose last line has no newline gets one: the marker must start a line --
    the only byte the block ever adds outside itself.) Idempotent: the same
    ``body`` twice yields the same bytes.
    """
    body = body if body.endswith("\n") or not body else body + "\n"
    block = begin_line(comment) + "\n" + body + end_line(comment) + "\n"
    before, existing, after = split_block(text, comment)
    if existing is not None:
        return before + block + after
    if not text or text.endswith("\n"):
        return text + block
    return text + "\n" + block
