"""The gate replay, run inside the Factory (Route A, owner order 2026-10-13).

The Factory's default GITHUB_TOKEN cannot read the private cerebrum-builds
(probe CerebrumDev.ai#738: HTTP 404, "Repository not found"), and
cerebrum-builds cannot read the private Factory without a key. So the replay
runs HERE, on the Factory at the PR head, with what cerebrum-builds handed
over (its ``.github/gate_handover/handover.py``), vendored under
``.github/gate_replay/vendor/<builds commit>/``:

* ``gate/`` -- the gate, byte for byte: ``.github/store_gate/`` (the harness
  helpers, ``replay_select.py``, ``replay_flips.py`` -- the pass rule --,
  ``known_bad_fixtures.json``, ``revoked_certifications.json``),
  ``store-gate.yml``, ``gate-replay.yml``, ``gate_replay.json``;
* ``products/<sha>.tar.gz`` -- each selected build's full tree;
* ``manifest.json`` -- the source commit, the sha256 of every gate file, the
  selection the gate's own record made, each product's git tree id and bundle
  sha256 (the same bytes as cerebrum-builds' ``.github/gate_handover/manifest.json``).

Nothing here re-implements the gate: the selection rule, the fixtures and the
pass rule are the vendored files, loaded or run as they are, and the gate job
(``.github/workflows/gate-replay-store-gate.yml``) is generated from the
vendored ``store-gate.yml`` with only its two fetches from cerebrum-builds
replaced by reads of the handover.

    python3 replay_in_factory.py verify
    python3 replay_in_factory.py select --out selection.json
    python3 replay_in_factory.py extract SHA DEST
    python3 replay_in_factory.py gate-scripts DEST
    python3 replay_in_factory.py vendor-dir
    python3 replay_in_factory.py generate-store-gate [--check]

Standard library only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VENDOR_ROOT = HERE / "vendor"
#: This repository's replay config (how many certified builds are replayed).
CONFIG = REPO / ".github" / "gate_replay.json"
STORE_GATE_OUT = REPO / ".github" / "workflows" / "gate-replay-store-gate.yml"
SCHEMA = "gate_handover.v1"
MANIFEST = "manifest.json"
GATE = "gate"
STORE_GATE_REL = ".github/workflows/store-gate.yml"
STORE_GATE_DIR_REL = ".github/store_gate"


# -- the vendored handover ----------------------------------------------------------


def vendor_dir(root: Path = VENDOR_ROOT) -> Path:
    """The one vendored handover (``vendor/<builds commit>``)."""
    dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if len(dirs) != 1:
        raise ValueError(f"{root}: expected exactly one vendored handover, found {[d.name for d in dirs]}")
    return dirs[0]


def manifest(vendor: Optional[Path] = None) -> Dict[str, Any]:
    vendor = vendor or vendor_dir()
    return json.loads((vendor / MANIFEST).read_text(encoding="utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle_members(path: Path) -> Dict[str, Tuple[str, bytes]]:
    """path -> (git mode, bytes) of every entry in a handed-over bundle."""
    out: Dict[str, Tuple[str, bytes]] = {}
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(path.read_bytes())), mode="r:") as tar:
        for member in tar.getmembers():
            name = member.name
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or not parts:
                raise ValueError(f"{path.name}: unsafe entry {name!r}")
            if member.issym():
                out[name] = ("120000", member.linkname.encode("utf-8"))
            elif member.isfile():
                fh = tar.extractfile(member)
                out[name] = ("100755" if member.mode & 0o111 else "100644", fh.read() if fh else b"")
            elif not member.isdir():
                raise ValueError(f"{path.name}: {name!r} is neither a file nor a symlink")
    return out


def bundle_files(path: Path) -> List[str]:
    return sorted(bundle_members(path))


def tree_id(members: Mapping[str, Tuple[str, bytes]]) -> str:
    """The git tree id of these files (what ``git rev-parse <commit>^{tree}``
    answers for the commit they were taken from)."""

    def obj(kind: str, body: bytes) -> bytes:
        return hashlib.sha1(f"{kind} {len(body)}".encode("ascii") + b"\0" + body).digest()

    def tree(prefix: str) -> bytes:
        entries: Dict[str, Tuple[str, bytes, str]] = {}
        for path, (mode, data) in members.items():
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            if "/" in rest:
                name = rest.split("/", 1)[0]
                if name not in entries:
                    entries[name] = ("40000", tree(prefix + name + "/"), name + "/")
            else:
                entries[rest] = (mode, obj("blob", data), rest)
        body = b"".join(
            f"{mode} {name}".encode("utf-8") + b"\0" + oid
            for name, (mode, oid, _key) in sorted(entries.items(), key=lambda kv: kv[1][2].encode("utf-8"))
        )
        return obj("tree", body)

    return tree("").hex()


def verify(vendor: Optional[Path] = None) -> List[str]:
    """Why the vendored copy is not the manifest ([] = it is)."""
    vendor = vendor or vendor_dir()
    problems: List[str] = []
    try:
        data = manifest(vendor)
    except (OSError, ValueError) as exc:
        return [f"manifest unreadable: {exc}"]
    if data.get("schema") != SCHEMA:
        problems.append(f"manifest schema is {data.get('schema')!r}, not {SCHEMA}")
    if vendor.name != data.get("source_commit"):
        problems.append(
            f"STALE HANDOVER: vendored as cerebrum-builds {vendor.name}, but the manifest "
            f"records the gate of {data.get('source_commit')}"
        )
    expected: Dict[str, str] = {}
    for rel, digest in (data.get("gate_files") or {}).items():
        expected[f"{GATE}/{rel}"] = digest
    products = data.get("products") or {}
    for sha, item in products.items():
        expected[str(item.get("bundle"))] = str(item.get("bundle_sha256"))
    present = {p.relative_to(vendor).as_posix() for p in vendor.rglob("*") if p.is_file()}
    present.discard(MANIFEST)
    for rel in sorted(set(expected) | present):
        if rel not in present:
            problems.append(f"{rel}: in the manifest, missing from the vendored copy")
        elif rel not in expected:
            problems.append(f"{rel}: vendored, not in the manifest")
        else:
            actual = _sha256((vendor / rel).read_bytes())
            if actual != expected[rel]:
                problems.append(f"{rel}: sha256 {actual[:12]}, the manifest says {expected[rel][:12]}")
    for sha, item in sorted(products.items()):
        path = vendor / str(item.get("bundle"))
        if not path.is_file():
            continue
        try:
            actual_tree = tree_id(bundle_members(path))
        except (OSError, ValueError, tarfile.TarError, EOFError, gzip.BadGzipFile) as exc:
            problems.append(f"{sha}: bundle unreadable ({exc})")
            continue
        if actual_tree != item.get("tree"):
            problems.append(f"{sha}: bundle holds tree {actual_tree}, the manifest says {item.get('tree')}")
    for row in data.get("selection") or []:
        if str(row.get("sha")) not in products:
            problems.append(f"{row.get('branch')}: selected, no product handed over")
    return problems


def _require_verified(vendor: Path) -> None:
    problems = verify(vendor)
    if problems:
        raise ValueError("the vendored handover is not its manifest: " + "; ".join(problems))


# -- extract ---------------------------------------------------------------------------


def extract(sha: str, dest: Path | str, vendor: Optional[Path] = None) -> List[str]:
    """Write handed-over commit ``sha``'s tree into ``dest``; its paths.

    Refused unless the bundle is the manifest's (sha256) and holds the
    manifest's git tree id -- i.e. exactly that cerebrum-builds commit's tree."""
    vendor = vendor or vendor_dir()
    item = (manifest(vendor).get("products") or {}).get(sha)
    if not item:
        raise ValueError(f"{sha} was not handed over")
    path = vendor / str(item["bundle"])
    if _sha256(path.read_bytes()) != item.get("bundle_sha256"):
        raise ValueError(f"{sha}: bundle sha256 differs from the manifest")
    members = bundle_members(path)
    if tree_id(members) != item.get("tree"):
        raise ValueError(f"{sha}: bundle is not tree {item.get('tree')}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name, (mode, data) in sorted(members.items()):
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            link = data.decode("utf-8")
            resolved = (target.parent / link).resolve()
            if not str(resolved).startswith(str(dest.resolve())):
                raise ValueError(f"{sha}: symlink {name} -> {link} leaves the tree")
            os.symlink(link, target)
            continue
        target.write_bytes(data)
        if os.name == "posix":
            os.chmod(target, 0o755 if mode == "100755" else 0o644)
    return sorted(members)


def gate_scripts(dest: Path | str, vendor: Optional[Path] = None) -> None:
    """The handed-over ``.github/store_gate`` at ``dest/.github/store_gate``
    (what store-gate.yml's "Check out the gate scripts under replay" provides)."""
    vendor = vendor or vendor_dir()
    _require_verified(vendor)
    src = vendor / GATE / STORE_GATE_DIR_REL
    target = Path(dest) / STORE_GATE_DIR_REL
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)


