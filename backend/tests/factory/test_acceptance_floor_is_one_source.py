"""One spec, two consumers — and a test that fails when they drift.

The Store gate graded every build against thirteen checks the coder was never
told. Measured, not assumed: before this landed, grepping the writer prompt
and the C-BRIEF for ``no_token_401`` returned zero, and the same for every
other check id. So the agent discovered the floor by failing it, and each
discovery cost a full writer pass.

Putting the rules in the brief alone would move the trust back to the author,
which is what the gate exists to remove. So the floor lives in one file and
both sides read it: the prompt renders ``requirement``, the gate takes its
checklist from the ids. This module is the part that keeps that true — if
either consumer grows its own copy, this goes red.
"""

from __future__ import annotations

import pathlib

from app.factory.build.acceptance_floor import (
    check_ids,
    checks,
    floor_hash,
    floor_path,
    floor_version,
    gate_fns,
    render_for_prompt,
    requirements,
)
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
)
from app.factory.build.writer_prompt import render_writer_prompt


class _Blueprint:
    product_id = "bakery-operations"
    product_name = "Bakery Branch Operations Platform"
    vertical = "bakery"
    summary = "branch ops"


def _prompt() -> str:
    return render_writer_prompt(_Blueprint(), brief="a bakery")


class TestTheGateReadsTheFloor:
    def test_the_checklist_is_not_a_second_copy(self):
        assert ACCEPTANCE_CHECK_NAMES == check_ids()

    def test_the_required_count_matches_what_the_floor_actually_holds(self):
        assert ACCEPTANCE_REQUIRED == len(check_ids())

    def test_authorship_floor_stays_last(self):
        """The gate's own assertion, restated where the order is decided."""
        assert check_ids()[-1] == "authorship_floor"


class TestTheCoderIsToldTheFloor:
    def test_every_check_the_gate_grades_appears_in_the_prompt(self):
        """The regression this file exists for.

        Before the floor was one file, this assertion failed on all 13.
        """
        text = _prompt()
        missing = [cid for cid in check_ids() if cid not in text]
        assert not missing, f"graded on but never told: {missing}"

    def test_the_requirement_text_is_rendered_not_paraphrased(self):
        text = _prompt()
        missing = [r for r in requirements() if r not in text]
        assert not missing, f"requirement reworded between spec and prompt: {missing}"

    def test_the_prompt_names_the_floor_version(self):
        """A prompt and a gate on different versions is the drift itself."""
        assert f"v{floor_version()}" in render_for_prompt()
        assert f"v{floor_version()}" in _prompt()

    def test_the_floor_is_stated_as_a_bar_not_as_advice(self):
        assert "not advice" in _prompt()


class TestTheSpecIsWellFormed:
    def test_every_check_says_both_what_to_build_and_what_is_verified(self):
        for check in checks():
            assert check["requirement_text"].strip(), check["id"]
            assert check["check"].strip(), check["id"]

    def test_requirements_are_written_for_the_builder_not_the_grader(self):
        """A requirement that only restates the check teaches the coder
        nothing it could not have guessed from the failure message."""
        for check in checks():
            assert check["requirement_text"].strip() != check["check"].strip(), check["id"]

    def test_ids_are_unique(self):
        assert len(set(check_ids())) == len(check_ids())


# --- P3: the drift lock ------------------------------------------------------


