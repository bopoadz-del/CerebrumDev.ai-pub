"""Which Store blocks serve a capability, read from what each block declares.

The architect used to keep a block only when the user's vertical's kit held
it (or the brief typed its id), so a build with no vertical chosen was cut
down to the governance blocks whatever the Store held. Relevance replaces
that gate: every block the Store clears for attaching is a candidate, and a
capability keeps the blocks whose OWN declaration (id, name, description,
tags, input and output field names and options) shares its words.

The score is a pure function of the pinned Store and the capability's text:
no model, no list of words or ids for any product. Words are weighted by how
rare they are across the Store, so words most blocks declare ("input",
"configuration", "action") count for nothing. A block CLEARS the floor for a
capability when the capability's id holds every word of the block's id,
or when they share ``MIN_SHARED`` words that fewer than
``INFORMATIVE_SHARE`` of the Store's blocks declare (in their names or their
prose), at least one of them a word of the capability's id. The vertical is only a
tie-break between equal scores, never a gate.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

#: A shared word counts towards the floor only when fewer than this share of
#: the Store's blocks declare it.
INFORMATIVE_SHARE = 0.1
#: The same bar for a KIT's declaration, counted across the Store's kits. A
#: kit index holds a few dozen documents, not hundreds, and the domain
#: suites share one template sentence ("typed document analysis, entity
#: extraction, metrics ..."); a word a quarter of the kits declare says
#: nothing about which kit serves a capability.
KIT_INFORMATIVE_SHARE = 0.25
#: Distinct informative words a block and a capability must share to clear
#: the floor (when the capability text does not name the block).
MIN_SHARED = 2
#: Words shorter than this carry no meaning on their own ("id", "to", "of").
MIN_WORD = 3
#: Two words are one word when they share a common start of at least this
#: many letters (more when both words change at the end) ...
MIN_ROOT = 4
#: ... and the longer one runs on past that common start by no more than
#: this many letters (an inflection's tail, not a different word).
MAX_TAIL = 3

_WORD = re.compile(r"[a-z0-9]+")


def words(text: Any) -> FrozenSet[str]:
    """The meaningful words of a text: lower case, split on anything that is
    not a letter or digit (so ``snake_case`` ids split)."""
    raw = _WORD.findall(str(text or "").lower())
    return frozenset(w for w in raw if len(w) >= MIN_WORD and not w.isdigit())


def folds(a: str, b: str) -> bool:
    """Whether two words are forms of one word, by their shape alone: a long
    common start and short tails. No suffix is spelled anywhere, so the rule
    holds for any language the Store's manifests are written in."""
    if a == b:
        return True
    root = len(os.path.commonprefix([a, b]))
    short_tail = min(len(a), len(b)) - root
    long_tail = max(len(a), len(b)) - root
    if short_tail == 0:
        # One word is the other plus one letter ("role" / "roles").
        return root >= MIN_ROOT and long_tail <= 1
    # Both changed at the end: a longer common start, one letter given up
    # by the shorter word ("policy" / "policies"; never "audit" / "audio").
    return root > MIN_ROOT and short_tail <= 1 and long_tail <= MAX_TAIL


def _field_text(fields: Any) -> List[str]:
    out: List[str] = []
    for field in fields if isinstance(fields, list) else ():
        if not isinstance(field, dict):
            continue
        out.append(str(field.get("name") or ""))
        options = field.get("options")
        if isinstance(options, list):
            out.extend(str(o) for o in options if isinstance(o, (str, int, float)))
    return out


def declared_words(block_id: str, manifest: Mapping[str, Any]) -> FrozenSet[str]:
    """Every word a block declares about itself in its own manifest: its id,
    name, description, tags, and the names and options of its input and
    output fields. Filler is removed later by ``StoreIndex.informative``."""
    tags = manifest.get("tags")
    parts = [
        block_id,
        str(manifest.get("name") or ""),
        str(manifest.get("description") or ""),
        *(str(t) for t in (tags if isinstance(tags, list) else ())),
        *_field_text(manifest.get("inputs")),
        *_field_text(manifest.get("outputs")),
    ]
    return words(" ".join(parts))


def declared_function(block_id: str, manifest: Mapping[str, Any], limit: int = 110) -> str:
    """One line saying what the block declares it does: its description,
    else its name and tags, else nothing (an honest blank)."""
    text = " ".join(str(manifest.get("description") or "").split())
    if not text:
        tags = manifest.get("tags")
        tag_text = ", ".join(str(t) for t in tags if isinstance(t, str)) if isinstance(tags, list) else ""
        name = str(manifest.get("name") or "").strip()
        text = f"{name} ({tag_text})" if name and tag_text else (name or tag_text)
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


@dataclass(frozen=True)
class Candidate:
    block_id: str
    score: float
    shared: Tuple[str, ...]
    clears: bool
    #: Clears on strong evidence: the capability's id names the block, or
    #: shares ``MIN_SHARED`` of its own words with the block's declaration,
    #: one of them a word of the block's own id (what the block IS).
    strong: bool = False


