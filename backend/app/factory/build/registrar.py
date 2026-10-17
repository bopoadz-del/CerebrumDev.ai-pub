"""The Store Manager's books: what each platform took, and what has moved on.

Read-only by construction. The Store Manager role has two halves with very
different risk, and this is the safe one: answering "which platform cloned
which block, at which revision, and is that revision still current" needs no
write access, no approval path and no network. The publish/version/deprecate
half stays deferred until it has a human-approval path -- a partial
implementation that can write to the Store is worse than none.

Everything here reads build ledgers. That is deliberate: once a platform is
delivered, the revision a block came from is unrecoverable from the artifact
itself, because the vendored files look identical whether they are current or
a year behind. The ledger is the only place that fact exists, which is why
:func:`app.factory.build.ledger.BuildLedger.record_clone` captures it at build
time.

Nothing here can tell you whether a *stale* clone is dangerous. It reports
drift, not severity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.factory.build.ledger import BuildLedger, iter_ledgers


@dataclass(frozen=True)
class CloneRecord:
    """One block, as taken by one platform."""

    product_id: str
    block_id: str
    revision: str
    origin: str
    vendored_path: str
    ledger: str
    cloned_at: str = ""

    @property
    def content_pinned(self) -> bool:
        """True when pinned by content digest rather than a commit.

        The vendor mirror is not a git repository, so its clones carry a
        digest. Comparing one against a Store commit is meaningless, and the
        staleness check has to say "unknown" rather than guess.
        """
        return self.revision.startswith("sha256:")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "block_id": self.block_id,
            "revision": self.revision,
            "origin": self.origin,
            "vendored_path": self.vendored_path,
            "ledger": self.ledger,
            "cloned_at": self.cloned_at,
        }


@dataclass
class StalenessReport:
    """Drift of one block against a reference revision."""

    block_id: str
    product_id: str
    revision: str
    status: str  # current | stale | unknown
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "product_id": self.product_id,
            "revision": self.revision,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class Inventory:
    """Every clone the registrar can see, indexed for the questions asked."""

    records: List[CloneRecord] = field(default_factory=list)

    def by_product(self) -> Dict[str, List[CloneRecord]]:
        out: Dict[str, List[CloneRecord]] = {}
        for r in self.records:
            out.setdefault(r.product_id, []).append(r)
        return {k: sorted(v, key=lambda r: r.block_id) for k, v in sorted(out.items())}

    def by_block(self) -> Dict[str, List[CloneRecord]]:
        out: Dict[str, List[CloneRecord]] = {}
        for r in self.records:
            out.setdefault(r.block_id, []).append(r)
        return {k: sorted(v, key=lambda r: r.product_id) for k, v in sorted(out.items())}

    def revisions_of(self, block_id: str) -> List[str]:
        """Distinct revisions of one block across every platform.

        More than one means the estate has diverged: two clients are running
        different code behind the same block name.
        """
        return sorted({r.revision for r in self.records if r.block_id == block_id})

    def diverged_blocks(self) -> Dict[str, List[str]]:
        return {
            bid: revs
            for bid in self.by_block()
            if len(revs := self.revisions_of(bid)) > 1
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clones": [r.to_dict() for r in self.records],
            "products": sorted(self.by_product()),
            "blocks": sorted(self.by_block()),
            "diverged": self.diverged_blocks(),
        }


def _product_of(ledger: BuildLedger) -> str:
    for event in ledger.events():
        pid = (event.payload or {}).get("product_id")
        if pid:
            return str(pid)
    return ledger.path.parent.name


def read_inventory(root: Path | str) -> Inventory:
    """Scan every build ledger under *root* and index its clones."""
    records: List[CloneRecord] = []
    for ledger in iter_ledgers(root):
        product = _product_of(ledger)
        for clone in ledger.clones():
            records.append(
                CloneRecord(
                    product_id=product,
                    block_id=str(clone.get("block_id", "")),
                    revision=str(clone.get("source_commit", "")),
                    origin=str(clone.get("store_repo", "")),
                    vendored_path=str(clone.get("vendored_path", "")),
                    ledger=str(ledger.path),
                    cloned_at=str(clone.get("ts", "")),
                )
            )
    return Inventory(records=records)


def check_staleness(
    inventory: Inventory,
    *,
    store_head: Optional[str] = None,
    blocks: Optional[Iterable[str]] = None,
) -> List[StalenessReport]:
    """Compare each clone's revision against *store_head*.

    ``unknown`` is a first-class answer and is used whenever a comparison
    would be meaningless: no reference supplied, or a clone pinned by content
    digest rather than a commit. Reporting those as "current" would be the
    dangerous reading -- it is precisely the mirror-sourced clones, which
    carry stub code, that most need flagging rather than blessing.
    """
    wanted = set(blocks) if blocks is not None else None
    out: List[StalenessReport] = []
    for record in sorted(inventory.records, key=lambda r: (r.product_id, r.block_id)):
        if wanted is not None and record.block_id not in wanted:
            continue
        if not store_head:
            status, detail = "unknown", "no store head supplied to compare against"
        elif record.content_pinned:
            status, detail = (
                "unknown",
                "pinned by content digest (mirror source); not comparable to a commit",
            )
        elif record.revision == store_head:
            status, detail = "current", ""
        else:
            status, detail = "stale", f"store head is {store_head[:12]}"
        out.append(
            StalenessReport(
                block_id=record.block_id,
                product_id=record.product_id,
                revision=record.revision,
                status=status,
                detail=detail,
            )
        )
    return out


def registrar_report(
    root: Path | str, *, store_head: Optional[str] = None
) -> Dict[str, Any]:
    """The Store Manager's books as one serialisable answer."""
    inventory = read_inventory(root)
    staleness = check_staleness(inventory, store_head=store_head)
    counts: Dict[str, int] = {}
    for report in staleness:
        counts[report.status] = counts.get(report.status, 0) + 1
    return {
        "schema_version": "registrar_report.v1",
        "root": str(root),
        "store_head": store_head,
        "platforms": len(inventory.by_product()),
        "blocks": len(inventory.by_block()),
        "clones": len(inventory.records),
        "status_counts": counts,
        "diverged": inventory.diverged_blocks(),
        "inventory": inventory.to_dict(),
        "staleness": [r.to_dict() for r in staleness],
    }