class TestNeitherConsumerCanDriftFromTheFile:
    def test_both_renders_are_pinned_to_the_same_floor_bytes(self):
        """A change that reaches one consumer and not the other is red.

        Both renders are derived here from the same hash, so this cannot be
        satisfied by updating one side.
        """
        digest = floor_hash()
        assert digest.startswith("sha256:")
        brief = render_for_prompt()
        from app.factory.build.store_acceptance import render_acceptance_script

        gate = render_acceptance_script()
        for cid in check_ids():
            assert cid in brief, f"{cid} missing from the brief render"
            assert cid in gate, f"{cid} missing from the gate render"
        assert floor_hash() == digest, "the floor changed mid-test"

    def test_the_harness_defines_every_gate_fn_the_floor_names(self):
        from app.factory.build.store_acceptance import render_acceptance_script

        gate = render_acceptance_script()
        missing = [fn for fn in gate_fns() if f"def {fn}(" not in gate]
        assert not missing, f"floor names gate fns the harness does not define: {missing}"

    def test_no_hand_kept_check_id_list_outside_the_floor(self):
        """The grep gate, as a test.

        A second roster is the thing the floor file abolishes: it drifts the
        moment a check is added, and the build is then graded against a list
        nobody updated. Defining check_<id> is not enumerating -- the
        implementations have to live somewhere. Quoting five or more ids as
        string literals in one file is.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[2] / "app"
        ids = set(check_ids())
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            quoted = {
                cid
                for cid in ids
                if re.search(r"[\"']" + re.escape(cid) + r"[\"']", text)
            }
            if len(quoted) >= 5:
                offenders.append(f"{path.relative_to(root)} ({len(quoted)} ids)")
        assert offenders == [], (
            "check ids enumerated outside acceptance_floor.v2.json: "
            + ", ".join(offenders)
        )

    def test_the_floor_file_is_where_the_ids_live(self):
        text = floor_path().read_text(encoding="utf-8")
        for cid in check_ids():
            assert cid in text


class TestTheFloorIsProductAgnostic:
    """The floor grades every product the Factory makes, so it may not
    describe any one of them.

    ``negative_floor`` shipped asking for the boundary case "closed exactly
    at due_at". ``due_at`` is a column in one facility-management build,
    carried in verbatim from an audit of that export -- so every other
    product would have been handed a rule naming a field it does not have.

    The first guard for this was a list of domain nouns I typed out, which
    is the same mistake one level up: a hand-kept list that goes stale the
    day a new vertical ships. Both halves are derived instead. A DOMAIN is
    whatever the Factory already calls one -- the ``vertical`` of its own
    blueprints, and the Store's ``<domain>_v2`` registry entries. A FIELD is
    caught by shape, not by name, so a column nobody has seen yet is caught
    on the first build that mentions it.
    """

    @staticmethod
    def _factory_domains() -> set:
        """Domains as WHOLE names, never split into words.

        Splitting on "_" turned real_estate into "real", which then matched
        "a real outbound delivery" and "real DDL" -- a guard that fires on
        ordinary English is a guard that gets deleted. A domain is the whole
        name, in both the underscore and spaced spellings.
        """
        import re as _re

        here = pathlib.Path(__file__).resolve()
        repo = here.parents[3]
        names = set()
        for yaml_path in (repo / "blueprints").rglob("*.yaml"):
            for line in yaml_path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines():
                m = _re.match(r"\s*vertical:\s*['\"]?([a-z_]+)", line)
                if m:
                    names.add(m.group(1))
        try:
            from app.factory.dual_registry import _default_blocks_root

            registry = _default_blocks_root() / "block_registry"
            for entry in registry.iterdir() if registry.is_dir() else []:
                if entry.is_dir() and entry.name.endswith("_v2"):
                    names.add(entry.name[: -len("_v2")])
        except Exception:
            pass
        names -= {"product", "field_operations"}  # too generic to mean a domain
        out = set()
        for name in names:
            if len(name) > 3:
                out.add(name)
                out.add(name.replace("_", " "))
        return out

    #: A domain column, by shape: the suffixes a generated entity uses.
    FIELD_SHAPE = r"(?<![a-z0-9_])[a-z]+_(?:at|id|no|ref|code|date|url)(?![a-z0-9_])"

    #: Platform vocabulary the floor is entitled to name.
    PLATFORM_WORDS = frozenset(
        {"database_url", "redis_url", "storage_path", "blocks_unavailable"}
    )

    def _floor_text(self) -> str:
        return " ".join(
            f"{c['requirement_text']} {c['brief_render']} {c['check']}"
            for c in checks()
        ).lower()

    def test_no_domain_the_factory_knows_appears_in_the_floor(self):
        domains = self._factory_domains()
        assert domains, "could not derive the Factory's domains; the guard is blind"
        text = self._floor_text()
        found = sorted(w for w in domains if w in text)
        assert not found, (
            "the floor names a domain the Factory builds for: "
            + ", ".join(found)
            + " -- every product is graded against this file"
        )

    def test_no_domain_column_shape_appears_in_the_floor(self):
        """Catches due_at, invoice_no, tenant_ref without knowing them."""
        import re as _re

        found = {
            m.group(0)
            for m in _re.finditer(self.FIELD_SHAPE, self._floor_text())
            if m.group(0) not in self.PLATFORM_WORDS
        }
        assert not found, f"the floor names a product's columns: {sorted(found)}"

    def test_the_floor_reaches_the_prompt_without_naming_a_product(self):
        import re as _re

        text = render_for_prompt().lower()
        domains = sorted(w for w in self._factory_domains() if w in text)
        columns = sorted(
            {
                m.group(0)
                for m in _re.finditer(self.FIELD_SHAPE, text)
                if m.group(0) not in self.PLATFORM_WORDS
            }
        )
        assert not domains and not columns, (
            f"product vocabulary reaches the coder's prompt: {domains} {columns}"
        )


class TestAdvisoryLinesReportButDoNotVeto:
    """Three checks demand evidence no pipeline step produces yet. Demoted, not
    removed: the requirement still reaches the writer, the check still runs and
    still says FAIL and why, and only the veto is gone. Owner's decision,
    2026-09-26 (Gate 3b), over building the three evidence bridges."""

    def test_the_advisory_set_is_exactly_the_three_the_owner_demoted(self):
        from app.factory.build.acceptance_floor import advisory_ids

        assert set(advisory_ids()) == {
            "one_live_connector", "backup_restore_roundtrip", "bench_p95"
        }, "an advisory line was added or removed without this test changing"

    def test_advisory_lines_are_on_the_floor_and_the_count_is_unchanged(self):
        """Demotion does not shrink the floor: k/N still counts 21 lines and every
        advisory id is one of them. A line that vanished from the floor would be
        neither graded nor told to the writer, which is a different decision."""
        from app.factory.build.acceptance_floor import advisory_ids, check_ids

        assert set(advisory_ids()) <= set(check_ids())
        assert len(check_ids()) == ACCEPTANCE_REQUIRED
        assert "authorship_floor" not in advisory_ids()

    def test_the_gate_and_the_stamped_harness_read_the_same_advisory_roster(self):
        """Two consumers, one source. A hand-kept ADVISORY list in the rendered
        harness is the second copy the floor file exists to abolish."""
        from app.factory.build.acceptance_floor import advisory_ids
        from app.factory.build.store_acceptance import (
            ACCEPTANCE_ADVISORY_NAMES, render_acceptance_script,
        )

        assert ACCEPTANCE_ADVISORY_NAMES == set(advisory_ids())
        rendered = render_acceptance_script()
        for name in advisory_ids():
            assert repr(name) in rendered.split("ADVISORY = [", 1)[1].split("]", 1)[0]

    def test_an_advisory_fail_parses_as_a_skip_that_keeps_its_reason(self):
        from app.factory.build.store_acceptance import harness_record, parse_acceptance_output

        text = harness_record("bench_p95", "FAIL", "STORE_BENCH_P95_MS unset: p95 was not measured")
        report = parse_acceptance_output(text)
        line = next(l for l in report.lines if l.name == "bench_p95")
        assert line.status == "SKIP"
        assert line.satisfied
        assert "p95 was not measured" in line.detail, "the reason must survive demotion"
        assert "advisory" in line.detail

    def test_a_floor_fail_still_fails_the_build(self):
        """The companion: demotion must not have quietly softened the floor.
        A non-advisory FAIL is untouched and still vetoes."""
        from app.factory.build.store_acceptance import harness_record, parse_acceptance_output

        report = parse_acceptance_output(harness_record("no_token_401", "FAIL", "HTTP 200 without a token"))
        line = next(l for l in report.lines if l.name == "no_token_401")
        assert line.status == "FAIL"
        assert not line.satisfied
        assert not report.ok

    def test_all_three_advisory_lines_failing_does_not_veto_an_otherwise_green_build(self):
        """The whole point: a build red ONLY on the three unproducible lines is green."""
        from app.factory.build.store_acceptance import (
            ACCEPTANCE_ADVISORY_NAMES, ACCEPTANCE_CHECK_NAMES, harness_record, harness_total,
            parse_acceptance_output,
        )

        lines = []
        for name in ACCEPTANCE_CHECK_NAMES:
            status = "FAIL" if name in ACCEPTANCE_ADVISORY_NAMES else "PASS"
            lines.append(harness_record(name, status, "synthetic"))
        lines.append(harness_total(ACCEPTANCE_REQUIRED, ACCEPTANCE_REQUIRED))
        report = parse_acceptance_output("\n".join(lines) + "\n")
        assert report.ok, [l for l in report.lines if not l.satisfied]


# ── the authorship stamp is one source too ─────────────────────────────────
#
# Live 2026-10-01 (automotive_aiops, cerebrum-builds run 36794735083):
# acceptance scored 20/21 with the only failure
# ``authorship_floor — authored=0 below need>=5`` while the SAME run reported
# ``handler_bodies_distinct — 10 distinct handle() bodies``. All ten handlers
# carried the writer's stamp; none matched the private substring list the
# rendered check scanned for (``CODER_MODEL`` / ``coding agent`` /
# ``coder CLI``), because that list predates the CodeWhale worker. The
# canonical detector already lives in authorship.py and accepts ``codewhale``
# — so a second copy here is the bug, exactly as this module's docstring says.

WRITER_SOURCE = "codewhale exec"


def _product(tmp_path, source=None, *, docstring_sentence="", n=5):
    """A product tree whose handlers carry ``AUTHORED_BY = source`` (or no
    marker when source is None) and, optionally, a docstring sentence."""
    root = tmp_path / "product"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "__init__.py").write_text("", encoding="utf-8")
    marker = f"AUTHORED_BY = {source!r}\n" if source is not None else ""
    for i in range(n):
        (actions / f"cap_{i}.py").write_text(
            f'"""Handler for cap_{i}.\n\n{docstring_sentence}\n"""\n\n'
            f'CAPABILITY_ID = "cap_{i}"\n{marker}\n\ndef handle(payload):\n    return {{}}\n',
            encoding="utf-8",
        )
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    return root


def _rendered_authorship_check(root):
    """The REAL rendered check, with ROOT resolved to *root*."""
    from app.factory.build.store_acceptance import render_acceptance_script

    ns = {
        "__name__": "acceptance_under_test",
        "__file__": str(root / "scripts" / "acceptance.py"),
    }
    exec(compile(render_acceptance_script(), "acceptance.py", "exec"), ns)
    return ns["check_authorship_floor"]


class TestAuthorshipReadsTheWritersActualStamp:
    def test_codewhale_marked_handlers_are_counted(self, tmp_path):
        root = _product(tmp_path, WRITER_SOURCE)
        status, detail = _rendered_authorship_check(root)()
        assert status == "PASS", detail

    def test_a_coder_cli_marker_counts(self, tmp_path):
        root = _product(tmp_path, "coder CLI")
        status, detail = _rendered_authorship_check(root)()
        assert status == "PASS", detail

    def test_a_docstring_sentence_alone_counts_for_nothing(self, tmp_path):
        """No prose reading: the old stamp sentence with no marker is not
        authorship, however it is worded."""
        root = _product(
            tmp_path, None,
            docstring_sentence="Written by the factory WRITER role (codewhale exec)",
        )
        status, detail = _rendered_authorship_check(root)()
        assert status == "FAIL", detail

    def test_unstamped_template_handlers_do_not_count(self, tmp_path):
        """The floor is not weakened: a templated tree still fails."""
        root = _product(tmp_path, "deterministic contract template")
        status, detail = _rendered_authorship_check(root)()
        assert status == "FAIL", detail

    def test_the_rendered_check_grew_no_private_stamp_list(self):
        from app.factory.build.store_acceptance import render_acceptance_script

        script = render_acceptance_script()
        assert 'if "CODER_MODEL" in text' not in script
        # One source, rendered: the harness carries the Factory's canonical
        # marker reader and agent vocabulary verbatim (a product never ships
        # the Factory package to import them from).
        from app.factory.build.authorship import (
            AGENT_SOURCE_PREFIXES,
            RENDERED_MARKER_READER,
        )

        assert RENDERED_MARKER_READER in script
        assert repr(tuple(AGENT_SOURCE_PREFIXES)) in script
        assert "app.factory" not in script
