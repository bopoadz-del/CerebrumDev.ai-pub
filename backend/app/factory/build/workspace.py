"""Lane-restricted workspace handed to a build role.

A role never touches the filesystem directly. It gets one of these, and every
write is routed through :func:`app.factory.build.authority.assert_write_allowed`
for that role. A path outside the role's lane raises ``AuthorityError`` and
kills the build -- it is never caught, downgraded, or skipped, because the
whole value of the authority kernel is that a mis-scoped role aborts instead
of quietly producing an artifact whose provenance nobody can vouch for.

Reads are deliberately wider than writes: the WRITER must be able to read the
block contracts the CLONER vendored in order to write against them, but must
not be able to edit them. Reads are still confined to the workspace (or the
store root, when one is supplied) so a role cannot exfiltrate the factory.
"""

from __future__ import annotations

import inspect
import os
import shutil
from pathlib import Path
from typing import Any, List, Optional, Sequence

from app.factory.build.authority import (
    AuthorityError,
    BuildRole,
    assert_write_allowed,
)


def _copy_unless_same(src: Path, dest: Path) -> None:
    """Copy ``src`` over ``dest`` -- unless they are already one file.

    A staged tree can hold a link (symlink or hard link) to the destination's
    own file: the coding agent works there and may link what it did not
    change. Copying a file onto itself is shutil.SameFileError, which killed a
    build thread at WRITER commit (live 2026-10-08, blocks.lock.json). When
    both names are the same file the destination already holds those bytes,
    so the copy has nothing to do.
    """
    try:
        if dest.exists() and os.path.samefile(src, dest):
            return
    except OSError:
        pass
    shutil.copy2(src, dest)


def _required_positionals(fn: Any) -> Optional[int]:
    """Count required positional params on a (possibly bound) callable.

    ``pathlib.Path.exists`` / ``read_text`` / ``write_text`` are truthy
    callables, but they operate on *self*. A bound ``Path.exists`` has
    zero required positionals (3.12+ only keyword ``follow_symlinks``).
    :meth:`RoleWorkspace.exists` requires ``relpath``. Un-inspectable
    builtins return ``None`` so callers fail closed to the Path-root walk.
    """
    if not callable(fn):
        return None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    count = 0
    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            return max(count, 1)
        if param.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            continue
        if param.default is inspect.Parameter.empty:
            count += 1
    return count


def supports_relpath_io(workspace: Any) -> bool:
    """True for ``exists(rel)`` / ``read_text(rel)`` — not pathlib.Path.

    ``pathlib.Path`` also has ``exists`` / ``read_text`` / ``write_text``, but
    those operate on *self*. Treating a Path (or a duck that bound those
    methods) as this API raises
    ``TypeError: Path.exists() takes 1 positional argument but 2 were given``
    (CEREBRUMDEV-BACKEND-W). Floor WRITER uses :class:`RoleWorkspace`.
    """
    if workspace is None or isinstance(workspace, Path):
        return False
    exists_n = _required_positionals(getattr(workspace, "exists", None))
    read_n = _required_positionals(getattr(workspace, "read_text", None))
    return (
        exists_n is not None
        and exists_n >= 1
        and read_n is not None
        and read_n >= 1
    )


def supports_relpath_write(workspace: Any) -> bool:
    """True for ``write_text(rel, content)`` — :class:`RoleWorkspace`, not Path."""
    if workspace is None or isinstance(workspace, Path):
        return False
    write_n = _required_positionals(getattr(workspace, "write_text", None))
    return write_n is not None and write_n >= 2


def relpath_exists(workspace: Any, relpath: str | Path) -> bool:
    """``exists(rel)`` when the handle is a protocol workspace; else False.

    A TypeError (Path-like ``exists(self)``) must not abort REUSE harvest.
    """
    if not supports_relpath_io(workspace):
        return False
    try:
        return bool(workspace.exists(relpath))
    except TypeError:
        return False


def relpath_read_text(workspace: Any, relpath: str | Path) -> str:
    """``read_text(rel)`` when the handle is a protocol workspace; else ``""``."""
    if not supports_relpath_io(workspace):
        return ""
    try:
        return workspace.read_text(relpath)
    except (TypeError, OSError):
        return ""


def _fs_root(workspace: Any) -> Path:
    """Filesystem root for a Path or RoleWorkspace-style handle."""
    return Path(getattr(workspace, "workspace", workspace))


def write_workspace_text(
    workspace: Any, relpath: str | Path, content: str
) -> Path:
    """Write via RoleWorkspace ``write_text(rel, …)`` or a Path root.

    Floor WRITER always receives :class:`RoleWorkspace`. Selfcheck / Path
    roots (CEREBRUMDEV-BACKEND-V) fall back to ``(root / rel).write_text``.
    """
    if supports_relpath_write(workspace):
        written = workspace.write_text(relpath, content)
        return written if isinstance(written, Path) else _fs_root(workspace) / relpath
    dest = _fs_root(workspace) / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


