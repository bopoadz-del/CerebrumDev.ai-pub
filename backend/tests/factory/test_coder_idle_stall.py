"""No-progress (idle) coder wall -- a leg that stalls inside its time wall is
stopped, while a slow-but-streaming build (progress signal still advancing) is
not. Keyed on the runner-stamped ``coder_seconds_since_progress``; inert when
that signal is absent (no premature kill)."""
from __future__ import annotations

import pytest

from app.factory.build.budget_inspect import (
    CODER_IDLE_WALL_ENV,
    NAMED_BLOCKER_CODER_STALLED,
    STAGE_1_S,
    coder_idle_stalled,
    coder_idle_wall_s,
    coder_stall_reason,
    inspect_decision,
)
from app.factory.llm_watchdog import DEFAULT_CALL_TIMEOUT_S


# --- coder_idle_wall_s -------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "not-a-number", ""])
def test_idle_wall_defaults_to_the_band_when_unset_or_garbage(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(CODER_IDLE_WALL_ENV, raising=False)
    else:
        monkeypatch.setenv(CODER_IDLE_WALL_ENV, raw)
    assert coder_idle_wall_s() == float(DEFAULT_CALL_TIMEOUT_S)


@pytest.mark.parametrize("raw, expected", [("300", 300.0), ("0", 1.0), ("-5", 1.0)])
def test_idle_wall_honours_env_with_a_1s_floor(monkeypatch, raw, expected):
    monkeypatch.setenv(CODER_IDLE_WALL_ENV, raw)
    assert coder_idle_wall_s() == expected


# --- coder_idle_stalled ------------------------------------------------------

def test_in_flight_leg_with_no_progress_past_the_wall_is_stalled():
    snap = {"cli_in_flight": True, "coder_seconds_since_progress": 1300}
    assert coder_idle_stalled(snap, idle_s=1200) is True


def test_in_flight_leg_still_progressing_is_not_stalled():
    snap = {"cli_in_flight": True, "coder_seconds_since_progress": 10}
    assert coder_idle_stalled(snap, idle_s=1200) is False


@pytest.mark.parametrize("snap", [
    {"cli_in_flight": False, "coder_seconds_since_progress": 9999},  # not a live leg
    {"cli_in_flight": True},                                          # signal absent
    {"cli_in_flight": True, "coder_seconds_since_progress": None},    # signal None
    {"cli_in_flight": True, "coder_seconds_since_progress": "x"},     # unparseable
])
def test_stall_is_never_asserted_without_a_live_leg_and_a_real_signal(snap):
    assert coder_idle_stalled(snap, idle_s=1) is False


def test_slot_waiting_also_counts_as_a_live_leg():
    snap = {"slot_waiting": True, "coder_seconds_since_progress": 50}
    assert coder_idle_stalled(snap, idle_s=10) is True


# --- coder_stall_reason ------------------------------------------------------

def test_stall_reason_names_the_blocker_and_the_idle_age():
    reason = coder_stall_reason({"coder_seconds_since_progress": 1500}, "stage_2")
    assert NAMED_BLOCKER_CODER_STALLED in reason
    assert "1500s" in reason and "stage_2" in reason


# --- inspect_decision integration -------------------------------------------

@pytest.mark.parametrize("snap", [
    {"cli_in_flight": True, "coder_seconds_since_progress": 2000},
    {"slot_waiting": True, "coder_seconds_since_progress": 1300},
])
def test_inspect_decision_stops_a_stalled_leg_and_denies_any_wall(snap):
    decided = inspect_decision(
        elapsed_s=STAGE_1_S, current_wall_s=STAGE_1_S, snapshot=snap, stage="stage_1",
    )
    assert decided["decision"] == "coder_stalled"
    assert decided["next_wall_s"] is None
    assert decided["continue"] is False
    assert NAMED_BLOCKER_CODER_STALLED in decided["reason"]


@pytest.mark.parametrize("since", [9999, 5000])
def test_a_store_green_product_is_never_cut_by_the_idle_wall(since):
    # pilot_ready outranks a stall: a finished product is not killed.
    snap = {"cli_in_flight": True, "coder_seconds_since_progress": since, "pilot_ready": True}
    decided = inspect_decision(
        elapsed_s=STAGE_1_S, current_wall_s=STAGE_1_S, snapshot=snap, stage="stage_1",
    )
    assert decided["decision"] == "already_pilot_ready"


@pytest.mark.parametrize("snap", [
    {"cli_in_flight": True, "agent_written": 0},            # signal key absent
    {"cli_in_flight": True, "coder_seconds_since_progress": None},  # stamped None
])
def test_absent_progress_signal_leaves_the_normal_decision_untouched(snap):
    # No usable coder_seconds_since_progress -> not a stall -> normal path.
    decided = inspect_decision(
        elapsed_s=STAGE_1_S, current_wall_s=STAGE_1_S, snapshot=snap, stage="stage_1",
    )
    assert decided["decision"] != "coder_stalled"
