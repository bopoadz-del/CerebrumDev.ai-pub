"""The product-gate suites TESTER stamps -- one list, two readers.

TESTER writes these files (roles_handlers.run_tester renders them from the
product's own declared models, declared_specs); the writer's self-check
(scripts/factory_checks.py) runs whichever of them are on disk. They stay
in the workspace between rework rounds, Factory-owned in TESTER's lane, so
in every rework round the writer can run the exact suites that failed it
before it declares done -- and TESTER re-stamps them from the models the
writer left, so what the writer ran and what judges it read one source.
"""

from __future__ import annotations

from app.factory.build.placeholder_connectors import CONTRACT_TEST

SMOKE_SUITE = "tests/test_smoke.py"
ROUTES_SUITE = "tests/test_routes.py"
DOMAIN_SUITE = "tests/test_domain_acceptance.py"

#: In the order the self-check runs them.
PRODUCT_SUITES = (SMOKE_SUITE, ROUTES_SUITE, DOMAIN_SUITE, CONTRACT_TEST)


#: A work-list item's KIND. The work list the writer reads is text, so the kind
#: is the item's leading tag: a finding item is ``[<check>] ...`` (or the raw
#: finding row); the re-check item -- the exact command to re-run the checks
#: that failed -- is ``[recheck] ...``. ``recheck`` is reserved: no gate check
#: carries that id. Readers split a work list with these two helpers, never by
#: matching the item's wording.
RECHECK_KIND = "recheck"
RECHECK_TAG = f"[{RECHECK_KIND}]"


def is_recheck(item: str) -> bool:
    return str(item).startswith(RECHECK_TAG)


def finding_items(work_list) -> tuple:
    """The work list's finding items, in order (every non-recheck item)."""
    return tuple(i for i in work_list if not is_recheck(i))


def recheck_items(work_list) -> tuple:
    return tuple(i for i in work_list if is_recheck(i))
