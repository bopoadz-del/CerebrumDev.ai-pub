#!/usr/bin/env python3
"""Base images through the account's own registry mirror, never a public pull.

Live 2026-10-13 (replay runs 38348113721, 38350862279; job 115104003598):
``public.ecr.aws`` answered ``toomanyrequests: Data limit exceeded`` to the
runners -- for the postgres service image and for the product's base images
-- after Docker Hub's anonymous limit had already been routed around
(2026-10-09). Every public registry throttles anonymous runners. The gate now
pulls from the account's own GitHub Container Registry mirror
(``ghcr.io/<owner>/mirror/<name>``) with the workflow's built-in token, which
no anonymous limit applies to.

The mirror holds what the gate and the builds pull, derived -- never listed by
hand -- from the workflows' ``image:`` keys and the ``FROM`` lines of the
Dockerfiles the gate builds (image-mirror.yml copies each one in, by digest).

Before the product's image is built the gate pre-pulls each of its bases from
the mirror BY DIGEST and gives it the upstream name, so the product's own
Dockerfile -- never edited -- resolves its ``FROM`` locally:

* ``FROM python:3.12-slim`` -> the mirror's digest of that tag is pulled and
  tagged ``python:3.12-slim``;
* ``FROM python:3.12-slim@sha256:...`` -> that exact digest is pulled; a tag
  cannot stand for a digest, so the build is handed a named build context
  (``--build-context python:3.12-slim@sha256:...=docker-image://<mirror>@...``)
  which BuildKit substitutes for that ``FROM``.

A base the mirror does not hold yet (a new build's new base, until the weekly
mirror run) is pulled from its own registry instead. A base that cannot be
pulled from either is NOT the product's failure: ``image_pull_failed.txt``
names it ("image pull failed: <image>") and the run reads "no score", never a
verdict on the product's Dockerfile.

    base_images.py froms <Dockerfile>                 one base image per line
    base_images.py prepull <Dockerfile> <mirror> <contexts-out>
    base_images.py pull <image> <mirror> <local-name>  a gate service image
    base_images.py derive <mirror> <file>...          upstream refs, one per line
    base_images.py plan <mirror> <ref>...             JSON copy plan
    base_images.py mirror <mirror> <lock-out> <refs-file>   copy each ref in (image-mirror.yml)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Written when a base image could not be pulled; the scorer names it.
PULL_FAILED = "image_pull_failed.txt"
#: The registry's own error code for a throttled client (HTTP 429). A pull
#: that failed with it is the registry rate-limiting the runner: named
#: "image pull rate-limited" -- infrastructure, never a verdict on anything.
RATE_LIMITED_CODE = "toomanyrequests"


def pull_failure_cause(text: str) -> str:
    """What stopped a pull, by the registry's own error code."""
    return "image pull rate-limited" if RATE_LIMITED_CODE in (text or "").lower() else "image pull failed"
DOCKER_HUB = "docker.io"
#: ECR Public's copy of the Docker Official Images: the same images, the same
#: names (library/<name>), mirrored under the same mirror name.
ECR_LIBRARY = "public.ecr.aws/docker/library/"