# -- selection: the vendored rule on the handed-over record ----------------------------


def _load(path: Path, name: str) -> Any:
    """Load a vendored module as it is. No bytecode is written next to it: the
    vendored copy must stay exactly the manifest's files."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def select(n: Optional[int] = None, vendor: Optional[Path] = None, *, verify_first: bool = True) -> List[Dict[str, Any]]:
    """The replay matrix: the vendored ``replay_select.py`` applied to the
    handed-over record (the certifying runs and their green ``store-gate``
    status), the vendored revocations, and the vendored known-bad fixtures."""
    vendor = vendor or vendor_dir()
    if verify_first:
        _require_verified(vendor)
    store_gate = vendor / GATE / STORE_GATE_DIR_REL
    tag = hashlib.sha1(str(store_gate).encode("utf-8")).hexdigest()[:10]
    rs = _load(store_gate / "replay_select.py", f"_vendored_replay_select_{tag}")
    revocations = _load(store_gate / "revocations.py", f"_vendored_revocations_{tag}")
    data = manifest(vendor)
    products = data.get("products") or {}
    handed = [row for row in data.get("selection") or [] if row.get("expect") != "fail"]
    total_handed = len(data.get("selection") or [])
    handed_branches = {str(row.get("branch")): str(row.get("sha")) for row in data.get("selection") or []}
    if n is None:
        n = rs.configured_n(env={}, config=CONFIG)
    def exists(branch: str) -> bool:
        return branch in handed_branches and handed_branches[branch] in products

    if len(handed) == 0:
        if total_handed == 0:
            # NOT APPLICABLE -- and ONLY this case: the handover is EMPTY (0
            # entries), so it hands over no tree at all and there is nothing to
            # replay. The CLI writes an empty selection and exits 0 with a
            # visible annotation; the verdict job exits 0. This is NOT a gate
            # weakening: the gate stays strict the moment any build is handed
            # over. (ORDERS 2026-10-28 0200Z #3 / 0300Z #4 / 0314Z: the minimum
            # stays configured in .github/gate_replay.json.)
            return []
        # A NON-EMPTY handover with 0 CERTIFIED builds is a real failure: builds
        # were handed over but none is certified (every row is expect==fail).
        # This returns 1 -- it is NOT not-applicable.
        raise ValueError(
            f"the handover holds {total_handed} build(s) but 0 are certified "
            "(every entry is expect=='fail'): hand over a certified selection "
            "from cerebrum-builds"
        )
    if len(handed) < n:
        raise ValueError(
            f"the handover holds {len(handed)} certified build(s); the replay asks for {n}: "
            "hand over a new selection from cerebrum-builds"
            + (f" (dropped: {data['dropped']['products']})" if (data.get("dropped") or {}).get("products") else "")
        )
    runs = [
        {
            "id": row.get("run_id"),
            "head_branch": row.get("branch"),
            "head_sha": row.get("sha"),
            "status": "completed",
            "conclusion": "success",
            "created_at": row.get("certified_at"),
        }
        for row in handed
    ]
    green = {str(row.get("sha")) for row in handed}

    def status_of(sha: str) -> str:
        return "success" if sha in green else ""

    picked = rs.certified(runs, status_of, exists, n, revoked_runs=revocations.revoked_run_ids(revocations.load()))
    bad = rs.known_bad(rs.read_known_bad(store_gate / "known_bad_fixtures.json"), exists)
    for row in bad:
        if row["sha"] not in products:
            raise ValueError(f"known-bad fixture {row['branch']} @ {row['sha']}: that tree was not handed over")
    return picked + bad


# -- the gate job: store-gate.yml with its two fetches read from the handover ----------

#: The vendored store-gate.yml up to ``jobs:`` (triggers, permissions) is
#: replaced by this: the gate is called only as a replay here. Its permissions
#: are the vendored ones less ``statuses: write`` (a replay writes no commit
#: status: that step runs only when not a replay).
HEADER = """\
# GENERATED -- do not edit. `python3 .github/gate_replay/replay_in_factory.py
# generate-store-gate` writes this file from the VENDORED cerebrum-builds
# store-gate.yml (.github/gate_replay/vendor/{source}/gate/.github/workflows/store-gate.yml);
# backend/tests/factory/test_gate_replay_in_factory.py fails when they differ.
#
# The Store acceptance gate, run inside the Factory for the gate replay (Route
# A). Everything from `jobs:` on is the vendored workflow's, byte for byte,
# except its two fetches from cerebrum-builds -- the certified build's checkout
# and the gate scripts' checkout -- which read the handover instead (the
# Factory cannot read cerebrum-builds). No other repository, no secret.

