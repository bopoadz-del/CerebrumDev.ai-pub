"""The Store gate's concurrency floor: every write path holds at 2x the pool.

Live 2026-10-10 (cycle 9 smoke B, build/plt_f9c91b85bce748c5): the writer's
``app/store.py`` ``save()`` held a pooled connection and called ``get()``,
which checked out a second one. Under the default SQLAlchemy pool (5 + 10)
twenty threads each held one connection and waited for another, and every
one of them ended in ``QueuePool limit ... connection timed out``. The
TESTER's emitted lifecycle suite caught it; the Store gate had no line that
would have. A product whose store deadlocks under its own threadpool must
not be certifiable by a gate that never writes twice at once.

``concurrent_writes_hold`` is that line, judged in the image (``subject:
runtime``). Everything it needs is read from the product's declared contract:

* the WRITE PATHS are the capabilities that declare a persisted entity --
  the same resolver ``cross_tenant_404`` and the round-trip probes use
  (``_persisting_caps``), minus declared placeholders;
* the WIDTH ``N`` is ``2 x`` the product's pool capacity (``pool_size +
  max_overflow`` read off the engine its ``app.db`` builds; the pool
  library's own defaults when the product configures none), and never fewer
  than the FastAPI sync threadpool -- the product's declared
  ``FASTAPI_SYNC_THREADPOOL`` and the runtime's own thread limiter;
* every path is written ``N`` times at once twice over: through the HTTP
  route the image serves, and through the store's declared write
  (``app.store.save(entity, record, tenant_id=...)``, the contract the
  emitted lifecycle suite and the round-trip probes call). The second leg
  exists because an ``async def`` route that calls a blocking store
  serialises every request on the event loop -- smoke B's route did
  (app/routers/capabilities.py:142,162) -- so HTTP alone never puts two
  writes into the store together, while a worker thread, a sync route or a
  second process will.

PASS only when every one of the ``N`` writes answers its declared success
(the accept statuses, never ``ok: false``) with its own stored id, and all
``N`` are stored (read back through the route; counted through the store).
FAIL names the leg, the path, ``N`` and the first error.

The source below is rendered into ``scripts/acceptance.py``; it uses the
harness's own ``_persisting_caps``, ``_post_accepting``, ``_created_id``,
``_declared_entity``, ``_models`` and ``CHECK_TENANT_TOKENS``. Standard
library only: a library the product loaded is read from ``sys.modules``,
never imported by the harness.
"""

from __future__ import annotations

#: How many times the pool's capacity the probe writes at once. At 2x, half
#: the writers must wait for a connection: a store that holds one while it
#: asks for another deadlocks at any width above the capacity.
POOL_FACTOR = 2

#: The pool a SQLAlchemy engine opens when the product configures none
#: (QueuePool: pool_size 5 + max_overflow 10). Used only when neither the
#: product's engine nor the loaded library can be read.
DEFAULT_POOL_CAPACITY = 15

#: How long one concurrent write may take before it counts as unanswered.
#: Above the pool's own default checkout timeout (30 s), so a pool timeout is
#: reported as the product's error rather than as the harness giving up.
PROBE_TIMEOUT_S = 120

