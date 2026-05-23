from __future__ import annotations

from gemia.project_model import (
    IMAGE_DURATION,
    PROJECT_SCHEMA,
    clip_count,
    legacy_project_state_from_project,
    normalize_project,
)


def test_normalize_legacy_project_state_builds_schema_v1() -> None:
    state = {
        "clips": [
            {
                "id": "clip_video",
                "trackId": "V1",
                "mediaKind": "video",
                "mimeType": "video/mp4",
                "name": "a.mp4",
                "serverPath": "/tmp/a.mp4",
                "duration": 5,
                "inPoint": 1,
                "outPoint": 4,
                "keep": True,
                "effects": {"speed": 1, "rotation": 0},
            },
            {
                "id": "clip_audio",
                "trackId": "A1",
                "mediaKind": "audio",
                "mimeType": "audio/mpeg",
                "name": "music.mp3",
                "serverPath": "/tmp/music.mp3",
                "duration": 10,
                "inPoint": 0,
                "outPoint": 10,
                "keep": False,
            },
        ],
        "selectedClipId": "clip_audio",
        "playhead": 2.5,
        "zoom": 1.7,
        "snapEnabled": False,
    }

    project = normalize_project(project_state=state, account_id="acct_1")

    assert project["schema"] == PROJECT_SCHEMA
    assert project["version"] == 1
    assert project["account_id"] == "acct_1"
    assert project["title"] == "a.mp4"
    assert len(project["assets"]) == 2
    assert clip_count(project) == 2
    assert project["timeline"]["clips"][0]["duration"] == 3
    assert project["timeline"]["clips"][1]["start"] == 3
    assert project["timeline"]["clips"][1]["enabled"] is False
    assert project["ui_state"]["selected_clip_id"] == "clip_audio"
    assert project["ui_state"]["snap_enabled"] is False


def test_image_clip_defaults_to_three_seconds() -> None:
    project = normalize_project(
        project_state={
            "clips": [
                {
                    "id": "still",
                    "assetId": "asset_library_still",
                    "mediaKind": "image",
                    "name": "still.png",
                    "serverPath": "/tmp/still.png",
                    "duration": 0,
                    "inPoint": 0,
                    "outPoint": 0,
                }
            ]
        }
    )

    clip = project["timeline"]["clips"][0]
    asset = project["assets"][0]

    assert asset["media_kind"] == "image"
    assert asset["asset_id"] == "asset_library_still"
    assert clip["asset_id"] == "asset_library_still"
    assert asset["duration"] == IMAGE_DURATION
    assert clip["duration"] == IMAGE_DURATION
    assert clip["source_out"] == IMAGE_DURATION
    assert project["timeline"]["duration"] == IMAGE_DURATION


def test_untrimmed_video_uses_real_duration_over_stale_out_point() -> None:
    project = normalize_project(
        project_state={
            "clips": [
                {
                    "id": "real_video",
                    "mediaKind": "video",
                    "name": "real.mp4",
                    "serverPath": "/tmp/real.mp4",
                    "duration": 12.4,
                    "inPoint": 0,
                    "outPoint": 8,
                    "trimmed": False,
                }
            ]
        }
    )

    clip = project["timeline"]["clips"][0]

    assert clip["duration"] == 12.4
    assert clip["source_in"] == 0
    assert clip["source_out"] == 12.4
    assert project["timeline"]["duration"] == 12.4


def test_trimmed_video_keeps_user_trim_bounds() -> None:
    project = normalize_project(
        project_state={
            "clips": [
                {
                    "id": "trimmed_video",
                    "mediaKind": "video",
                    "name": "trimmed.mp4",
                    "serverPath": "/tmp/trimmed.mp4",
                    "duration": 12.4,
                    "inPoint": 2,
                    "outPoint": 5,
                    "trimmed": True,
                }
            ]
        }
    )

    clip = project["timeline"]["clips"][0]

    assert clip["duration"] == 3
    assert clip["source_in"] == 2
    assert clip["source_out"] == 5
    assert project["timeline"]["duration"] == 3


def test_canonical_project_round_trips_to_legacy_state() -> None:
    project = normalize_project(
        project_state={
            "clips": [
                {
                    "id": "c1",
                    "mediaKind": "image",
                    "name": "still.jpg",
                    "serverPath": "/tmp/still.jpg",
                    "previewSrc": "/file/inputs/still.jpg",
                }
            ],
            "selectedClipId": "c1",
        }
    )

    normalized_again = normalize_project(project)
    legacy = legacy_project_state_from_project(normalized_again)

    assert normalized_again["schema"] == PROJECT_SCHEMA
    assert legacy["clips"][0]["mediaKind"] == "image"
    assert legacy["clips"][0]["duration"] == IMAGE_DURATION
    assert legacy["clips"][0]["outPoint"] == IMAGE_DURATION
    assert legacy["selectedClipId"] == "c1"
