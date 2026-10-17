"""Claim provenance — the attribution an answer cannot back is not shipped.

Phase 3 of the governed-layers build. This is the PORT of The_Fork's
``app/agents/citation_provenance.py`` (EvidenceRecord / Evidence /
build_evidence) into the product kernel, so every generated platform
inherits the same mechanism instead of hand-rolling a second one.

PORT NOTES (what changed, why, honestly):
- The Fork's module polices CONSTRUCTION-domain attribution shapes
  (contract-id regexes, BOQ fragments). Those stay in The_Fork; this port
  carries the domain-neutral core: evidence records (retrieval / tool_run /
  user), the citable-class rule, and the claim->evidence mapping.
- Identifiers are extracted generically (word-shaped ids the record's own
  text actually contains), not with the contract-id pattern, because the
  factory's corpora are multi-domain.
- The same owner's ruling is preserved: the model never writes a source
  line; citations render from EVIDENCE records. An attribution that
  matches no evidence record is stripped, never shipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Evidence kinds.
KIND_RETRIEVAL = "retrieval"
KIND_TOOL_RUN = "tool_run"
KIND_USER = "user"

#: Only these source classes may back an identifier attribution.
CITABLE_CLASSES = {"project_corpus", "master_corpus"}


@dataclass
class EvidenceRecord:
    """One thing that actually happened this turn.

    ``kind`` is "retrieval" (a chunk came back), "tool_run" (a tool
    executed, with the inputs it was actually given) or "user" (the
    operator's own words — an id the user named is theirs, not a
    fabrication).
    """

    kind: str
    text: str = ""
    tool: Optional[str] = None
    inputs: str = ""
    source_name: str = ""
    source_class: str = "project_corpus"

    @property
    def reads_corpus(self) -> bool:
        if self.kind == KIND_RETRIEVAL:
            return bool(self.text.strip())
        if self.kind == KIND_TOOL_RUN:
            # A tool run reads the corpus unless it is one of the known
            # non-corpus tools; the factory port has no fixed denylist, so
            # a tool run is corpus-reading when it was handed inputs.
            return bool((self.inputs or "").strip())
        return False

    @property
    def citable(self) -> bool:
        """May an identifier in this record back an attribution?

        A corpus-reading tool run can; the operator's own words always can.
        """
        if self.kind == KIND_USER:
            return True
        if self.kind == KIND_TOOL_RUN:
            return self.reads_corpus
        return self.source_class in CITABLE_CLASSES

    def identifiers(self) -> set[str]:
        """Word-shaped ids the record's own text/inputs/name contain."""
        blob = " ".join((self.text, self.inputs, self.source_name))
        words = {
            word.strip(".,;:()[]")
            for word in blob.replace("-", " ").replace("_", " ").split()
        }
        return {w.lower() for w in words if len(w) >= 3}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "tool": self.tool,
            "inputs": self.inputs,
            "source_name": self.source_name,
            "source_class": self.source_class,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceRecord":
        return cls(
            kind=str(data.get("kind") or ""),
            text=str(data.get("text") or ""),
            tool=data.get("tool"),
            inputs=str(data.get("inputs") or ""),
            source_name=str(data.get("source_name") or ""),
            source_class=str(data.get("source_class") or "project_corpus"),
        )


@dataclass
class Evidence:
    """Every record for one turn, and the questions a gate asks of them."""

    records: List[EvidenceRecord] = field(default_factory=list)

    def any_corpus_read(self) -> bool:
        return any(r.reads_corpus for r in self.records)

    def citable_ids(self) -> set[str]:
        ids: set[str] = set()
        for record in self.records:
            if record.citable:
                ids |= record.identifiers()
        return ids

    def user_ids(self) -> set[str]:
        ids: set[str] = set()
        for record in self.records:
            if record.kind == KIND_USER:
                ids |= record.identifiers()
        return ids

    def source_names(self, citable_only: bool = False) -> set[str]:
        names: set[str] = set()
        for record in self.records:
            if citable_only and not record.citable:
                continue
            if record.source_name:
                names.add(record.source_name.lower())
        return names

    def tool_names(self) -> set[str]:
        return {
            record.tool.lower()
            for record in self.records
            if record.kind == KIND_TOOL_RUN and record.tool
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Evidence":
        raw = data.get("records") or []
        return cls(records=[EvidenceRecord.from_dict(r) for r in raw])


def build_evidence(
    rag_sys_msg: Optional[Dict[str, Any]],
    messages: Optional[List[Dict[str, Any]]],
) -> Evidence:
    """Claim -> evidence mapping: what the turn actually read, ran, and was
    told — nothing else may back an attribution.

    ``rag_sys_msg`` carries the retrieval/system context; ``messages`` the
    turn's message list. Records are rebuilt the same way The_Fork's gate
    rebuilds them: retrieval context becomes retrieval records, tool-call
    messages become tool_run records, and the user's own turns become user
    records.
    """
    records: List[EvidenceRecord] = []

    if rag_sys_msg:
        context = rag_sys_msg.get("context") or rag_sys_msg.get("chunks") or []
        for chunk in context if isinstance(context, list) else []:
            if isinstance(chunk, dict):
                records.append(
                    EvidenceRecord(
                        kind=KIND_RETRIEVAL,
                        text=str(chunk.get("text") or ""),
                        source_name=str(chunk.get("source_name") or ""),
                        source_class=str(chunk.get("source_class") or "project_corpus"),
                    )
                )

    for message in messages or []:
        role = str((message or {}).get("role") or "")
        content = str((message or {}).get("content") or "")
        if role == "user":
            records.append(EvidenceRecord(kind=KIND_USER, text=content))
        elif role == "tool" or role == "assistant":
            tool = None
            if isinstance(message, dict):
                tool = message.get("tool") or message.get("name")
            records.append(
                EvidenceRecord(
                    kind=KIND_TOOL_RUN,
                    tool=tool,
                    inputs=content,
                    text="",
                )
            )

    return Evidence(records=records)