class RoleWorkspace:
    """The only filesystem handle a role is given."""

    def __init__(
        self,
        role: BuildRole | str,
        workspace: Path | str,
        *,
        store_root: Optional[Path | str] = None,
        staging: Optional[Path | str] = None,
        sealed: Sequence[str] = (),
    ) -> None:
        self.role = BuildRole(role)
        #: Where the artifact really lives.
        self.destination = Path(workspace).resolve()
        #: Where writes land. Equal to destination unless staged.
        self.workspace = Path(staging).resolve() if staging else self.destination
        self.staged = staging is not None
        self.store_root = Path(store_root).resolve() if store_root else None
        #: Globs sealed after an earlier gate (vendor/** after CLONER).
        self.sealed = tuple(sealed)
        #: Every path this role wrote, workspace-relative, in order.
        self.written: List[str] = []
        if self.staged:
            self.workspace.mkdir(parents=True, exist_ok=True)

    def commit(self) -> List[str]:
        """Move staged writes into the real workspace. No-op when unstaged.

        Called only after the role returns successfully, so a process killed
        part-way through a pass leaves the destination holding the previous
        complete attempt rather than a splice of two. Exception-rollback would
        not do: a hard kill runs no Python, so the protection has to be that
        the destination was never touched in the first place.
        """
        if not self.staged:
            return list(self.written)
        for rel in self.written:
            src = self.workspace / rel
            if not src.exists():
                continue
            dest = self.destination / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            _copy_unless_same(src, dest)
        return list(self.written)

    def record_existing(self, root: Optional[Path | str] = None) -> List[str]:
        """Register files already on disk under the workspace into ``written``.

        Subprocess writers (the CodeWhale worker) create files directly in
        the staging tree without going through ``write_text``, so commit()
        silently dropped every authored handler and the worker receipt
        (live-factory sess_620b8581fb224bea: the role counted authored=8,
        the gate counted 0 — the worker's output was stranded in staging
        and rmtree'd after commit). Recording the tree makes the pass's own
        output travel with the commit.
        """
        base = Path(root).resolve() if root is not None else self.workspace
        added: List[str] = []
        if not base.is_dir():
            return added
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(self.workspace).as_posix()
            except ValueError:
                continue
            if rel not in self.written:
                self.written.append(rel)
                added.append(rel)
        return added

    # -- writes (authority-checked) --------------------------------------

    def _authorise(self, relpath: str | Path) -> Path:
        target = Path(relpath)
        if not target.is_absolute():
            target = self.workspace / target
        return assert_write_allowed(
            self.role,
            target,
            workspace=self.workspace,
            store_root=self.store_root,
            sealed=self.sealed,
        )

    def _record(self, resolved: Path) -> None:
        try:
            self.written.append(resolved.relative_to(self.workspace).as_posix())
        except ValueError:
            self.written.append(str(resolved))

    def write_text(self, relpath: str | Path, content: str) -> Path:
        resolved = self._authorise(relpath)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        # newline="": the bytes written are the text given, on every OS (a
        # shared-file stamp promises byte-exact product lines).
        resolved.write_text(content, encoding="utf-8", newline="")
        self._record(resolved)
        return resolved

    def write_bytes(self, relpath: str | Path, content: bytes) -> Path:
        resolved = self._authorise(relpath)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content)
        self._record(resolved)
        return resolved

    def mkdir(self, relpath: str | Path) -> Path:
        resolved = self._authorise(relpath)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def copy_file(self, source: Path | str, relpath: str | Path) -> Path:
        """Copy one file in. The destination is lane-checked; the source is not
        -- roles legitimately read from the Store, which is outside the
        workspace."""
        resolved = self._authorise(relpath)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        _copy_unless_same(Path(source), resolved)
        self._record(resolved)
        return resolved

    def copy_tree(self, source: Path | str, relpath: str | Path) -> Path:
        """Copy a directory in, authorising every file rather than the root.

        A single check on the destination root would let a tree containing
        ``../`` symlinks land outside the lane, so each file is authorised on
        its own account.
        """
        src = Path(source)
        dest_root = self._authorise(relpath)
        for item in sorted(src.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(src)
            self._authorise(dest_root / rel)
        dest_root.mkdir(parents=True, exist_ok=True)
        for item in sorted(src.rglob("*")):
            if not item.is_file():
                continue
            target = dest_root / item.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_unless_same(item, target)
            self._record(target)
        return dest_root

    # -- reads (workspace-confined, not lane-confined) --------------------

    def read_text(self, relpath: str | Path) -> str:
        return self._resolve_read(relpath).read_text(encoding="utf-8")

    def read_path(self, relpath: str | Path) -> Path:
        """Where a read of ``relpath`` resolves: staging first, then the
        destination (then the Store root) -- the same order as read_text."""
        return self._resolve_read(relpath)

    def exists(self, relpath: str | Path) -> bool:
        try:
            return self._resolve_read(relpath).exists()
        except AuthorityError:
            return False

    def _resolve_read(self, relpath: str | Path) -> Path:
        target = Path(relpath)
        # Reads span staging AND the destination: a staged WRITER still has to
        # read what the CLONER already put in the real workspace. Staging is
        # searched first so a role sees its own in-progress writes, then the
        # destination -- resolving only against staging would hide every
        # artifact an earlier phase produced.
        roots = [self.workspace, self.destination]
        if self.store_root:
            roots.append(self.store_root)

        if not target.is_absolute():
            candidates = [(root / target).resolve() for root in roots]
            for candidate in candidates:
                if candidate.exists():
                    return candidate
            return candidates[0]  # non-existent: report against staging

        resolved = target.resolve()
        for root in roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise AuthorityError(
            f"{self.role.value} may not read {relpath} — outside the workspace"
        )