_ARG = re.compile(r"^\s*ARG\s+(.+)$", re.IGNORECASE)
_FROM = re.compile(r"^\s*FROM\s+(.+)$", re.IGNORECASE)
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+])([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class Ref:
    """One image reference, split the way Docker's reference grammar does."""

    domain: str
    path: str
    tag: str
    digest: str

    @property
    def familiar(self) -> str:
        """The short form Docker prints (and BuildKit keys named contexts by)."""
        path = self.path
        if self.domain == DOCKER_HUB:
            if path.startswith("library/") and path.count("/") == 1:
                path = path[len("library/"):]
            name = path
        else:
            name = f"{self.domain}/{path}"
        if self.tag:
            name += f":{self.tag}"
        if self.digest:
            name += f"@{self.digest}"
        return name

    @property
    def named(self) -> str:
        """The familiar name without the digest: what ``docker tag`` can create."""
        return self.familiar.split("@", 1)[0]

    @property
    def mirror_name(self) -> str:
        """The image's name under the mirror: Docker Hub's ``library/python``
        and ECR Public's ``docker/library/python`` are both ``python``."""
        full = f"{self.domain}/{self.path}"
        if full.startswith(ECR_LIBRARY):
            return full[len(ECR_LIBRARY):]
        if self.domain == DOCKER_HUB:
            return self.path[len("library/"):] if self.path.startswith("library/") else self.path
        return full

    @property
    def mirror_tag(self) -> str:
        """The tag the mirror keeps this reference under. A floating tag is
        kept as itself; a digest pin under a tag naming the digest, so the
        weekly refresh of the floating tag never moves it."""
        if self.digest:
            short = self.digest.split(":", 1)[1][:12]
            return f"{self.tag}-{short}" if self.tag else f"sha256-{short}"
        return self.tag or "latest"


def parse(ref: str) -> Ref:
    ref = ref.strip()
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    tag = ""
    slash = ref.rfind("/")
    colon = ref.rfind(":")
    if colon > slash:
        ref, tag = ref[:colon], ref[colon + 1:]
    first, _, rest = ref.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        domain, path = first, rest
    else:
        domain, path = DOCKER_HUB, ref
    if domain == DOCKER_HUB and "/" not in path:
        path = f"library/{path}"
    if not tag and not digest:
        tag = "latest"
    return Ref(domain=domain, path=path.lower(), tag=tag, digest=digest)


def mirror_ref(ref: Ref, mirror: str) -> str:
    return f"{mirror.rstrip('/')}/{ref.mirror_name}"


def upstream_of(image: str, mirror: str) -> str:
    """A workflow's ``image:`` as the upstream reference it mirrors: a mirror
    reference names its upstream after the mirror's prefix
    (``<mirror>/postgres:16@sha256:...`` is ``postgres:16@sha256:...``)."""
    prefix = mirror.rstrip("/") + "/"
    return image[len(prefix):] if image.startswith(prefix) else image


# -- the Dockerfile ----------------------------------------------------------------


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        lines.append(buf + stripped)
        buf = ""
    if buf:
        lines.append(buf)
    return lines


def _expand(value: str, args: dict[str, str]) -> str | None:
    unresolved = False

    def sub(m: re.Match) -> str:
        nonlocal unresolved
        name = m.group(1) or m.group(4)
        op, alt = m.group(2), m.group(3)
        have = args.get(name)
        if op and op.endswith("-"):
            return have if have else (alt or "")
        if op and op.endswith("+"):
            return (alt or "") if have else ""
        if have is None:
            unresolved = True
            return ""
        return have

    out = _VAR.sub(sub, value)
    return None if unresolved or not out else out


def froms(dockerfile: str) -> list[str]:
    """The base images a Dockerfile pulls, as written after ARG expansion.

    Global ARGs (before the first FROM) with defaults are the only build
    arguments the gate's build has. Earlier stage names and ``scratch`` are
    not images; a reference that cannot be resolved is left to the build."""
    args: dict[str, str] = {}
    stages: set[str] = set()
    bases: list[str] = []
    seen_from = False
    for line in _logical_lines(dockerfile):
        m = _ARG.match(line)
        if m and not seen_from:
            for part in m.group(1).split():
                name, eq, default = part.partition("=")
                if eq:
                    args[name] = default.strip("\"'")
            continue
        m = _FROM.match(line)
        if not m:
            continue
        seen_from = True
        words = [w for w in m.group(1).split() if not w.startswith("--")]
        if not words:
            continue
        image = _expand(words[0], args)
        if len(words) >= 3 and words[1].lower() == "as":
            stages.add(words[2].lower())
        if not image or image.lower() == "scratch" or image.lower() in stages:
            continue
        if image not in bases:
            bases.append(image)
    return bases


# -- pre-pull ------------------------------------------------------------------------


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _tail(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or proc.stdout or "").strip().splitlines()
    return text[-1] if text else f"exit {proc.returncode}"


def mirror_digest(image: str) -> tuple[str, str]:
    """The digest the mirror holds for ``image`` (a tag), or ("", why)."""
    proc = _docker("buildx", "imagetools", "inspect", image, "--format", "{{json .Manifest}}")
    if proc.returncode != 0:
        return "", _tail(proc)
    try:
        digest = str(json.loads(proc.stdout).get("digest") or "")
    except ValueError:
        digest = ""
    return (digest, "") if _DIGEST.match(digest) else ("", "the mirror answered no digest")


def prepull(base: str, mirror: str) -> tuple[str, str]:
    """Pull one base by digest from the mirror and give it its upstream name.

    Returns (build-context line or "", failure or "")."""
    ref = parse(base)
    source = mirror_ref(ref, mirror)
    digest = ref.digest
    why = ""
    if not digest:
        digest, why = mirror_digest(f"{source}:{ref.mirror_tag}")
    if digest:
        pinned = f"{source}@{digest}"
        pulled = _docker("pull", "--quiet", pinned)
        if pulled.returncode == 0:
            if ref.digest:
                # A tag cannot stand for a digest: BuildKit takes the pulled
                # image in place of this exact FROM.
                print(f"{base} <- {pinned} (named build context)")
                return f"{ref.familiar}=docker-image://{pinned}", ""
            tagged = _docker("tag", pinned, ref.named)
            if tagged.returncode == 0:
                print(f"{base} <- {pinned}")
                return "", ""
            why = f"docker tag {ref.named}: {_tail(tagged)}"
        else:
            why = f"{pinned}: {_tail(pulled)}"
    # Not mirrored (yet), or the mirror failed: the base's own registry.
    print(f"{base}: not pulled from the mirror ({why}); pulling it from its own registry")
    upstream = _docker("pull", "--quiet", base)
    if upstream.returncode == 0:
        return "", ""
    return "", f"image pull failed: {base} (mirror: {why}; upstream: {_tail(upstream)})"


def pull_service(image: str, mirror: str, local: str) -> str:
    """Pull one of the gate's own service images (postgres) and name it
    ``local``: the mirror's pinned copy first, read with the built-in token,
    then the SAME digest from each public source in the copy plan's order
    (Google's mirror, ECR Public, Docker Hub) -- one throttled registry is not
    a stopped gate. Returns "" or the failure, also written to PULL_FAILED."""
    upstream = upstream_of(_owner_free(image, mirror), mirror)
    row = plan(mirror, [upstream])[0]
    pin = f"@{row['digest']}" if row["digest"] else f":{row['tag']}"
    candidates = [f"{row['mirror']}{pin}"] + [f"{source}{pin}" for source in row["sources"].split()]
    errors: list[str] = []
    for candidate in candidates:
        pulled = _docker("pull", "--quiet", candidate)
        if pulled.returncode != 0:
            errors.append(f"{candidate}: {_tail(pulled)}")
            continue
        tagged = _docker("tag", candidate, local)
        if tagged.returncode == 0:
            print(f"{image} <- {candidate}")
            return ""
        errors.append(f"docker tag {local}: {_tail(tagged)}")
        break
    failure = f"image pull failed: {image} ({'; '.join(errors)})"
    Path(PULL_FAILED).write_text(failure + "\n", encoding="utf-8")
    print(failure)
    return failure


def run_prepull(dockerfile: Path, mirror: str, contexts_out: Path) -> int:
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except OSError:
        # No Dockerfile: the build itself fails and that is the product's.
        contexts_out.write_text("", encoding="utf-8")
        return 0
    contexts: list[str] = []
    failures: list[str] = []
    for base in froms(text):
        context, failure = prepull(base, mirror)
        if context:
            contexts.append(context)
        if failure:
            failures.append(failure)
    contexts_out.write_text("".join(f"{c}\n" for c in contexts), encoding="utf-8")
    if failures:
        Path(PULL_FAILED).write_text("; ".join(failures) + "\n", encoding="utf-8")
        for failure in failures:
            print(failure)
        return 1
    return 0


# -- the mirror's list (image-mirror.yml) ---------------------------------------------


#: The job env naming the gate's own service images ("<name>=<image>").
SERVICE_IMAGES_ENV = "GATE_SERVICE_IMAGES"


def service_pairs(value: str) -> list[tuple[str, str]]:
    """``<name>=<image>`` pairs, whitespace-separated; an image may carry an
    unexpanded ``${{ ... }}`` (the owner) with spaces inside it."""
    return re.findall(r"([A-Za-z0-9_-]+)=((?:\$\{\{[^}]*\}\}|\S)+)", value or "")


def images_in_workflow(text: str) -> list[str]:
    """Every container/service ``image:`` a workflow pulls."""
    import yaml  # the mirror workflow and the tests have it; the gate never calls this

    doc = yaml.safe_load(text) or {}
    found: list[str] = []
    for job in (doc.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        containers = [job.get("container")] + list((job.get("services") or {}).values())
        images = [spec.get("image") if isinstance(spec, dict) else spec for spec in containers]
        # The gate's own service images, pulled by a step (pull_service):
        # GATE_SERVICE_IMAGES, "<name>=<image>" pairs, in the job's env.
        services = str((job.get("env") or {}).get(SERVICE_IMAGES_ENV) or "")
        images += [image for _, image in service_pairs(services)]
        for image in images:
            if isinstance(image, str) and image and "${{" not in image.split("/mirror/", 1)[-1]:
                found.append(image)
    return found


def derive(mirror: str, files: list[Path]) -> list[str]:
    """The upstream references the mirror must hold, from workflow files
    (``*.yml``/``*.yaml``: their ``image:`` keys), Dockerfiles (their FROM
    lines) and the mirror manifest (``*.json``: ``{"images": [...]}``)."""
    refs: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path.suffix in {".yml", ".yaml"}:
            found = [upstream_of(_owner_free(i, mirror), mirror) for i in images_in_workflow(text)]
        elif path.suffix == ".json":
            found = [str(i) for i in (json.loads(text).get("images") or [])]
        else:
            found = froms(text)
        for ref in found:
            if ref not in refs:
                refs.append(ref)
    return refs


def _owner_free(image: str, mirror: str) -> str:
    """``ghcr.io/${{ github.repository_owner }}/mirror/...`` as the mirror's name."""
    return re.sub(r"^ghcr\.io/\$\{\{\s*github\.repository_owner\s*\}\}/mirror", mirror.rstrip("/"), image)


def plan(mirror: str, refs: list[str]) -> list[dict[str, str]]:
    """Where each upstream reference is copied from, and to.

    Sources are tried in order: the Docker Hub images through Google's
    pull-through cache and ECR Public's copy before Docker Hub itself."""
    rows: list[dict[str, str]] = []
    for raw in refs:
        ref = parse(raw)
        sources: list[str] = []
        full = f"{ref.domain}/{ref.path}"
        library = ref.mirror_name if (ref.domain == DOCKER_HUB and ref.path.startswith("library/")) or full.startswith(ECR_LIBRARY) else ""
        if ref.domain == DOCKER_HUB or library:
            hub_path = f"library/{library}" if library else ref.path
            sources.append(f"mirror.gcr.io/{hub_path}")
            if library:
                sources.append(f"{ECR_LIBRARY}{library}")
            sources.append(f"docker.io/{hub_path}")
        else:
            sources.append(full)
        rows.append(
            {
                "upstream": raw,
                "tag": ref.tag,
                "digest": ref.digest,
                "sources": " ".join(sources),
                "mirror": mirror_ref(ref, mirror),
                "mirror_tag": ref.mirror_tag,
            }
        )
    return rows


def copy(row: dict[str, str]) -> dict[str, str]:
    """Copy one upstream reference into the mirror, digest for digest.

    A floating tag is resolved to the digest the first answering source
    holds; a pinned digest is copied as itself. The copy is read back: the
    mirror must answer the very digest that was copied."""
    sources = row["sources"].split()
    digest = row["digest"]
    errors: list[str] = []
    if not digest:
        for source in sources:
            digest, why = mirror_digest(f"{source}:{row['tag']}")
            if digest:
                break
            errors.append(f"{source}: {why}")
    target = f"{row['mirror']}:{row['mirror_tag']}"
    out = dict(row, target=target, mirrored="", error="")
    if not digest:
        out["error"] = "no source answered a digest: " + "; ".join(errors)
        return out
    for source in sources:
        made = _docker("buildx", "imagetools", "create", "--tag", target, f"{source}@{digest}")
        if made.returncode == 0:
            break
        errors.append(f"{source}@{digest}: {_tail(made)}")
    else:
        out["error"] = "copy failed: " + "; ".join(errors)
        return out
    got, why = mirror_digest(target)
    if got != digest:
        out["error"] = f"the mirror answers {got or why} for {target}, not the copied {digest}"
        return out
    out["digest"] = digest
    out["mirrored"] = f"{row['mirror']}@{digest}"
    return out


def run_mirror(mirror: str, refs: list[str], lock_out: Path) -> int:
    rows = [copy(row) for row in plan(mirror, refs)]
    lock_out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    failed = [r for r in rows if r["error"]]
    for r in rows:
        print(f"{r['upstream']} -> {r['mirrored'] or 'NOT MIRRORED: ' + r['error']}")
    return 1 if failed or not rows else 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "mirror":
        refs = [line.strip() for line in Path(argv[3]).read_text(encoding="utf-8").splitlines() if line.strip()] if len(argv) > 3 else []
        return run_mirror(argv[1], refs, Path(argv[2]))
    if len(argv) >= 2 and argv[0] == "froms":
        for base in froms(Path(argv[1]).read_text(encoding="utf-8")):
            print(base)
        return 0
    if len(argv) == 4 and argv[0] == "pull":
        return 1 if pull_service(argv[1], argv[2], argv[3]) else 0
    if len(argv) == 4 and argv[0] == "prepull":
        return run_prepull(Path(argv[1]), argv[2], Path(argv[3]))
    if len(argv) >= 2 and argv[0] == "derive":
        for ref in derive(argv[1], [Path(p) for p in argv[2:]]):
            print(ref)
        return 0
    if len(argv) >= 2 and argv[0] == "plan":
        print(json.dumps(plan(argv[1], argv[2:]), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
