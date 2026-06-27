"""Tests for the elicit tool (agent-facing ask interface)."""
import pytest
from unittest.mock import Mock, MagicMock
from gemia.tools.elicit import dispatch, dispatch_respond, _build_controls
from gemia.tools.ask import AskControlType


@pytest.fixture
def mock_ctx():
    """Create a mock ToolContext."""
    ctx = Mock()
    ctx.session_id = "test_session_123"
    ctx.session_state = {}
    return ctx


def test_elicit_dispatch_basic(mock_ctx):
    """elicit dispatches with simple select control."""
    args = {
        "title": "Choose an option",
        "description": "Pick one",
        "controls": {
            "choice": {
                "type": "select",
                "options": [
                    {"label": "Option A", "value": "a"},
                    {"label": "Option B", "value": "b"},
                ],
            }
        },
    }

    result = dispatch(args, mock_ctx)

    assert result["status"] == "question_emitted"
    assert "question_id" in result
    assert result["question"]["title"] == "Choose an option"
    assert "choice" in result["question"]["controls"]


def test_elicit_dispatch_no_controls(mock_ctx):
    """elicit rejects missing controls."""
    args = {
        "title": "Test",
        "controls": {},
    }

    result = dispatch(args, mock_ctx)

    assert "error" in result
    assert result["error_code"] == "E_ELICIT_NO_CONTROLS"


def test_elicit_dispatch_panel_form(mock_ctx):
    """elicit dispatches with panel (grouped form)."""
    args = {
        "title": "User Information",
        "controls": {
            "form": {
                "type": "panel",
                "description": "Fill in your info",
                "fields": {
                    "email": {
                        "type": "text",
                        "pattern": r"^[^\s@]+@[^\s@]+$",
                        "min_length": 5,
                    },
                    "age": {
                        "type": "slider",
                        "min": 18,
                        "max": 120,
                        "step": 1,
                        "default": 25,
                    },
                },
            }
        },
    }

    result = dispatch(args, mock_ctx)

    assert result["status"] == "question_emitted"
    assert "form" in result["question"]["controls"]
    form_ctrl = result["question"]["controls"]["form"]
    assert "email" in form_ctrl["fields"]
    assert "age" in form_ctrl["fields"]


def test_elicit_dispatch_stores_question(mock_ctx):
    """elicit stores the question in session state for later reference."""
    args = {
        "title": "Test",
        "controls": {
            "choice": {
                "type": "select",
                "options": [{"label": "A", "value": "a"}],
            }
        },
    }

    result = dispatch(args, mock_ctx)
    question_id = result["question_id"]

    assert "_pending_questions" in mock_ctx.session_state
    assert question_id in mock_ctx.session_state["_pending_questions"]


def test_elicit_respond_success(mock_ctx):
    """elicit_respond validates and returns the answer."""
    # First, emit a question
    args = {
        "title": "Choose",
        "controls": {
            "choice": {
                "type": "select",
                "options": [
                    {"label": "A", "value": "a"},
                    {"label": "B", "value": "b"},
                ],
            }
        },
    }

    result = dispatch(args, mock_ctx)
    question_id = result["question_id"]

    # Now respond
    response_args = {
        "question_id": question_id,
        "answers": {
            "choice": "a",
        },
    }

    response = dispatch_respond(response_args, mock_ctx)

    assert response["status"] == "answer_received"
    assert response["validated"]["choice"] == "a"
    # Question should be removed from pending
    assert question_id not in mock_ctx.session_state["_pending_questions"]


def test_elicit_respond_invalid_answer(mock_ctx):
    """elicit_respond rejects invalid answers."""
    # First, emit a question
    args = {
        "title": "Choose",
        "controls": {
            "choice": {
                "type": "select",
                "options": [{"label": "A", "value": "a"}],
            }
        },
    }

    result = dispatch(args, mock_ctx)
    question_id = result["question_id"]

    # Try to respond with invalid value
    response_args = {
        "question_id": question_id,
        "answers": {
            "choice": "z",  # Invalid
        },
    }

    response = dispatch_respond(response_args, mock_ctx)

    assert "error" in response
    assert response["error_code"] == "E_ELICIT_INVALID_ANSWER"


def test_elicit_respond_missing_question(mock_ctx):
    """elicit_respond handles missing/already-answered questions."""
    response_args = {
        "question_id": "nonexistent_question",
        "answers": {},
    }

    response = dispatch_respond(response_args, mock_ctx)

    assert "error" in response
    assert response["error_code"] == "E_ELICIT_UNKNOWN_QUESTION"