class StoreIndex:
    """The declared words of a set of Store blocks and how rare each word is."""

    def __init__(
        self,
        manifests: Mapping[str, Mapping[str, Any]],
        share: float = INFORMATIVE_SHARE,
        common: Iterable[str] = (),
    ):
        self.manifests: Dict[str, Mapping[str, Any]] = dict(manifests)
        self.share = share
        raw = {bid: declared_words(bid, m) for bid, m in sorted(self.manifests.items())}
        # One root per word the Store declares, shortest form first, so a
        # word's forms ("permit", "permits") count as one word.
        self._roots: Dict[str, List[str]] = {}
        self._canon: Dict[str, str] = {}
        for word in sorted(set().union(*raw.values()) if raw else (), key=lambda w: (len(w), w)):
            self._canon[word] = self._root_of(word, add=True)
        self.terms: Dict[str, FrozenSet[str]] = {bid: self.fold(ws) for bid, ws in raw.items()}
        self.size = len(self.terms)
        self._df = _counts(self.terms.values())
        # How many blocks use a word in their description PROSE. A word many
        # blocks write in passing ("the", "from", "value") is filler, however
        # rarely a block names a field after it.
        self._prose_df = _counts(
            self.fold(words(m.get("description") or "")) for m in self.manifests.values()
        )
        #: Words another, larger index found common in prose ("with",
        #: "from"): filler here too, however few of these documents use them.
        self._common = self.fold(common)

    def _root_of(self, word: str, add: bool = False) -> str:
        bucket = self._roots.setdefault(word[:MIN_ROOT], []) if add else self._roots.get(word[:MIN_ROOT], [])
        for root in bucket:
            if folds(root, word):
                return root
        if add:
            bucket.append(word)
        return word

    def fold(self, ws: Iterable[str]) -> FrozenSet[str]:
        """The words mapped onto the Store's roots; a word the Store never
        declares in any form stays itself (and so can share nothing)."""
        return frozenset(self._canon.get(w) or self._root_of(w) for w in ws)

    def idf(self, term: str) -> float:
        return math.log((self.size + 1) / (self._df.get(term, 0) + 1))

    def informative(self, term: str) -> bool:
        limit = self.share * self.size
        if term in self._common:
            return False
        return 0 < self._df.get(term, 0) < limit and self._prose_df.get(term, 0) < limit

    def common_words(self) -> FrozenSet[str]:
        """The words too many of these documents write in their prose to say
        anything about one of them."""
        limit = self.share * self.size
        return frozenset(t for t, n in self._prose_df.items() if n >= limit)

    def candidate(
        self, block_id: str, capability_id: Any, capability_text: Any = ""
    ) -> Optional[Candidate]:
        """How ``block_id`` serves a capability. It CLEARS the floor when the
        capability's id holds every word of the block's id, or when they share ``MIN_SHARED`` informative
        words and at least one of them is a word of the capability's own id
        (a fit is about what the capability IS, not a word in its prose)."""
        terms = self.terms.get(block_id)
        if terms is None:
            return None
        own_cap = self.fold(words(capability_id))
        cap = own_cap | self.fold(words(capability_text))
        shared = tuple(sorted(t for t in cap & terms if self.informative(t)))
        own = self.fold(words(block_id))
        named = bool(own) and own <= own_cap
        anchors = sum(1 for t in shared if t in own_cap)
        score = sum(self.idf(t) for t in shared)
        clears = named or (anchors >= 1 and len(shared) >= MIN_SHARED)
        strong = named or (anchors >= MIN_SHARED and bool(own & own_cap & set(shared)))
        return Candidate(block_id, score, shared, clears, strong)

    def clears(self, block_id: str, capability_id: Any, capability_text: Any = "") -> bool:
        found = self.candidate(block_id, capability_id, capability_text)
        return bool(found and found.clears)

    def rank(
        self,
        capability_id: Any,
        capability_text: Any = "",
        *,
        prefer: Iterable[str] = (),
        limit: int = 5,
    ) -> List[Candidate]:
        """Blocks sharing an informative word with the capability, clearing
        ones first, then by score. ``prefer`` (the chosen vertical's kit)
        only breaks ties between equal scores; it never admits a block."""
        preferred = set(prefer or ())
        found = [
            c
            for c in (self.candidate(b, capability_id, capability_text) for b in self.terms)
            if c and (c.shared or c.clears)
        ]
        found.sort(key=lambda c: (not c.clears, -c.score, c.block_id not in preferred, c.block_id))
        return found[: max(0, limit)]

    def best_fit(
        self, capability_id: Any, capability_text: Any = "", *, prefer: Iterable[str] = ()
    ) -> Optional[Candidate]:
        """The top-ranked block that clears the floor on STRONG evidence, or
        None (GENERATE). This is the bar for binding a block nobody chose: a
        word shared by sense-blind matching is enough to keep a pick made
        from the blocks' declared functions, not to add one unasked."""
        for cand in self.rank(capability_id, capability_text, prefer=prefer, limit=self.size):
            if not cand.clears:
                return None
            if cand.strong:
                return cand
        return None


def _counts(groups: Iterable[Iterable[str]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for group in groups:
        for t in group:
            out[t] = out.get(t, 0) + 1
    return out


def store_index(block_ids: Iterable[str], store_root: Any = None) -> StoreIndex:
    """An index over the given blocks, read from the pinned Store's own
    manifests through the same reader every Store lookup uses (no list kept
    in the Factory). A block the Store holds no manifest for declares
    nothing, so it can never clear the floor."""
    from app.factory import store_kits

    manifests = store_kits.block_manifests(store_root)
    return StoreIndex({str(b): manifests[str(b)] for b in block_ids or () if str(b) in manifests})


def kit_index(kits: Mapping[str, Mapping[str, Any]], common: Iterable[str] = ()) -> StoreIndex:
    """An index over KIT manifests (id, name, description, tags), so a kit
    is judged against a capability by what the kit itself declares it is
    for, with the same floor a block meets. Rarity is counted across kits;
    ``common`` (the block Store's prose filler) is filler here too, since a
    few dozen kit sentences cannot tell an everyday word from a rare one."""
    return StoreIndex(kits, share=KIT_INFORMATIVE_SHARE, common=common)
