"""A Factory-built product resolves the same framework on every build.

Live 2026-10-07 (e9efdf77): the base requirements were open ranges
(``starlette>=0.37``), so what a build installed depended on the day it ran.
One table (dependency_pins.FACTORY_PINS) now pins the base requirements, the
dev requirements and a Factory-owned constraints.txt the Dockerfile contract
installs against; the product's own declarations and vendored blocks' bounds
are never fought.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.factory.build.dependency_pins import (
    CONSTRAINTS_REL,
    FACTORY_PINS,
    TEST_ONLY,
    dist_name,
    load_lock,
    render_constraints,
)
from app.factory.build.factory_block import outside
from app.factory.build.factory_refresh import merged_requirements, refresh_factory_files
from app.factory.build.roles_handlers import (
    _render_dev_requirements,
    _render_dockerfile,
    _render_requirements,
)
from app.factory.build.store_acceptance import factory_rendered_paths, render_github_ci

BACKEND = Path(__file__).resolve().parents[2]


def _lines(text: str):
    return [ln for ln in text.splitlines() if dist_name(ln)]


def test_every_base_requirement_the_factory_renders_is_pinned_from_the_one_table():
    rendered = {dist_name(ln): ln for ln in _lines(_render_requirements())}
    for dist, line in rendered.items():
        assert dist in FACTORY_PINS, "base requirement %r is not in the pin table" % line
        assert line.split("#")[0].strip().endswith("==" + FACTORY_PINS[dist]), line
    assert not set(TEST_ONLY) & set(rendered), "a test-only package reached requirements.txt"


def test_the_dev_requirements_pin_the_test_stack_from_the_same_table():
    rendered = {dist_name(ln): ln.strip() for ln in _lines(_render_dev_requirements())}
    assert set(rendered) == set(TEST_ONLY)
    for dist, line in rendered.items():
        assert line == "%s==%s" % (dist, FACTORY_PINS[dist])


def test_constraints_carry_every_pin():
    constrained = {dist_name(ln): ln for ln in _lines(render_constraints())}
    assert constrained == {d: "%s==%s" % (d, v) for d, v in FACTORY_PINS.items()}


def test_the_pins_are_the_versions_the_factorys_own_suite_runs():
    # Known-good by construction: the Factory's own CI installs these exact
    # versions and runs its suite (test clients included) on them.
    own = {}
    for line in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s;#]+)", line.strip())
        if match:
            own[match.group(1).lower().replace("_", "-")] = match.group(3)
    top = load_lock()["top_level_versions"]
    shared = set(own) & set(top)
    assert {"fastapi", "uvicorn", "pydantic", "httpx", "pytest"} <= shared
    assert {d: top[d] for d in shared} == {d: own[d] for d in shared}


# The product image's interpreter (python:3.12-slim, linux x86_64).
_IMAGE_ENV = {
    "python_version": "3.12", "python_full_version": "3.12.0", "sys_platform": "linux",
    "platform_system": "Linux", "platform_machine": "x86_64", "os_name": "posix",
    "implementation_name": "cpython", "platform_python_implementation": "CPython",
}


def test_the_pin_set_is_closed_so_two_image_builds_resolve_the_same_distributions():
    """Every distribution a pinned distribution needs on the image's platform
    is itself pinned, at a version that satisfies the need. With
    ``-c constraints.txt`` pip then has exactly one choice for every
    distribution of the stack: two builds of one commit install the same
    versions, whatever was released in between."""
    from packaging.requirements import Requirement

    lock = load_lock()
    dists = lock["distributions"]
    assert {d: dists[d]["version"] for d in lock["top_level_versions"]} == lock["top_level_versions"]
    wanted_extras = {d: set(x) for d, x in lock.get("top_level_extras", {}).items()}
    gaps = []
    for dist, row in dists.items():
        for line in row["requires_dist"]:
            req = Requirement(line)
            extras = wanted_extras.get(dist, set()) | {""}
            if req.marker is not None and not any(
                req.marker.evaluate({**_IMAGE_ENV, "extra": e}) for e in extras
            ):
                continue
            name = re.sub(r"[-_.]+", "-", req.name).lower()
            if name not in FACTORY_PINS:
                gaps.append("%s needs %s: not pinned" % (dist, line))
            elif not req.specifier.contains(FACTORY_PINS[name], prereleases=True):
                gaps.append("%s needs %s: pinned %s" % (dist, line, FACTORY_PINS[name]))
    assert gaps == []


def test_rendering_the_pin_set_twice_is_byte_identical(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    for root in (first, second):
        root.mkdir()
        (root / "requirements.txt").write_bytes(b"fastapi>=0.110\nrequests>=2.31\n")
        refresh_factory_files(root, "platform")
    for rel in ("requirements.txt", CONSTRAINTS_REL):
        assert (first / rel).read_bytes() == (second / rel).read_bytes()
    assert _render_requirements() == _render_requirements()
    assert _render_dev_requirements() == _render_dev_requirements()


def test_the_pin_set_never_names_what_only_a_vendored_block_brings():
    # A block's own distribution bounds what it brings in (an OCR block holds
    # its imaging library below a major version); those bounds belong to the
    # Store's block lock. The closure is the Factory stack's alone: a block
    # import obligation that is not part of it is never constrained.
    from app.factory.build.block_obligations import dependency_obligations

    obligations = dependency_obligations({"vendor/cerebrum/blocks/img.py": "import PIL\nimport yaml\n"})
    assert obligations
    constrained = {dist_name(ln) for ln in _lines(render_constraints())}
    assert not {d.lower() for d in obligations} & constrained


def test_a_writer_line_the_pin_does_not_satisfy_keeps_its_declaration(tmp_path):
    (tmp_path / "requirements.txt").write_bytes("starlette<1\nfastapi>=0.120\nrequests==2.32.3\n".encode())
    merged = merged_requirements(tmp_path)
    product = outside(merged)
    # The writer's own lines are untouched, byte for byte.
    assert product.startswith("starlette<1\nfastapi>=0.120\nrequests==2.32.3\n")
    block = merged[len(product):] if merged.startswith(product) else merged.replace(product, "")
    block_dists = {dist_name(ln) for ln in _lines(block)}
    assert not {"starlette", "fastapi", "requests"} & block_dists, block
    constrained = {dist_name(ln) for ln in _lines(render_constraints(product.splitlines()))}
    assert "starlette" not in constrained          # pin excluded by the writer: dropped
    assert "fastapi" in constrained                # writer range admits the pin: kept
    assert "requests" not in constrained           # not the Factory's to pin


def test_a_writer_added_dependency_still_resolves_against_the_constraints(tmp_path):
    from packaging.requirements import Requirement

    (tmp_path / "requirements.txt").write_bytes("fastapi>=0.120\nrequests>=2.31\nstarlette<1\n".encode())
    refresh_factory_files(tmp_path, "platform")
    constraints = (tmp_path / CONSTRAINTS_REL).read_text(encoding="utf-8")
    pins = {dist_name(ln): ln.split("==")[1].strip() for ln in _lines(constraints)}
    requirements = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    for line in _lines(requirements):
        req = Requirement(line.split("#")[0].strip())
        name = req.name.lower().replace("_", "-")
        if name in pins:
            assert req.specifier.contains(pins[name], prereleases=True), (line, pins[name])


def test_refresh_renders_constraints_for_a_tree_built_before_the_pins(tmp_path):
    (tmp_path / "requirements.txt").write_bytes("fastapi>=0.110\n".encode())
    changed = refresh_factory_files(tmp_path, "platform")
    assert CONSTRAINTS_REL in changed
    assert refresh_factory_files(tmp_path, "platform") == []   # idempotent
    assert CONSTRAINTS_REL in factory_rendered_paths()


def test_the_dockerfile_contract_installs_against_the_constraints():
    floor = json.loads((BACKEND / "app" / "factory" / "acceptance_floor.v2.json").read_text(encoding="utf-8"))
    rows = [c for c in floor["checks"] if c["id"] == "docker_health_200"]
    assert rows and all(CONSTRAINTS_REL in rows[0][f] for f in ("requirement_text", "brief_render"))
    docker = _render_dockerfile()
    installs = [ln for ln in docker.splitlines() if ln.startswith("RUN pip install")]
    assert installs and all(("-c %s" % CONSTRAINTS_REL) in ln for ln in installs)
    copies = [ln for ln in docker.splitlines() if ln.startswith("COPY") and CONSTRAINTS_REL in ln]
    assert copies, "constraints.txt must be in the image before the first install"
    ci_installs = [ln for ln in render_github_ci().splitlines() if "pip install" in ln and "-r requirements" in ln]
    assert ci_installs and all(("-c %s" % CONSTRAINTS_REL) in ln for ln in ci_installs)
