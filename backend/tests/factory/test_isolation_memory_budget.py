"""The child's memory ceiling must be a BUDGET, not an absolute number.

Regression for a bug that reached production and silently emptied every
document >= 1 MB (The_Fork, after isolation shipped with an absolute
RLIMIT_AS of 1536 MB).

``RLIMIT_AS`` caps VIRTUAL address space, and ``fork()`` gives the child a
copy of the parent's whole mapping. With torch and the embedding model
resident the parent's VmSize is already multiple GB, so an ABSOLUTE ceiling
of 1536 MB was already breached at child start. The first allocation raised
MemoryError; extraction swallowed it as a successful empty document.

A follow-up sent ``("nolimit", str(exc))`` then ``("ok", result)`` on the
same pipe. The parent reads ONE message and would treat ``nolimit`` as
failure. Rlimit refusal must log and continue — never send a status message.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.cerebrum_product_kernel import isolation


ROOT = Path(__file__).resolve().parents[3]


def test_limit_is_relative_to_the_parent_not_absolute():
    """The ceiling must exceed what the child already inherits."""
    parent = isolation._parent_virtual_bytes()
    if parent is None:
        pytest.skip(reason="/proc/self/status unavailable (non-Linux)")

    budget = 1536 * 1024 * 1024
    limit = isolation.child_address_space_limit(budget)

    assert limit is not None
    assert limit > parent, (
        "limit must sit ABOVE the parent's inherited virtual size; the "
        "absolute 1536MB ceiling was below it, so the child died instantly"
    )
    assert limit == parent + budget


def test_budget_maths_holds_without_proc(monkeypatch):
    """Platform-independent version of the assertion above.

    The /proc test SKIPS on Windows, which is where an absolute-ceiling bug
    can pass review because the fork path never runs. Pin the arithmetic
    everywhere by injecting a realistic parent size.

    3.5 GB is representative: torch plus the embedding model resident.
    """
    parent = 3500 * 1024 * 1024
    budget = 1536 * 1024 * 1024
    monkeypatch.setattr(isolation, "_parent_virtual_bytes", lambda: parent)

    limit = isolation.child_address_space_limit(budget)

    assert limit == parent + budget
    assert limit > parent, "the shipped bug: ceiling below what the child inherits"
    assert budget < parent


def test_no_limit_rather_than_an_impossible_one():
    """When the parent's size is unknown, set no limit at all."""
    real = isolation._parent_virtual_bytes
    isolation._parent_virtual_bytes = lambda: None
    try:
        assert isolation.child_address_space_limit(123) is None
    finally:
        isolation._parent_virtual_bytes = real


@pytest.mark.skipif(os.name != "posix", reason="reads /proc")
def test_parent_virtual_size_is_plausible():
    """If VmSize ever reads as tiny, the budget maths is meaningless."""
    parent = isolation._parent_virtual_bytes()
    if parent is None:
        pytest.skip(reason="/proc/self/status unavailable")
    assert parent > 16 * 1024 * 1024, f"implausible VmSize: {parent}"


class _RecordingConn:
    def __init__(self) -> None:
        self.sent: list = []
        self.closed = False

    def send(self, payload) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True


def test_setrlimit_failure_does_not_send_a_status_message(monkeypatch):
    """Transcript bug: rlimit refusal must not become the parent's result.

    The broken child sent ``("nolimit", str(exc))`` then later ``("ok",
    result)``. The parent reads one message and would treat ``nolimit`` as
    failure. Prove: exactly one message, and it is the function result.
    """
    resource = pytest.importorskip("resource")

    parent = 3500 * 1024 * 1024
    budget = 1536 * 1024 * 1024
    monkeypatch.setattr(isolation, "_parent_virtual_bytes", lambda: parent)

    def _boom(*_a, **_k):
        raise OSError("operation not permitted")

    monkeypatch.setattr(resource, "setrlimit", _boom)

    conn = _RecordingConn()
    isolation._child_work(conn, lambda: "extracted-text", (), budget)

    assert conn.sent == [("ok", "extracted-text")], (
        "rlimit refusal must not send a status; the parent would take it as "
        f"the result. got {conn.sent!r}"
    )
    assert len(conn.sent) == 1
    assert conn.sent[0][0] != "nolimit"


def test_setrlimit_failure_still_runs_the_function(monkeypatch, caplog):
    """apply_child_address_space_budget logs and returns None; it does not raise."""
    resource = pytest.importorskip("resource")
    monkeypatch.setattr(isolation, "_parent_virtual_bytes", lambda: 1024 * 1024 * 1024)
    monkeypatch.setattr(
        resource,
        "setrlimit",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("current limit exceeds new limit")),
    )
    with caplog.at_level("WARNING"):
        applied = isolation.apply_child_address_space_budget(1536 * 1024 * 1024)
    assert applied is None
    assert any("continuing without it" in r.message for r in caplog.records)


def test_child_sends_memory_status_only_for_memoryerror():
    conn = _RecordingConn()
    monkeypatch_parent_none = isolation._parent_virtual_bytes

    isolation._parent_virtual_bytes = lambda: None
    try:

        def _boom():
            raise MemoryError("injected")

        isolation._child_work(conn, _boom, (), 1024)
    finally:
        isolation._parent_virtual_bytes = monkeypatch_parent_none

    assert conn.sent == [("memory", None)]
    assert len(conn.sent) == 1


def test_generated_kernel_ships_the_budget_form(tmp_path, monkeypatch):
    """Factory vendors this module so products cannot re-ship an absolute ceiling."""
    from app.factory.blueprint import load_blueprint
    from app.factory.generator import ProductGenerator

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    bp = load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    out = tmp_path / "product"
    ProductGenerator(
        bp, blocks_root=None, factory_commit="test", blocks_commit="test"
    ).generate(out)

    iso = out / "app" / "cerebrum_product_kernel" / "isolation.py"
    assert iso.is_file(), "kernel isolation helper must be vendored into products"
    text = iso.read_text(encoding="utf-8")
    assert "def child_address_space_limit" in text
    assert "parent + budget_bytes" in text
    assert "continuing without it" in text
    # The budget form, not a bare absolute cap as the rlimit tuple.
    assert "RLIMIT_AS), (budget" not in text
    assert "setrlimit(resource.RLIMIT_AS, (budget_bytes" not in text


def test_workbench_sandbox_uses_budget_preexec(monkeypatch, tmp_path):
    """Live host isolation point: sandbox commands get the budget preexec, not an absolute cap."""
    import subprocess as sp

    from app.workbench.sandbox import WorkbenchSandbox

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    recorded: dict = {}

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def _fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded.update(kwargs)
        return _Proc()

    monkeypatch.setattr(sp, "run", _fake_run)
    sandbox = WorkbenchSandbox("mem-budget")
    result = sandbox.run_command(["/bin/true"])
    assert result["returncode"] == 0
    if os.name == "posix":
        assert recorded.get("preexec_fn") is not None
        assert recorded["preexec_fn"].__name__ == "_preexec"
    meta = sandbox.persist_meta()
    assert "parent VmSize" in meta["memory_isolation"]
    assert "no limit if parent size unknown" in meta["memory_isolation"]
