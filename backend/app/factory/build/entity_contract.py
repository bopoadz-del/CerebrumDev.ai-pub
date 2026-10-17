"""Where a capability persists, read from what the product DECLARES.

The round-trip probes (product_gate's ROUND_TRIP_PROBE and the WRITER gate's
behaviour probe) run inside the generated workspace, which carries no factory
code, so the resolver is a source string both probes embed -- one definition.

Two things are read, never guessed:

* the ENTITY a capability writes. The Factory-rendered ``app/routes.py``
  declares ``ROUTE_ENTITIES`` -- the entity each POST route saves to, rendered
  from the same entries as the routes -- and that is read first. The
  capability manifest ``app.jobs.CAPABILITIES``, the handler module's
  ``ENTITY`` and the model's ``ENTITY`` are read after it, in that order, for
  products without a Factory-rendered route. The
  capability id is NOT a fallback: a probe that assumed entity == capability
  id judged nothing whenever a product's names differed (co-op repro,
  2026-10-06: every capability "no readable entity").
  A capability that declares ``ENTITY = None`` (or empty) persists nothing --
  a read-only/aggregate capability -- and is not judgeable by a round-trip.
  A capability that declares nothing at all is reported as such.
* the TENANT the route wrote under. The route resolves it through the
  product's single tenancy path, ``app.tenancy.resolve_tenant``; the probe
  asks the same function for the same token. The canonical store requires
  ``tenant_id`` on ``list_all``; calling it without one raised for every
  capability, which also read as "no readable entity".
"""

from __future__ import annotations

from app.factory.build.store_acceptance import DEFAULT_TENANT_ID

#: The line a probe source carries where the resolver is rendered in, so the
#: probe stays one literal (tests lift functions out of it by AST).
ENTITY_RESOLVER_SLOT = "ENTITY_RESOLVER = None"

#: In-probe definitions. Expects ``store`` and ``AUTH`` (the headers the probe
#: posts with) to be bound before it runs.
ENTITY_RESOLVER_SRC = r'''
_ENTITY_UNDECLARED = object()
DEFAULT_TENANT = __DEFAULT_TENANT__


def _jobs_entities():
    try:
        from app.jobs import CAPABILITIES
    except Exception:
        return {}
    out = {}
    for item in CAPABILITIES or []:
        if isinstance(item, dict) and item.get("id") and "entity" in item:
            out[item["id"]] = item.get("entity")
    return out


_JOBS_ENTITIES = _jobs_entities()


def _route_entities():
    try:
        from app.routes import ROUTE_ENTITIES
    except Exception:
        return {}
    return dict(ROUTE_ENTITIES or {})


_ROUTE_ENTITIES = _route_entities()


def _declared_entity(cap_id, cls):
    """(entity, declared). entity None/'' with declared=True: persists nothing.

    The Factory-rendered route's save target comes first (every product
    built through the Factory carries it); then the capability manifest
    (app.jobs.CAPABILITIES); then the handler module's ENTITY; then the model.
    """
    import importlib
    if cap_id in _ROUTE_ENTITIES:
        return _ROUTE_ENTITIES[cap_id], True
    if cap_id in _JOBS_ENTITIES:
        return _JOBS_ENTITIES[cap_id], True
    module_name = "app.actions." + str(cap_id).replace("-", "_")
    try:
        module = importlib.import_module(module_name)
    except Exception:
        module = None
    if module is not None and hasattr(module, "ENTITY"):
        return getattr(module, "ENTITY"), True
    if hasattr(cls, "ENTITY"):
        return getattr(cls, "ENTITY"), True
    return None, False


_PROBE_TENANT = []


def _probe_tenant():
    """The tenant the route wrote under, resolved lazily (AUTH may be bound
    after this source in the probe)."""
    if not _PROBE_TENANT:
        tenant_id = None
        try:
            from app.tenancy import resolve_tenant
            tenant_id = resolve_tenant(AUTH).tenant_id
        except Exception:
            tenant_id = None
        _PROBE_TENANT.append(tenant_id)
    return _PROBE_TENANT[0]


def _list_entity(entity):
    tenant_id = _probe_tenant()
    if tenant_id is None:
        # No tenancy module to ask: a single-tenant product, whose one tenant
        # is the default the Factory's tenancy module declares.
        tenant_id = DEFAULT_TENANT
    try:
        return store.list_all(entity, tenant_id=tenant_id)
    except TypeError:
        # A store that is not tenant-scoped at all.
        return store.list_all(entity)
'''.replace("__DEFAULT_TENANT__", repr(DEFAULT_TENANT_ID))