CONCURRENT_WRITES_SRC = r'''
CONCURRENCY_POOL_FACTOR = __POOL_FACTOR__
CONCURRENCY_DEFAULT_POOL_CAPACITY = __DEFAULT_POOL_CAPACITY__
CONCURRENCY_TIMEOUT_S = __PROBE_TIMEOUT_S__


def _pool_capacity() -> Tuple[Optional[int], str]:
    """(pool_size + max_overflow, where it was read). None when unbounded or
    unreadable. Read off the engine the product's own app.db builds -- the
    one place that decides the backend -- never from its source text."""
    try:
        import app.db as _db
    except Exception as exc:
        return None, "app.db not importable (%s)" % type(exc).__name__
    eng = getattr(_db, "engine", None)
    if callable(eng) and not hasattr(eng, "pool"):
        try:
            eng = eng()
        except Exception as exc:
            return None, "app.db.engine() raised %s" % type(exc).__name__
    pool = getattr(eng, "pool", None)
    size = getattr(pool, "size", None)
    overflow = getattr(pool, "_max_overflow", None)
    if callable(size) and isinstance(overflow, int) and overflow >= 0:
        try:
            pool_size = int(size())
        except Exception:
            return None, "the engine's pool size is unreadable"
        return pool_size + overflow, "app.db pool_size=%d + max_overflow=%d" % (pool_size, overflow)
    if pool is None:
        return None, "app.db exposes no engine pool"
    return None, "app.db's %s has no bounded size" % type(pool).__name__


def _library_pool_capacity() -> Tuple[int, str]:
    """The pool library's own defaults, read from the copy the product
    loaded (never imported here: the harness is standard library only)."""
    import inspect

    module = sys.modules.get("sqlalchemy.pool")
    queue_pool = getattr(module, "QueuePool", None)
    if queue_pool is not None:
        try:
            params = inspect.signature(queue_pool.__init__).parameters
            size = int(params["pool_size"].default)
            overflow = int(params["max_overflow"].default)
            return size + overflow, "the loaded pool library's default %d + %d" % (size, overflow)
        except Exception:
            pass
    return CONCURRENCY_DEFAULT_POOL_CAPACITY, "the default pool capacity %d" % CONCURRENCY_DEFAULT_POOL_CAPACITY


def _declared_threadpool() -> Tuple[int, str]:
    """The widest sync threadpool the product declares or its runtime runs:
    app.store.FASTAPI_SYNC_THREADPOOL, the durability record in
    docs/data_lifecycle.json, and the loaded anyio's default thread limiter
    (the pool FastAPI runs sync routes on)."""
    found = []
    try:
        import app.store as _store

        value = getattr(_store, "FASTAPI_SYNC_THREADPOOL", None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            found.append((value, "app.store.FASTAPI_SYNC_THREADPOOL"))
    except Exception:
        pass
    for base in (ROOT, REPO):
        path = base / "docs" / "data_lifecycle.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        value = ((record or {}).get("durability") or {}).get("fastapi_sync_threadpool")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            found.append((value, "docs/data_lifecycle.json"))
        break
    anyio = sys.modules.get("anyio")
    if anyio is not None:
        try:
            async def _tokens():
                return anyio.to_thread.current_default_thread_limiter().total_tokens

            value = int(anyio.run(_tokens))
            if value > 0:
                found.append((value, "the runtime's sync thread limiter"))
        except Exception:
            pass
    if not found:
        return 0, "no declared threadpool"
    return max(found)


def _concurrency_width() -> Tuple[int, str]:
    """N, and how it was derived: POOL_FACTOR x the pool capacity, never
    fewer than the sync threadpool."""
    capacity, source = _pool_capacity()
    if capacity is None:
        why = source
        capacity, source = _library_pool_capacity()
        source = "%s (%s)" % (source, why)
    threads, thread_source = _declared_threadpool()
    width = max(CONCURRENCY_POOL_FACTOR * capacity, threads, 2)
    return width, "N=%d: %dx pool %d [%s]; sync threadpool %d [%s]" % (
        width, CONCURRENCY_POOL_FACTOR, capacity, source, threads, thread_source,
    )


def _at_once(fn, n: int) -> List[Tuple[bool, Any]]:
    """Run fn(0..n-1) on n threads released together. (ok, value or error)
    per call; a call still running at the deadline is an unanswered write."""
    import threading
    import time

    gate = threading.Barrier(n)
    results: List[Any] = [None] * n

    def _one(i: int) -> None:
        try:
            gate.wait(timeout=CONCURRENCY_TIMEOUT_S)
        except threading.BrokenBarrierError:
            pass
        try:
            results[i] = (True, fn(i))
        except Exception as exc:
            results[i] = (False, "%s: %s" % (type(exc).__name__, exc))

    threads = [threading.Thread(target=_one, args=(i,), daemon=True) for i in range(n)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + CONCURRENCY_TIMEOUT_S
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return [
        r if r is not None else (False, "no answer within %ss" % CONCURRENCY_TIMEOUT_S)
        for r in results
    ]


def _first_error(errors: List[str]) -> str:
    return (errors[0] if errors else "")[:300]


class _CapturingClient(_CreateClient):
    """The harness client that remembers the last body it posted: the
    payload the product accepted, after the shared builder's corrections."""

    def __init__(self, http: _Http):
        super().__init__(http)
        self.sent = None

    def post(self, path, json=None, headers=None):
        self.sent = dict(json or {})
        return super().post(path, json=json, headers=headers)


def _http_leg(http: _Http, path: str, payload: Dict[str, Any], headers: Dict[str, str], n: int) -> str:
    """N creates through the served route at once. '' when every one is
    accepted with its own stored id and reads back; else why not."""

    def _create(_i: int):
        resp = http.request("post", path, json=dict(payload), headers=headers, timeout=CONCURRENCY_TIMEOUT_S)
        record_id, refusal = _created_id(resp)
        if record_id is None:
            raise RuntimeError(refusal or "no stored id")
        return record_id

    outcomes = _at_once(_create, n)
    errors = [str(v) for ok, v in outcomes if not ok]
    if errors:
        return "%d of %d concurrent creates failed; first error: %s" % (len(errors), n, _first_error(errors))
    ids = [v for _ok, v in outcomes]
    distinct = {str(i) for i in ids}
    if len(distinct) != n:
        return "%d concurrent creates answered success but only %d distinct stored ids" % (n, len(distinct))
    for record_id in ids:
        read = http.request("get", "%s/%s" % (path, record_id), headers=headers, timeout=CONCURRENCY_TIMEOUT_S)
        if read.status_code != 200:
            return "created id %s reads back HTTP %s (want 200): %d accepted, not all stored" % (
                record_id, read.status_code, n,
            )
    return ""


def _store_leg(entity: str, payload: Dict[str, Any], tenant_id: str, n: int) -> str:
    """N calls of the store's declared write at once, from N threads.
    '' when every one returns its stored record and all N are counted."""
    try:
        import app.store as _store
    except Exception as exc:
        return "app.store does not import: %s: %s" % (type(exc).__name__, exc)
    save = getattr(_store, "save", None)
    if not callable(save):
        return "app.store declares no save(entity, record, tenant_id=)"
    list_all = getattr(_store, "list_all", None)

    def _count() -> Optional[int]:
        if not callable(list_all):
            return None
        try:
            return len(list_all(entity, tenant_id=tenant_id))
        except Exception:
            return None

    before = _count()
    outcomes = _at_once(lambda _i: save(entity, dict(payload), tenant_id=tenant_id), n)
    errors = [str(v) for ok, v in outcomes if not ok]
    if errors:
        return "%d of %d concurrent saves failed; first error: %s" % (len(errors), n, _first_error(errors))
    ids = [v.get(__RECORD_ID_KEY__) if isinstance(v, dict) else None for _ok, v in outcomes]
    if all(i not in (None, "") for i in ids) and len({str(i) for i in ids}) != n:
        return "%d concurrent saves returned only %d distinct ids" % (n, len({str(i) for i in ids}))
    after = _count()
    if before is not None and after is not None and after - before != n:
        return "%d concurrent saves returned, but the store holds %d more" % (n, after - before)
    return ""


def check_concurrent_writes_hold(http: _Http) -> Tuple[str, str]:
    """Every write path takes N simultaneous creates: N = 2x the product's
    pool capacity, never fewer than its sync threadpool. A store that holds
    a pooled connection while it checks out another (save() calling get()
    on a fresh connection) deadlocks here and times out."""
    caps = _persisting_caps()
    if not caps:
        return "SKIP", (
            "no capability declares a persisted entity -- there is no write "
            "path to drive concurrently on this product"
        )
    n, derivation = _concurrency_width()
    token, _, tenant_id = CHECK_TENANT_TOKENS.split(",")[0].partition(":")
    headers = {"Authorization": "Bearer " + token}
    capture = _CapturingClient(http)
    globals()["client"] = capture
    models = _models()
    judged, refused = [], []
    for cap in caps:
        path = "/v1/" + cap
        capture.sent = None
        resp, _corrections = _post_accepting(path, {}, headers, cap)
        record_id, refusal = _created_id(resp)
        if record_id is None:
            # Not a write path this product can take a single create on:
            # cross_tenant_404 and the round-trip judge that. Here it is
            # reported, never driven.
            refused.append("%s: %s" % (cap, refusal))
            continue
        payload = dict(capture.sent or {})
        problem = _http_leg(http, path, payload, headers, n)
        if problem:
            return "FAIL", ("POST %s at N=%d: %s (%s)" % (path, n, problem, derivation))[:900]
        entity, _declared = _declared_entity(cap, models.get(cap))
        problem = _store_leg(str(entity), payload, tenant_id, n)
        if problem:
            return "FAIL", (
                "app.store.save(%r) at N=%d (the write behind POST %s): %s (%s)"
                % (entity, n, path, problem, derivation)
            )[:900]
        judged.append(cap)
    if not judged:
        return "FAIL", (
            "no write path accepted a single create, so none could be driven "
            "concurrently -- " + "; ".join(refused)
        )[:900]
    note = ("; not driven (refused a single create): " + "; ".join(refused)) if refused else ""
    return "PASS", (
        "%d write path(s) x %d concurrent creates, HTTP and store: every one "
        "accepted with its own id and stored (%s)%s" % (len(judged), n, derivation, note)
    )[:900]
'''


def render_concurrent_writes() -> str:
    """The probe's source with this module's constants and the declared
    record-id key bound. Interpolated into the harness as a value, so its
    braces are literal."""
    from app.factory.build.rejection_contract import RECORD_ID_KEY

    return (
        CONCURRENT_WRITES_SRC.replace("__RECORD_ID_KEY__", repr(RECORD_ID_KEY))
        .replace("__POOL_FACTOR__", repr(POOL_FACTOR))
        .replace("__DEFAULT_POOL_CAPACITY__", repr(DEFAULT_POOL_CAPACITY))
        .replace("__PROBE_TIMEOUT_S__", repr(PROBE_TIMEOUT_S))
    )
