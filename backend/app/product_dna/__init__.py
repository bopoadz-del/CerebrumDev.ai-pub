"""Product DNA bundle — versioned, checksummed product understanding surface.

Emitted by the Factory generator into every product at ``/product-dna/``.
Resident Mode (Milestone 2+) must reason over this bundle, not scan source.
"""

from __future__ import annotations

from app.product_dna.emit import DNA_BUNDLE_FILES, DNA_SCHEMA_VERSION, emit_product_dna
from app.product_dna.validate import load_schema, validate_dna_document

__all__ = [
    "DNA_BUNDLE_FILES",
    "DNA_SCHEMA_VERSION",
    "emit_product_dna",
    "load_schema",
    "validate_dna_document",
]
