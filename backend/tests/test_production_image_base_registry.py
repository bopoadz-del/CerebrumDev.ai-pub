"""The production image's base images never pull anonymously from Docker Hub.

Live 2026-10-09 20:59-21:32 UTC: Docker Hub answered 429, then
auth.docker.io 504, to the GitHub runners. ``FROM python:3.11-slim-bookworm``
failed to resolve on CI's Production Docker build -- and deploy-aws builds the
same Dockerfile, so no fix could have shipped while it lasted. The same
official image comes through the ECR Public mirror of Docker Official Images.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _bases(text: str) -> list:
    stages = set()
    bases = []
    for line in text.splitlines():
        m = re.match(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
        if m:
            if m.group(1) not in stages:
                bases.append(m.group(1))
            if m.group(2):
                stages.add(m.group(2))
    return bases


def test_every_production_base_image_names_a_registry_that_is_not_docker_hub():
    bases = _bases(DOCKERFILE.read_text(encoding="utf-8"))
    assert bases, "the production Dockerfile has no FROM"
    for image in bases:
        first = image.split("/", 1)[0]
        registry = first if "/" in image and ("." in first or ":" in first) else "docker.io"
        assert registry != "docker.io", f"{image!r} pulls anonymously from Docker Hub"


def test_the_parser_reads_an_implicit_docker_hub_image_as_docker_hub():
    assert _bases("FROM python:3.11-slim\n") == ["python:3.11-slim"]
    assert _bases("FROM a AS build\nFROM build\n") == ["a"]