name: Store acceptance gate (replay in the Factory)

on:
  workflow_call:
    inputs:
      product_sha:
        description: The certified build commit to gate (a handed-over tree).
        type: string
        required: true
      replay_label:
        description: Names this replay's artifact.
        type: string
        required: true
      overlay_artifact:
        description: Artifact of Factory-rendered files laid over the build before it is gated.
        type: string
        required: false
        default: ""
      factory_ref:
        description: The Factory commit whose vendored handover is read (the PR head).
        type: string
        required: true

{permissions}"""

PRODUCT_CHECKOUT = """\
      - uses: actions/checkout@v4
        with:
          # Empty (a dispatch on a build branch): that branch, as always.
          ref: ${{ inputs.product_sha }}
"""

PRODUCT_FROM_HANDOVER = """\
      - name: Check out the handover (the Factory at the PR head)
        uses: actions/checkout@v4
        with:
          ref: ${{ inputs.factory_ref }}
          path: _factory_vendor
          sparse-checkout: .github/gate_replay
          persist-credentials: false
      - name: Restore the certified build from the handover (its git tree id verified)
        run: python3 _factory_vendor/.github/gate_replay/replay_in_factory.py extract "$PRODUCT_SHA" .
"""

GATE_CHECKOUT = """\
      - name: Check out the gate scripts under replay
        if: ${{ inputs.replay_label }}
        uses: actions/checkout@v4
        with:
          path: _gate_under_replay
