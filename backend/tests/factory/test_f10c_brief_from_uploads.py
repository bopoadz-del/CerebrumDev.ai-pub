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


@pytest.mark.parametrize(
    "chunks, names, exp_chunks, exp_folded",
    [
        ([], [], 0, False),
        (["Time for Completion is 730 days.", "LD rate is 0.1%."],
         ["contract.pdf", "contract.pdf"], 2, True),
    ],
)
def test_draft_result_carries_brief_diag(chunks, names, exp_chunks, exp_folded):
    # ORDERS_1735Z trace: the draft result reports the state the drafter saw —
    # source_files it carried and whether the digest folded — so a live cited=[]
    # is diagnosable from the artifact, not guessed.
    s = _state(chunks, names)
    s.loaded_from = "memory"  # discriminator (ORDERS_1810Z): must pass through
    res = draft_from_chat(s, "Build a contracts platform")
    diag = res["_brief_diag"]
    assert diag["chunks"] == exp_chunks
    assert diag["source_files"] == names[:8]
    assert diag["source_files_count"] == len(names)
    assert diag["digest_folded"] is exp_folded
    assert diag["loaded_from"] == "memory"  # kills drop/const on the discriminator
    assert ":" in diag["worker"]  # host:pid of the drafting process
