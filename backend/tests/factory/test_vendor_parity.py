"""CD-PARITY-1: vendor_blocks_mirror must match blocks.lock.json hashes.

The lock is generated with ``app.factory.blocks_lock.block_content_hash``
(#401). This suite uses that same function via ``scripts/verify_vendor_parity.py``.
A second hash is forbidden.

B04: a lock entry with no ``vendor_blocks_mirror/<id>/`` is a hard fail.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


from app.factory.blocks_lock import block_content_hash

ROOT = Path(__file__).resolve().parents[3]
VERIFY_SCRIPT = ROOT / "scripts" / "verify_vendor_parity.py"
MIRROR = ROOT / "backend" / "app" / "factory" / "vendor_blocks_mirror"
LOCK_PATH = ROOT / "blocks.lock.json"


def _load_verify():
    spec = importlib.util.spec_from_file_location("verify_vendor_parity", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lock_for(tmp_path: Path, blocks: Dict[str, Any]) -> Path:
    dest = tmp_path / "blocks.lock.json"
    dest.write_text(
        json.dumps(
            {
                "schema": "factory.blocks.lock.v1",
                "store": {
                    "repo": "https://github.com/bopoadz-del/Cerebrum-Blocks",
                    "sha": "a372e769e05e47dc4dbc274f7d83eebf29a836f1",
                },
                "blocks": blocks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return dest


def _write_block(root: Path, block_id: str, body: str, extra: Dict[str, str] | None = None) -> Path:
    d = root / block_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "block.py").write_text(body, encoding="utf-8")
    (d / "block.json").write_text(
        json.dumps({"id": block_id, "version": "1.0.0"}) + "\n", encoding="utf-8"
    )
    for name, text in (extra or {}).items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def test_fixture_lock_plus_stubbed_mirror_fails(tmp_path: Path) -> None:
    """Failing-first: a stub mirror cannot match a lock hash of real bytes."""
    verify = _load_verify()
    real = tmp_path / "real"
    stub = tmp_path / "mirror"
    source = _write_block(real, "document_engine", "VALUE = 'store'\n", {"Dockerfile": "FROM x\n"})
    _write_block(stub, "document_engine", "VALUE = 'stub'\n")
    pinned = block_content_hash(source)
    assert block_content_hash(stub / "document_engine") != pinned
    lock = _lock_for(
        tmp_path,
        {
            "document_engine": {
                "id": "document_engine",
                "version": "1.0.0",
                "content_hash": pinned,
                "source": "cerebrum-blocks",
            }
        },
    )
    rows, ok = verify.compare(lock_path=lock, mirror_root=stub)
    assert ok is False
    assert len(rows) == 1
    assert rows[0]["id"] == "document_engine"
    assert rows[0]["match"] is False
    assert rows[0]["pinned_hash"] == pinned


def test_matching_lock_and_mirror_pass(tmp_path: Path) -> None:
    verify = _load_verify()
    mirror = tmp_path / "mirror"
    source = _write_block(mirror, "audit", "PIN = 1\n", {"Dockerfile": "FROM base\n"})
    pinned = block_content_hash(source)
    lock = _lock_for(
        tmp_path,
        {
            "audit": {
                "id": "audit",
                "version": "1.0.0",
                "content_hash": pinned,
                "source": "cerebrum-blocks",
            }
        },
    )
    rows, ok = verify.compare(lock_path=lock, mirror_root=mirror)
    assert ok is True
    assert rows[0]["match"] is True
    assert rows[0]["pinned_hash"] == rows[0]["mirror_hash"] == pinned
    assert rows[0]["files_mirror"] == 3


def test_lock_entry_with_no_mirror_dir_fails_b04(tmp_path: Path) -> None:
    """B04: lock names a block the mirror does not have."""
    verify = _load_verify()
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    lock = _lock_for(
        tmp_path,
        {
            "file_hasher": {
                "id": "file_hasher",
                "version": "1.0.0",
                "content_hash": "sha256:" + ("ab" * 32),
                "source": "cerebrum-blocks",
            }
        },
    )
    rows, ok = verify.compare(lock_path=lock, mirror_root=mirror)
    assert ok is False
    assert rows[0]["id"] == "file_hasher"
    assert rows[0]["match"] is False
    assert rows[0]["mirror_hash"] == "MISSING"
    assert rows[0]["files_mirror"] == 0
    rc = verify.main(["--lock", str(lock), "--mirror", str(mirror)])
    assert rc == 1


def _install_universal_block_stub() -> None:
    """Store UniversalBlock is not in this repo; the class file is in the mirror."""
    import types

    core = sys.modules.get("app.core")
    if core is None:
        core = types.ModuleType("app.core")
        sys.modules["app.core"] = core
    ub = types.ModuleType("app.core.universal_base")

    class UniversalBlock:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.config = dict(getattr(self, "default_config", {}) or {})

        def get_dep(self, name: str) -> Any:
            return None

        async def execute(self, input_data: Any, params: Any = None) -> Dict[str, Any]:
            process = getattr(self, "process", None)
            if process is not None:
                return await process(input_data, params or {})
            return {"status": "ok", "result": input_data}

    ub.UniversalBlock = UniversalBlock
    sys.modules["app.core.universal_base"] = ub


def _load_mirror_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"missing {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mirror_hashed_files_are_not_gitignored() -> None:
    """CI checkout must see every byte ``block_content_hash`` hashes.

    A bare ``storage/`` gitignore hid ``vendor_blocks_mirror/storage/Dockerfile``
    so local parity was 25/25 and CI was 24/25.
    """
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    ignored: list[str] = []
    for block_id in sorted(lock.get("blocks") or {}):
        root = MIRROR / block_id
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            proc = subprocess.run(
                ["git", "check-ignore", "-q", str(path)],
                cwd=ROOT,
                check=False,
            )
            if proc.returncode == 0:
                ignored.append(str(path.relative_to(ROOT)))
    assert ignored == [], "lock-hashed mirror files are gitignored: " + ", ".join(ignored)


def _install_typed_block_stubs() -> None:
    import enum
    import types

    _install_universal_block_stub()
    UniversalBlock = sys.modules["app.core.universal_base"].UniversalBlock

    typed = types.ModuleType("app.core.typed_block")

    class ContentType(enum.Enum):
        TEXT = "text"
        JSON = "json"

    class Schema:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class TypedBlock(UniversalBlock):
        async def execute(self, input_data: Any, params: Any = None) -> Dict[str, Any]:
            return {"status": "ok", "result": input_data}

        def validate_input(self, data: Any) -> Dict[str, Any]:
            return {"valid": True, "errors": [], "warnings": [], "data": data}

    typed.TypedBlock = TypedBlock
    typed.Schema = Schema
    typed.ContentType = ContentType
    sys.modules["app.core.typed_block"] = typed

    vs = types.ModuleType("app.core.vector_store")
    sys.modules["app.core.vector_store"] = vs
    ac = types.ModuleType("app.core.answer_contract")
    ac.SOURCE_CLASS_KEY = "source_class"
    ac.AnswerContractViolation = type("AnswerContractViolation", (Exception,), {})

    def _passthrough(*args: Any, **kwargs: Any) -> Any:
        return args[0] if args else None

    ac.coverage_line = _passthrough
    ac.emit_chunk = _passthrough
    ac.forbid_does_not_exist_claim = _passthrough
    ac.render_source_class = lambda *a, **k: "class"
    ac.source_class_of = lambda *a, **k: "class"
    sys.modules["app.core.answer_contract"] = ac


