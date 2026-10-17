"""Generic action registry: discovery, validation and resolution.

The registry is domain-neutral. It discovers :class:`ActionSpec` objects from
installed domain kits, validates them, and resolves them by *exact* id. It
never silently falls back to another action, and never sources trust-scope
(domain / permissions) from caller arguments.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
from typing import Dict, Iterable, List, Optional

from app.cerebrum_product_kernel.contract.models import ActionContext, ActionSpec

logger = logging.getLogger(__name__)

# Domain-kit action modules are discovered under this package as
# ``app.product.kit.domains.<kit>.actions`` exposing an ``ACTION_SPECS`` list.
DOMAINS_PACKAGE = "app.product.kit.domains"


class InvalidActionError(ValueError):
    """Raised when an ActionSpec fails structural validation."""


class DuplicateActionError(ValueError):
    """Raised when two ActionSpecs share the same action_id."""


class ActionRegistry:
    """In-memory registry of validated :class:`ActionSpec` objects."""

    def __init__(self) -> None:
        self._specs: Dict[str, ActionSpec] = {}

    # ------------------------------------------------------------------
    # Registration / validation
    # ------------------------------------------------------------------
    def register(self, spec: ActionSpec) -> None:
        self._validate_spec(spec)
        if spec.action_id in self._specs:
            raise DuplicateActionError(
                f"action_id '{spec.action_id}' is already registered"
            )
        self._specs[spec.action_id] = spec
        logger.debug("registered action '%s'", spec.action_id)

    def register_all(self, specs: Iterable[ActionSpec]) -> None:
        for spec in specs:
            self.register(spec)

    @staticmethod
    def _validate_spec(spec: ActionSpec) -> None:
        if not isinstance(spec, ActionSpec):
            raise InvalidActionError("expected an ActionSpec instance")
        # Namespace is validated by ActionSpec itself; re-assert defensively.
        namespace = spec.action_id.split(".", 1)[0]
        if namespace != spec.domain:
            raise InvalidActionError(
                f"action '{spec.action_id}' namespace does not match domain "
                f"'{spec.domain}'"
            )
        if spec.handler is None:
            raise InvalidActionError(
                f"action '{spec.action_id}' has no handler"
            )
        if not callable(spec.handler):
            raise InvalidActionError(
                f"action '{spec.action_id}' handler is not callable"
            )
        if not inspect.iscoroutinefunction(spec.handler):
            raise InvalidActionError(
                f"action '{spec.action_id}' handler must be an async coroutine"
            )
        for label, schema in (
            ("input_schema", spec.input_schema),
            ("output_schema", spec.output_schema),
        ):
            if not isinstance(schema, dict):
                raise InvalidActionError(
                    f"action '{spec.action_id}' {label} must be a JSON-schema dict"
                )
            if schema and "type" not in schema and "properties" not in schema:
                raise InvalidActionError(
                    f"action '{spec.action_id}' {label} must declare a 'type' "
                    "or 'properties'"
                )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        kit_ids: Optional[Iterable[str]] = None,
        package: str = DOMAINS_PACKAGE,
    ) -> List[str]:
        """Discover and register actions from installed domain kits.

        If ``kit_ids`` is provided, only those kits' ``<package>.<kit>.actions``
        modules are imported. Otherwise every subpackage of ``package`` that
        exposes ``ACTION_SPECS`` is discovered. Returns the list of newly
        registered action ids.

        Adding a new action to a kit (append to its ``ACTION_SPECS``) or adding
        a whole new kit package requires no change here.
        """
        before = set(self._specs)
        modules: List[str] = []
        if kit_ids is not None:
            modules = [f"{package}.{kit}.actions" for kit in kit_ids]
        else:
            modules = list(self._scan_domain_action_modules(package))

        for module_name in modules:
            self._load_module_specs(module_name)

        return sorted(set(self._specs) - before)

    @staticmethod
    def _scan_domain_action_modules(package_name: str = DOMAINS_PACKAGE) -> Iterable[str]:
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            return []
        found: List[str] = []
        for _finder, name, _ispkg in pkgutil.iter_modules(package.__path__):
            module_name = f"{package_name}.{name}.actions"
            if importlib.util.find_spec(module_name) is not None:
                found.append(module_name)
        return found

    def _load_module_specs(self, module_name: str) -> None:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            logger.debug("no action module '%s'", module_name)
            return
        specs = getattr(module, "ACTION_SPECS", None)
        if specs is None:
            logger.warning("module '%s' has no ACTION_SPECS", module_name)
            return
        for spec in specs:
            self.register(spec)

    # ------------------------------------------------------------------
    # Resolution / listing
    # ------------------------------------------------------------------
    def resolve(self, action_id: str) -> Optional[ActionSpec]:
        """Resolve an action by *exact* id. No fuzzy/fallback matching."""
        return self._specs.get(action_id)

    def has(self, action_id: str) -> bool:
        return action_id in self._specs

    def all(self) -> List[ActionSpec]:
        return [self._specs[k] for k in sorted(self._specs)]

    def list_permitted(self, context: ActionContext) -> List[ActionSpec]:
        """Return actions the given trusted context is allowed to see/run."""
        permitted: List[ActionSpec] = []
        for spec in self.all():
            if not context.allows_domain(spec.domain):
                continue
            if spec.permissions and not all(
                context.has_permission(p) for p in spec.permissions
            ):
                continue
            permitted.append(spec)
        return permitted

    def clear(self) -> None:
        self._specs.clear()
