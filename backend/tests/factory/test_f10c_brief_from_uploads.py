"""F-10c-a: uploaded documents are folded into the draft brief, cited as
document:<filename>, so what gets drafted (and built) reflects them. A draft
with NO uploaded documents is byte-identical to before. Full-dotted import so
the mutation gate selects this module.
"""
from __future__ import annotations

import pytest

import app.factory.platform_chat_flow as pcf  # noqa: F401  dotted token for CI-M
from app.factory.platform_chat_flow import draft_from_chat, uploaded_docs_digest
from app.models.session import SessionState


def _state(chunks, names):
    s = SessionState(session_id="sess_f10c", user_id="u1")
    s.chunks = chunks
    s.source_files = names
    return s


@pytest.mark.parametrize(
    "chunks, names, has_doc",
    [
        ([], [], False),
        (
            ["Time for Completion is 730 days.", "LD rate is 0.1% per day."],
            ["contract.pdf", "contract.pdf"],
            True,
        ),
    ],
)
def test_uploaded_docs_digest_labels_by_filename_or_is_empty(chunks, names, has_doc):
    digest = uploaded_docs_digest(_state(chunks, names))
    if has_doc:
        assert "document:contract.pdf" in digest
        assert "Time for Completion is 730 days." in digest
    else:
        assert digest == ""  # no upload -> no digest (draft stays byte-identical)


@pytest.mark.parametrize(
    "chunks, names, augmented",
    [
        ([], [], False),
        (["Accepted Contract Amount is SAR 39,098,392.98."], ["boq.xlsx"], True),
    ],
)
def test_draft_from_chat_folds_uploads_and_is_byte_identical_without_them(
    chunks, names, augmented
):
    msg = "Build a contracts administration platform for a civil works project"
    s = _state(chunks, names)
    draft_from_chat(s, msg)
    brief = s.product_design.brief
    if augmented:
        assert brief.startswith(msg)
        assert "document:boq.xlsx" in brief
        assert "Accepted Contract Amount is SAR 39,098,392.98." in brief
    else:
        assert brief == msg  # byte-identical with no uploaded documents
