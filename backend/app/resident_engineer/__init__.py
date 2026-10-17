"""Cerebrum Resident Engineer — Resident Mode core (Milestone 2).

Feature-flagged via ``RESIDENT_ENGINEER_ENABLED`` (default false).
Reasons over ``/product-dna/`` only — never scans source to invent product shape.
"""

from app.resident_engineer.flags import resident_engineer_enabled
from app.resident_engineer.heal.catalog import ALLOWLISTED_HEAL_ACTIONS

__all__ = ["ALLOWLISTED_HEAL_ACTIONS", "resident_engineer_enabled"]
