"""factory_repo_root resolves local + Docker layouts."""

from __future__ import annotations


from app.factory.paths import factory_repo_root
from app.factory.golden_match import goldens


def test_factory_repo_root_finds_blueprints():
    root = factory_repo_root()
    assert (root / "blueprints").is_dir()
    # Goldens are found on disk by their own serves_verticals declaration.
    assert goldens(root / "blueprints"), "no golden blueprint declares serves_verticals"


def test_factory_repo_root_docker_layout(tmp_path):
    app_pkg = tmp_path / "app" / "factory"
    app_pkg.mkdir(parents=True)
    (tmp_path / "blueprints" / "steward").mkdir(parents=True)
    (tmp_path / "blueprints" / "steward" / "steward.v1.yaml").write_text("x: 1\n")
    (tmp_path / "blocks.lock.json").write_text("{}\n")
    anchor = app_pkg / "paths.py"
    anchor.write_text("# anchor\n")
    assert factory_repo_root(anchor) == tmp_path
    # Production image copies the pin next to blueprints/ at /app.
    assert (factory_repo_root(anchor) / "blocks.lock.json").is_file()
