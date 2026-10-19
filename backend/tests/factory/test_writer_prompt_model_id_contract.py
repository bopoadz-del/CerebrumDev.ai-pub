"""The writer prompt must declare the model id / response-envelope contract.

A generated product's own tests POST a record and GET it back, reading an
``id`` off the response. When the writer's ``to_dict()`` omitted ``id`` the
product contradicted its own suite -- the GET found no identifier and the
capability reddened, though nothing had actually refused. The cure is upstream
and general: the WRITER prompt must state, for every product, that each
model's ``to_dict()`` exposes an ``id`` (``None`` until stored) and that the
capability response envelope is ``{ok, record, id}``.

This asserts the Factory's rendered writer prompt now carries that contract.
General: nothing here is keyed to any product, brief, queue block, PM, or
test name. FAILS on master (the prompt never stated it); PASSES on the branch.
"""

from __future__ import annotations

from app.factory.build import writer_prompt


class _SyntheticBlueprint:
    """A content-free blueprint: the contract under test is not product-specific."""

    product_id = "p-synthetic"
    product_name = "Synthetic Product"
    vertical = "generic"
    summary = "synthetic blueprint for a general contract check"
    capabilities = ()
    entities = ()


def _rendered() -> str:
    return writer_prompt.render_writer_prompt(
        _SyntheticBlueprint(), brief="synthetic brief"
    )


def test_writer_prompt_requires_model_to_dict_id():
    text = _rendered()
    assert "to_dict" in text
    # Every model's to_dict() must expose id, None until the record is stored.
    assert "``to_dict()`` MUST expose" in text, (
        "writer prompt does not require to_dict() to expose id"
    )
    assert "``None`` until the record is" in text, (
        "writer prompt does not state id is None until the record is stored"
    )


def test_writer_prompt_declares_response_envelope():
    text = _rendered()
    # The capability response envelope is {ok, record, id}.
    assert "{ok, record, id}" in text, (
        "writer prompt does not declare the {ok, record, id} response envelope"
    )