"""

GATE_FROM_HANDOVER = """\
      - name: Check out the gate scripts under replay
        if: ${{ inputs.replay_label }}
        run: python3 _factory_vendor/.github/gate_replay/replay_in_factory.py gate-scripts _gate_under_replay
      - name: Remove the handover checkout from the build's tree
        run: rm -rf _factory_vendor
"""

STATUS_WRITE = "  statuses: write\n"

#: (vendored text, what replaces it). Each must occur exactly once.
REPLACEMENTS = ((PRODUCT_CHECKOUT, PRODUCT_FROM_HANDOVER), (GATE_CHECKOUT, GATE_FROM_HANDOVER))


def generate_store_gate(vendor: Optional[Path] = None) -> str:
    vendor = vendor or vendor_dir()
    text = (vendor / GATE / STORE_GATE_REL).read_text(encoding="utf-8")
    marker = "\njobs:\n"
    if text.count(marker) != 1:
        raise ValueError("the vendored store-gate.yml has no single top-level `jobs:`")
    head, jobs = text[: text.index(marker) + 1], text[text.index(marker) + 1 :]
    if head.count("\npermissions:\n") != 1:
        raise ValueError("the vendored store-gate.yml has no single top-level `permissions:`")
    permissions = head[head.index("\npermissions:\n") + 1 :]
    if permissions.count(STATUS_WRITE) != 1:
        raise ValueError("the vendored store-gate.yml's permissions changed shape (no `statuses: write`)")
    permissions = permissions.replace(STATUS_WRITE, "")
    for old, new in REPLACEMENTS:
        if jobs.count(old) != 1:
            raise ValueError(
                "the vendored store-gate.yml changed shape (a fetch from cerebrum-builds is not where it was): "
                + old.strip().splitlines()[0]
            )
        jobs = jobs.replace(old, new)
    return HEADER.format(source=vendor.name, permissions=permissions) + jobs


# -- CLI ----------------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="replay_in_factory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    p_select = sub.add_parser("select")
    p_select.add_argument("--out", required=True)
    p_extract = sub.add_parser("extract")
    p_extract.add_argument("sha")
    p_extract.add_argument("dest")
    p_gate = sub.add_parser("gate-scripts")
    p_gate.add_argument("dest")
    sub.add_parser("vendor-dir")
    p_gen = sub.add_parser("generate-store-gate")
    p_gen.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "verify":
        problems = verify()
        for line in problems:
            print(f"DRIFT {line}")
        if problems:
            print("the vendored handover is not its manifest: the replay would not be cerebrum-builds' gate")
            return 1
        data = manifest()
        print(
            f"vendored handover of {data['source_repo']}@{data['source_commit']}: "
            f"{len(data['gate_files'])} gate file(s), {len(data['products'])} tree(s), all as the manifest says"
        )
        return 0
    if args.cmd == "select":
        try:
            rows = select()
        except ValueError as exc:
            print(f"the replay cannot select: {exc}")
            return 1
        good = [r for r in rows if r.get("expect") != "fail"]
        bad = [r for r in rows if r.get("expect") == "fail"]
        data = manifest()
        print(f"handover of {data['source_repo']}@{data['source_commit'][:12]}")
        print(f"replaying the gate on {len(good)} certified build branch(es):")
        for row in good:
            print(f"  {row['branch']} @ {row['sha'][:12]} (certified {row.get('certified_at')}, run {row.get('run_id')})")
        print(f"and on {len(bad)} known-bad fixture(s), each of which must still fail:")
        for row in bad:
            print(f"  {row['branch']} @ {row['sha'][:12]} (must fail {row['expected_check']})")
        # NOT APPLICABLE is not a failure: an empty certified handover writes an
        # empty (or known-bad-only) selection and exits 0 with a visible
        # annotation. The replay runs strict the moment builds are handed over.
        if not good:
            print(
                "::notice title=gate replay::not applicable: the certified handover is empty "
                "(0 entries); the replay runs strict the moment any build is handed over "
                "from cerebrum-builds"
            )
        Path(args.out).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write("matrix=" + json.dumps(rows) + "\n")
        return 0
    if args.cmd == "extract":
        names = extract(args.sha, args.dest)
        print(f"restored {args.sha}: {len(names)} file(s), tree {manifest()['products'][args.sha]['tree']}")
        return 0
    if args.cmd == "gate-scripts":
        gate_scripts(args.dest)
        print(f"gate scripts under replay: the handover of {vendor_dir().name}")
        return 0
    if args.cmd == "vendor-dir":
        print(vendor_dir().relative_to(REPO).as_posix())
        return 0
    text = generate_store_gate()
    if args.check:
        current = STORE_GATE_OUT.read_text(encoding="utf-8") if STORE_GATE_OUT.is_file() else ""
        if current != text:
            print(f"{STORE_GATE_OUT.name} is not what the vendored store-gate.yml generates")
            return 1
        print(f"{STORE_GATE_OUT.name} is the vendored store-gate.yml's")
        return 0
    STORE_GATE_OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {STORE_GATE_OUT}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
