"""Domain models for this platform.

Plain dataclasses on purpose: the delivered platform must run with no
ORM, no service, and no network. Persistence is in app/store.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AnalyticsSurface:
    """Entity for capability analytics_surface."""

    id: Optional[int] = None
    reference: str = ""
    status: str = 'open'
    quantity: int = 0

    FIELDS = ['reference', 'status', 'quantity']
    CONSTRAINTS = {'status': {'allowed_values': ['open', 'in_progress', 'closed']}, 'quantity': {'min': 0, 'max': 10000}}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnalyticsSurface":
        known = {k: v for k, v in (data or {}).items() if k in cls.FIELDS}
        return cls(id=(data or {}).get("id"), **known)


@dataclass
class DashboardSurface:
    """Entity for capability dashboard_surface."""

    id: Optional[int] = None
    reference: str = ""
    status: str = 'open'
    quantity: int = 0

    FIELDS = ['reference', 'status', 'quantity']
    CONSTRAINTS = {'status': {'allowed_values': ['open', 'in_progress', 'closed']}, 'quantity': {'min': 0, 'max': 10000}}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DashboardSurface":
        known = {k: v for k, v in (data or {}).items() if k in cls.FIELDS}
        return cls(id=(data or {}).get("id"), **known)


MODELS = {
    "analytics_surface": AnalyticsSurface,
    "dashboard_surface": DashboardSurface,
}
