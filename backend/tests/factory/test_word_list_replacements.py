"""Each former word list is replaced by the mechanism it approximated.

One test per replacement, with invented names, so a list can never quietly
come back: if the words decided again, these would pass for the wrong
reason or fail.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.factory.build import acceptance_floor as floor
from app.factory.build.block_inputs import align_spec_to_handler_source
from app.factory.build.level_grade import _cli_honesty_miss
from app.factory.build.supply_chain import _text_has_outbound


def _cap(cid: str, description: str) -> SimpleNamespace:
    return SimpleNamespace(id=cid, description=description, block_ids=[cid])


# _RETRIEVAL_HINTS -> the Store's typed read declaration (database, vector)


def test_retrieval_is_the_bound_blocks_declared_vector_read_never_words(
    tmp_path, monkeypatch
):
    import json

    for bid, scope in (("zorblat_index", "vector"), ("quux_rows", "rows")):
        path = tmp_path / "block_registry" / bid / "block.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"id": bid, "reads": [{"kind": "database", "scope": scope}]}),
            encoding="utf-8",
        )
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path))

    def brief(cid, words, block):
        cap = SimpleNamespace(id=cid, description=words, block_ids=[block])
        return SimpleNamespace(rigor="", summary=words, capabilities=[cap], connectors=[])

    plain = brief("frob", "keeps a list", "zorblat_index")
    wordy = brief("rag_search", "semantic vector retrieval over a knowledge corpus", "quux_rows")
    assert "retrieval" in floor.brief_signals(plain)
    assert "rag_roundtrip_hit" in floor.enforced_ids(plain)  # a veto, not advisory
    assert "retrieval" not in floor.brief_signals(wordy)


# inline "./" -> path structure


def test_a_rendered_path_is_matched_by_its_segments_wherever_it_is_mounted():
    rendered = frozenset({"app/zorblat.py"})
    assert floor._factory_rendered("./app/zorblat.py", rendered)
    assert floor._factory_rendered("/srv/quux/app/zorblat.py", rendered)
    assert not floor._factory_rendered("zorblat.py", rendered)  # bare basename
    assert not floor._factory_rendered("lib/app/zorblat.py", rendered)  # relative, deeper


# _ROSTER_CONSULTS_PAYLOAD -> removed: a roster in code declares no field;
# required fields come only from the declared schema (owner, 2026-10-05).


def test_a_roster_in_code_declares_no_field_whether_or_not_it_consults_the_payload():
    consults = (
        'FROBS = ["zorb_ref", "quux_id"]\n'
        "def handle(payload):\n"
        "    for name in FROBS:\n"
        "        if name not in payload:\n"
        "            raise ValueError('needs ' + name)\n"
    )
    words_only = (
        'FROBS = ["zorb_ref", "quux_id"]\n'
        "def handle(payload):\n"
        "    for name in FROBS:\n"
        "        log('missing not in payload .get(')\n"
    )
    for source in (consults, words_only):
        spec, changed = align_spec_to_handler_source({"fields": []}, source)
        assert spec["fields"] == [] and changed == []


# CLI_FOUNDING_HONESTY_MISS -> the receipt's typed blocker field


def test_any_named_receipt_blocker_is_a_miss_and_prose_is_not_read():
    named = {"coder_receipt": {"ok": True, "blocker": "ZORBLAT_NEW_BLOCKER"}}
    assert _cli_honesty_miss(named) == "ZORBLAT_NEW_BLOCKER"
    prose = {"coder_receipt": {"ok": True, "detail": "FACTORY_CODE_CLI_BILLING in words"}}
    assert _cli_honesty_miss(prose) is None


# _OUTBOUND_MARKERS -> outbound URL structure


def test_outbound_is_a_non_loopback_url_whatever_tool_or_host_names_it():
    assert _text_has_outbound("RUN zorbfetch https://quux.example/payload")
    assert not _text_has_outbound("RUN curl -fsS http://127.0.0.1:8000/health")
    assert not _text_has_outbound("# see https://quux.example for docs")