def test_build_controls_select():
    """_build_controls parses select control."""
    spec = {
        "my_select": {
            "type": "select",
            "options": [
                {"label": "A", "value": "a"},
                {"label": "B", "value": "b"},
            ],
            "default": "a",
        }
    }

    controls = _build_controls(spec)

    assert "my_select" in controls
    assert controls["my_select"].default == "a"


def test_build_controls_multi_select():
    """_build_controls parses multi_select control."""
    spec = {
        "my_multi": {
            "type": "multi_select",
            "options": [
                {"label": "A", "value": "a"},
                {"label": "B", "value": "b"},
            ],
            "min": 1,
            "max": 2,
        }
    }

    controls = _build_controls(spec)

    assert "my_multi" in controls
    assert controls["my_multi"].min == 1
    assert controls["my_multi"].max == 2


def test_build_controls_text():
    """_build_controls parses text control."""
    spec = {
        "my_text": {
            "type": "text",
            "placeholder": "Enter text",
            "min_length": 1,
            "max_length": 100,
            "pattern": r"^[a-z]+$",
        }
    }

    controls = _build_controls(spec)

    assert "my_text" in controls
    assert controls["my_text"].placeholder == "Enter text"
    assert controls["my_text"].pattern == r"^[a-z]+$"


def test_build_controls_slider():
    """_build_controls parses slider control."""
    spec = {
        "my_slider": {
            "type": "slider",
            "min": 0,
            "max": 100,
            "step": 5,
            "default": 50,
        }
    }

    controls = _build_controls(spec)

    assert "my_slider" in controls
    assert controls["my_slider"].min == 0
    assert controls["my_slider"].max == 100
    assert controls["my_slider"].step == 5
    assert controls["my_slider"].default == 50


def test_build_controls_panel():
    """_build_controls parses panel control with nested fields."""
    spec = {
        "my_panel": {
            "type": "panel",
            "description": "A form",
            "fields": {
                "name": {
                    "type": "text",
                    "min_length": 1,
                },
                "age": {
                    "type": "slider",
                    "min": 0,
                    "max": 150,
                },
            },
        }
    }

    controls = _build_controls(spec)

    assert "my_panel" in controls
    panel = controls["my_panel"]
    assert "name" in panel.fields
    assert "age" in panel.fields


def test_build_controls_custom_panel():
    """_build_controls parses custom_panel control."""
    spec = {
        "my_custom": {
            "type": "custom_panel",
            "schema": {
                "type": "complex",
                "nested": {"key": "value"},
            },
        }
    }

    controls = _build_controls(spec)

    assert "my_custom" in controls
    assert controls["my_custom"].schema["type"] == "complex"


def test_build_controls_invalid_type():
    """_build_controls rejects unknown control types."""
    spec = {
        "invalid": {
            "type": "unknown_type",
        }
    }

    with pytest.raises(ValueError, match="unsupported control type"):
        _build_controls(spec)


def test_build_controls_invalid_panel_field():
    """_build_controls rejects unknown panel field types."""
    spec = {
        "panel": {
            "type": "panel",
            "fields": {
                "bad_field": {
                    "type": "unknown_type",
                }
            },
        }
    }

    with pytest.raises(ValueError, match="unsupported panel field type"):
        _build_controls(spec)


def test_elicit_dispatch_invalid_spec(mock_ctx):
    """elicit rejects invalid control specifications."""
    args = {
        "title": "Test",
        "controls": {
            "bad": {
                "type": "unknown_type",
            }
        },
    }

    result = dispatch(args, mock_ctx)

    assert "error" in result
    assert result["error_code"] == "E_ELICIT_INVALID_SPEC"


def test_elicit_all_control_types(mock_ctx):
    """elicit can emit all 6 control types in one question."""
    args = {
        "title": "Kitchen sink",
        "controls": {
            "sel": {
                "type": "select",
                "options": [{"label": "A", "value": "a"}],
            },
            "multi": {
                "type": "multi_select",
                "options": [{"label": "B", "value": "b"}],
            },
            "txt": {
                "type": "text",
                "placeholder": "text",
            },
            "sld": {
                "type": "slider",
                "min": 0,
                "max": 10,
            },
            "pnl": {
                "type": "panel",
                "fields": {
                    "field1": {
                        "type": "text",
                    },
                },
            },
            "custom": {
                "type": "custom_panel",
                "schema": {},
            },
        },
    }

    result = dispatch(args, mock_ctx)

    assert result["status"] == "question_emitted"
    controls = result["question"]["controls"]
    assert set(controls.keys()) == {"sel", "multi", "txt", "sld", "pnl", "custom"}
