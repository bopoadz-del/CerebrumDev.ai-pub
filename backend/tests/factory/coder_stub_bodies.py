"""Handler bodies for tests that stand in for coder output.

A stub is not free to return anything. The WRITER gate refuses a handler
that declares blocks in ``BLOCK_IDS`` and never calls them — that is F11,
and it is the shape LotDesk shipped, where ``capture`` and ``team`` were
bound and never invoked. A stub returning a canned dict has exactly that
defect, so using one would assert the factory accepts what it must reject.

These bodies invoke every declared block and fail closed on a block error,
which is what a correct coder handler does. Anything a test needs to assert
about coder wiring rides along in ``marker``, which is spliced into the
success envelope literal so a header search for it still matches.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def invoking_handler_body(marker: Optional[Dict[str, Any]] = None) -> str:
    """A handler body that calls every block in BLOCK_IDS and fails closed.

    ``marker`` adds keys to the success envelope, written inline so the
    emitted source literally contains e.g. ``"agent": True`` — tests grep the
    generated handler for their own marker.
    """
    extras = "".join(
        f'            "{key}": {value!r},\n' for key, value in (marker or {}).items()
    )
    # Shape the payload per block from the block's OWN manifest, the way a
    # real agent does. The body used to forward the raw capability payload to
    # every block, which only an always-ok fake accepts: the Store's real
    # estate_registry answers "missing required field(s): id" and "unknown
    # field(s): reference". The Factory holds no blocks, so the stub has to
    # be good enough for the Store's.
    return (
        "    import json as _json\n"
        "    import re as _re\n"
        "    import uuid as _uuid\n"
        "    from pathlib import Path as _Path\n"
        "\n"
        "    def _for_block(block_id):\n"
        "        manifest = (\n"
        "            _Path(__file__).resolve().parents[2]\n"
        "            / 'vendor' / 'blocks' / block_id / 'block.json'\n"
        "        )\n"
        "        try:\n"
        "            inputs = _json.loads(\n"
        "                manifest.read_text(encoding='utf-8')\n"
        "            ).get('inputs') or []\n"
        "        except (OSError, ValueError):\n"
        "            return payload\n"
        "        named = [\n"
        "            i for i in inputs\n"
        "            if isinstance(i, dict)\n"
        "            and i.get('name') not in (None, 'action', 'input')\n"
        "        ]\n"
        "        if not named:\n"
        "            return payload\n"
        "        shaped = {}\n"
        "        for spec in named:\n"
        "            name = spec['name']\n"
        "            # A same-named capability field is only the block's input when\n"
        "            # its type fits: readiness_engine's optional 'state' is a map,\n"
        "            # and a capability's own string 'state' is not that.\n"
        "            _fits = name in payload and not (\n"
        "                spec.get('type') == 'json'\n"
        "                and not isinstance(payload[name], (dict, list))\n"
        "            )\n"
        "            if _fits:\n"
        "                shaped[name] = payload[name]\n"
        "            elif spec.get('required'):\n"
        "                # Unique per call: the real estate_registry rejects a\n"
        "                # duplicate record id, which the always-ok fake never did.\n"
        "                # A manifest states a list-of-records shape in its\n"
        "                # description -- '[{id, required}] checklist' -- and the\n"
        "                # real blocks enforce it (readiness_engine: 'checklist\n"
        "                # must be a list of {id, required} items'). Follow it,\n"
        "                # as an agent reading the contract would.\n"
        "                _shape = _re.match(r'\\s*\\[\\{([^}]*)\\}\\]', str(spec.get('description') or ''))\n"
        "                if _shape:\n"
        "                    _rec = {}\n"
        "                    for _key in [k.strip() for k in _shape.group(1).split(',') if k.strip()]:\n"
        "                        if _key == 'required':\n"
        "                            _rec[_key] = True\n"
        "                        elif _key == 'value':\n"
        "                            _rec[_key] = 1\n"
        "                        elif _key == 'status':\n"
        "                            _rec[_key] = 'ok'\n"
        "                        else:\n"
        "                            _rec[_key] = 'sample-' + _uuid.uuid4().hex[:10]\n"
        "                    shaped[name] = [_rec]\n"
        "                    continue\n"
        "                shaped[name] = (\n"
        "                    dict(payload) if spec.get('type') == 'json'\n"
        "                    else 'sample-' + _uuid.uuid4().hex[:10]\n"
        "                )\n"
        "        # 'Map of item id -> state': key it by the ids of the records built\n"
        "        # above. The Store's readiness_engine marks 'state' optional in its\n"
        "        # manifest but refuses a call without it ('state must be a map').\n"
        "        for spec in named:\n"
        "            name = spec['name']\n"
        "            if name in shaped or spec.get('type') != 'json':\n"
        "                continue\n"
        "            if not str(spec.get('description') or '').lower().startswith('map of'):\n"
        "                continue\n"
        "            shaped[name] = {\n"
        "                rec['id']: 'ok'\n"
        "                for value in list(shaped.values())\n"
        "                if isinstance(value, list)\n"
        "                for rec in value\n"
        "                if isinstance(rec, dict) and 'id' in rec\n"
        "            }\n"
        "        return shaped\n"
        "\n"
        "    results = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        results[block_id] = execute(\n"
        "            block_id, _for_block(block_id),\n"
        "            action=BLOCK_DEFAULT_ACTIONS.get(block_id),\n"
        "        )\n"
        "    failed = {\n"
        "        b: r for b, r in results.items()\n"
        '        if isinstance(r, dict) and r.get("status") == "error"\n'
        "    }\n"
        "    if failed:\n"
        '        return {"ok": False, "capability": CAPABILITY_ID,\n'
        '                "error": str(failed)[:200]}\n'
        "    return {\n"
        '            "ok": True,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "results": results,\n'
        + extras
        + "    }"
    )
