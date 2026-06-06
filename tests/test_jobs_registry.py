"""Tests for JobRegistry and JobRecord."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from gemia.tools._jobs import JobRecord, JobRegistry


def test_job_record_submit_and_get(tmp_path: Path) -> None:
    """Test basic submit → get roundtrip."""
    registry = JobRegistry()

    # Submit a job
    record = registry.submit(
        kind="video",
        provider="ai_studio:veo-3.1-fast",
        operation_name="operations/abc123",
        pending_asset_id="v_007",
        estimated_eta_sec=120.0,
        summary="Test Veo generation",
    )

    # Verify initial state
    assert record.job_id.startswith("video_")
    assert record.kind == "video"
    assert record.provider == "ai_studio:veo-3.1-fast"
    assert record.pending_asset_id == "v_007"
    assert record.last_polled_status == "submitted"
    assert record.last_polled_at is None
    assert record.final_path is None
    assert record.final_error is None

    # Retrieve via get
    retrieved = registry.get(record.job_id)
    assert retrieved.job_id == record.job_id
    assert retrieved.last_polled_status == "submitted"


def test_job_record_explicit_job_id(tmp_path: Path) -> None:
    """Test submit with explicit job_id."""
    registry = JobRegistry()

    record = registry.submit(
        kind="build",
        provider="sandbox",
        operation_name="pid_12345",
        pending_asset_id="v_001",
        estimated_eta_sec=30.0,
        summary="Build job",
        job_id="build_custom123",
    )

    assert record.job_id == "build_custom123"
    assert registry.get("build_custom123") == record


def test_job_record_get_unknown_id(tmp_path: Path) -> None:
    """Test get with unknown job_id raises KeyError with known ids list."""
    registry = JobRegistry()

    registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_001",
        estimated_eta_sec=100.0,
        summary="Job 1",
        job_id="veo_job1",
    )

    with pytest.raises(KeyError) as exc_info:
        registry.get("veo_unknown")

    error_msg = str(exc_info.value)
    assert "veo_unknown" in error_msg
    assert "veo_job1" in error_msg  # Known ids should be listed


def test_list_pending_filters_done_and_failed() -> None:
    """Test list_pending excludes done and failed jobs."""
    registry = JobRegistry()

    # Create jobs in different states
    submitted = registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_001",
        estimated_eta_sec=100.0,
        summary="Submitted",
        job_id="job_submitted",
    )

    running = registry.submit(
        kind="audio",
        provider="ai_studio",
        operation_name="op2",
        pending_asset_id="aud_001",
        estimated_eta_sec=50.0,
        summary="Running",
        job_id="job_running",
    )

    done = registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op3",
        pending_asset_id="v_002",
        estimated_eta_sec=100.0,
        summary="Done",
        job_id="job_done",
    )

    failed = registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op4",
        pending_asset_id="v_003",
        estimated_eta_sec=100.0,
        summary="Failed",
        job_id="job_failed",
    )

    # Update states
    registry.update_from_poll("job_running", "running")
    registry.update_from_poll("job_done", "done", final_path=Path("/tmp/output.mp4"))
    registry.update_from_poll("job_failed", "failed", error="Provider error")

    # Check pending
    pending = registry.list_pending()
    pending_ids = {r.job_id for r in pending}

    assert "job_submitted" in pending_ids
    assert "job_running" in pending_ids
    assert "job_done" not in pending_ids
    assert "job_failed" not in pending_ids
    assert len(pending) == 2


def test_update_from_poll_running_state() -> None:
    """Test update_from_poll with running status."""
    registry = JobRegistry()

    job = registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_001",
        estimated_eta_sec=100.0,
        summary="Test",
        job_id="veo_test",
    )

    # Poll once
    time.sleep(0.01)  # Ensure monotonic clock advances
    updated = registry.update_from_poll("veo_test", "running")

    assert updated.last_polled_status == "running"
    assert updated.last_polled_at is not None
    assert updated.final_path is None
    assert updated.final_error is None


def test_update_from_poll_done_with_final_path() -> None:
    """Test update_from_poll with done status and final_path."""
    registry = JobRegistry()

    registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_001",
        estimated_eta_sec=100.0,
        summary="Test",
        job_id="veo_done",
    )

    final_path = Path("/tmp/video_output.mp4")
    updated = registry.update_from_poll(
        "veo_done",
        "done",
        final_path=final_path,
    )

    assert updated.last_polled_status == "done"
    assert updated.final_path == final_path.resolve()
    assert updated.final_error is None


def test_update_from_poll_failed_with_error() -> None:
    """Test update_from_poll with failed status and error."""
    registry = JobRegistry()

    registry.submit(
        kind="audio",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="aud_001",
        estimated_eta_sec=50.0,
        summary="Test",
        job_id="lyria_fail",
    )

    error_msg = "Provider returned quota exceeded"
    updated = registry.update_from_poll(
        "lyria_fail",
        "failed",
        error=error_msg,
    )

    assert updated.last_polled_status == "failed"
    assert updated.final_error == error_msg
    assert updated.final_path is None


def test_compact_text_for_prompt_with_pending() -> None:
    """Test compact_text_for_prompt renders pending jobs."""
    registry = JobRegistry()

    registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_007",
        estimated_eta_sec=120.0,
        summary="Veo generation",
        job_id="veo_a1b2c3",
    )

    registry.submit(
        kind="audio",
        provider="ai_studio",
        operation_name="op2",
        pending_asset_id="aud_004",
        estimated_eta_sec=60.0,
        summary="Audio generation",
        job_id="lyria_d4e5f6",
    )

    # Update first to "running"
    registry.update_from_poll("veo_a1b2c3", "running")

    text = registry.compact_text_for_prompt()

    # Should show pending jobs with status
    assert "veo_a1b2c3" in text
    assert "lyria_d4e5f6" in text
    assert "running" in text
    assert "submitted" in text
    assert "v_007" in text
    assert "aud_004" in text
    assert "ETA was 120s" in text
    assert "ETA was 60s" in text


def test_compact_text_for_prompt_no_pending() -> None:
    """Test compact_text_for_prompt with no pending jobs."""
    registry = JobRegistry()

    text = registry.compact_text_for_prompt()
    assert text == "(no pending jobs)"

    # Add and mark as done
    registry.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_001",
        estimated_eta_sec=100.0,
        summary="Test",
        job_id="veo_test",
    )
    registry.update_from_poll("veo_test", "done", final_path=Path("/tmp/out.mp4"))

    text = registry.compact_text_for_prompt()
    assert text == "(no pending jobs)"


def test_save_load_roundtrip(tmp_path: Path) -> None:
    """Test save and load with atomic write."""
    jobs_file = tmp_path / "jobs.json"

    # Create and populate registry
    registry1 = JobRegistry()
    registry1.submit(
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_001",
        estimated_eta_sec=120.0,
        summary="Veo job",
        job_id="veo_job1",
    )

    registry1.submit(
        kind="audio",
        provider="ai_studio",
        operation_name="op2",
        pending_asset_id="aud_001",
        estimated_eta_sec=60.0,
        summary="Audio job",
        job_id="lyria_job1",
    )

    # Update one job
    registry1.update_from_poll("veo_job1", "running")
    registry1.update_from_poll(
        "lyria_job1",
        "done",
        final_path=tmp_path / "audio_output.mp3",
    )

    # Save
    registry1.save(jobs_file)
    assert jobs_file.exists()

    # Load into new registry
    registry2 = JobRegistry.load(jobs_file)

    # Verify records
    assert len(registry2._records) == 2

    veo_job = registry2.get("veo_job1")
    assert veo_job.kind == "video"
    assert veo_job.last_polled_status == "running"
    assert veo_job.provider == "ai_studio"

    lyria_job = registry2.get("lyria_job1")
    assert lyria_job.kind == "audio"
    assert lyria_job.last_polled_status == "done"
    assert lyria_job.final_path == (tmp_path / "audio_output.mp3").resolve()


def test_load_nonexistent_file() -> None:
    """Test load with nonexistent file returns empty registry."""
    registry = JobRegistry.load(Path("/tmp/nonexistent_jobs_12345.json"))
    assert len(registry._records) == 0


def test_kind_build_accepted() -> None:
    """Test kind='build' is accepted in submit."""
    registry = JobRegistry()

    record = registry.submit(
        kind="build",
        provider="sandbox",
        operation_name="pid_99999",
        pending_asset_id="v_100",
        estimated_eta_sec=45.0,
        summary="Build output",
        job_id="build_python_01",
    )

    assert record.kind == "build"
    assert registry.get("build_python_01").kind == "build"


def test_to_dict_skips_submitted_mono() -> None:
    """Test to_dict excludes submitted_mono field."""
    record = JobRecord(
        job_id="test_job",
        kind="video",
        provider="ai_studio",
        operation_name="op1",
        pending_asset_id="v_001",
        submitted_at="2026-06-07T00:00:00+00:00",
        estimated_eta_sec=100.0,
        last_polled_at=None,
        last_polled_status="submitted",
        final_path=None,
        final_error=None,
        summary="Test",
    )

    d = record.to_dict()
    assert "submitted_mono" not in d
    assert d["job_id"] == "test_job"
    assert d["kind"] == "video"


def test_from_dict_resets_submitted_mono() -> None:
    """Test from_dict resets submitted_mono to current time."""
    d = {
        "job_id": "veo_test",
        "kind": "video",
        "provider": "ai_studio",
        "operation_name": "op1",
        "pending_asset_id": "v_001",
        "submitted_at": "2026-06-07T00:00:00+00:00",
        "estimated_eta_sec": 100.0,
        "last_polled_at": None,
        "last_polled_status": "submitted",
        "final_path": None,
        "final_error": None,
        "summary": "Test",
    }

    before = time.monotonic()
    record = JobRecord.from_dict(d)
    after = time.monotonic()

    assert before <= record.submitted_mono <= after
