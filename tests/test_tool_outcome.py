from __future__ import annotations

from typing import Any

import pytest

from gemia.errors import RECOVERY_SWITCH_TOOL, ToolError
from gemia.tool_outcome import (
    ToolOutcome,
    classify_tool_exception,
    classify_tool_result,
)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"exit_code": 0},
        {"exit_code": "0"},
        {"timed_out": False},
        {"error": ""},
        {"error": "   "},
        {"ok": True},
        {"ok": False},
        {"success": True},
        {"success": False},
        {"applied": True},
        {"logged": False, "reason": "empty"},
    ],
)
def test_success_boundaries_do_not_infer_failure(payload: dict[str, Any]) -> None:
    outcome = classify_tool_result(payload)

    assert outcome.state == "success"
    assert outcome.raw_payload is payload
    assert outcome.error_code is None
    assert outcome.recovery is None


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"error_code": "E_ARG"}, "E_ARG"),
        ({"exit_code": 1}, "E_PROCESS_EXIT"),
        ({"exit_code": -9}, "E_PROCESS_EXIT"),
        ({"exit_code": "2"}, "E_PROCESS_EXIT"),
        ({"timed_out": True}, "E_TIMEOUT"),
        ({"status": "failed"}, "E_TOOL_FAILED"),
        ({"status": " ERROR "}, "E_TOOL_FAILED"),
        ({"status": "cancelled"}, "E_TOOL_FAILED"),
        ({"status": "canceled"}, "E_TOOL_FAILED"),
        ({"error": "render failed"}, "E_TOOL_FAILED"),
        ({"error": {"message": "render failed"}}, "E_TOOL_FAILED"),
    ],
)
def test_explicit_failure_markers_win(
    payload: dict[str, Any], expected_code: str | None
) -> None:
    outcome = classify_tool_result(payload)

    assert outcome.state == "failure"
    assert outcome.is_failure is True
    assert outcome.raw_payload is payload
    assert outcome.error_code == expected_code


def test_failure_preserves_error_code_and_recovery() -> None:
    payload = {
        "error": "feature unavailable",
        "error_code": "E_UNAVAILABLE",
        "recovery": "switch_tool",
        "valid_options": ["fallback"],
    }

    outcome = classify_tool_result(payload)

    assert outcome.state == "failure"
    assert outcome.raw_payload is payload
    assert outcome.error_code == "E_UNAVAILABLE"
    assert outcome.recovery == "switch_tool"


def test_failure_marker_overrides_pending_or_partial_status() -> None:
    pending_failure = classify_tool_result(
        {"status": "running", "error_code": "E_REMOTE"}
    )
    partial_failure = classify_tool_result(
        {"status": "partial", "error": "one branch failed"}
    )

    assert pending_failure.state == "failure"
    assert partial_failure.state == "failure"


@pytest.mark.parametrize(
    "status",
    ["pending", "queued", "submitted", "running", "processing", "question_emitted"],
)
def test_pending_statuses(status: str) -> None:
    payload = {"status": status}

    outcome = classify_tool_result(payload)

    assert outcome.state == "pending"
    assert outcome.raw_payload is payload


def test_pending_status_is_case_and_whitespace_insensitive() -> None:
    assert classify_tool_result({"status": " Submitted "}).state == "pending"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "partial"},
        {"partial": True},
    ],
)
def test_partial_results(payload: dict[str, Any]) -> None:
    assert classify_tool_result(payload).state == "partial"


@pytest.mark.parametrize(
    "payload",
    [
        {"applied": False},
        {"applied": False, "error": ""},
        {"status": "noop"},
        {"status": "no_op"},
    ],
)
def test_noop_results_are_not_failures(payload: dict[str, Any]) -> None:
    outcome = classify_tool_result(payload)

    assert outcome.state == "noop"
    assert outcome.is_failure is False


@pytest.mark.parametrize(
    "payload",
    [
        {"applied": False, "error_code": "E_NOT_APPLIED"},
        {"applied": False, "error": "could not apply"},
        {"applied": False, "status": "failed"},
    ],
)
def test_applied_false_with_failure_marker_is_failure(payload: dict[str, Any]) -> None:
    assert classify_tool_result(payload).state == "failure"


def test_explicit_lifecycle_status_precedes_applied_false() -> None:
    assert (
        classify_tool_result({"status": "running", "applied": False}).state
        == "pending"
    )
    assert (
        classify_tool_result({"status": "partial", "applied": False}).state
        == "partial"
    )


def test_non_mapping_payload_is_preserved_as_protocol_failure() -> None:
    payload = ["raw", "result"]

    outcome = classify_tool_result(payload)

    assert outcome.state == "failure"
    assert outcome.error_code == "E_TOOL_PROTOCOL"
    assert outcome.raw_payload is payload


def test_failed_job_gets_job_specific_code() -> None:
    outcome = classify_tool_result({"status": "failed", "job_id": "job_1"})

    assert outcome.error_code == "E_JOB_FAILED"


def test_typed_exception_preserves_structured_payload() -> None:
    exc = ToolError(
        "renderer unavailable",
        code="E_UNAVAILABLE",
        recovery=RECOVERY_SWITCH_TOOL,
        valid_options=["software_renderer"],
        hint="Switch renderer.",
    )

    outcome = classify_tool_exception(exc)

    assert outcome.state == "failure"
    assert outcome.error_code == "E_UNAVAILABLE"
    assert outcome.recovery == RECOVERY_SWITCH_TOOL
    assert outcome.raw_payload == {
        "error": "renderer unavailable",
        "error_code": "E_UNAVAILABLE",
        "recovery": RECOVERY_SWITCH_TOOL,
        "valid_options": ["software_renderer"],
        "hint": "Switch renderer.",
    }


def test_generic_exception_becomes_uncaught_failure() -> None:
    outcome = classify_tool_exception(RuntimeError("boom"))

    assert outcome.state == "failure"
    assert outcome.error_code == "E_UNCAUGHT"
    assert outcome.recovery is None
    assert outcome.raw_payload == {
        "error": "RuntimeError: boom",
        "error_code": "E_UNCAUGHT",
    }


def test_state_vocabulary_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="invalid tool outcome state"):
        ToolOutcome(state="unknown", raw_payload={})  # type: ignore[arg-type]
