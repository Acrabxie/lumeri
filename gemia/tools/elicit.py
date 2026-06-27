"""Elicitation verb: agent requesting user input via ask mechanism.

The elicit verb allows the agent to ask the user a structured question
and receive a validated answer. This is the agent-facing interface to
the ask mechanism.

The verb dispatches as follows:
  1. Agent calls elicit with a question schema
  2. This verb emits an 'ask_question' SSE event to the user
  3. User submits answer via client-side elicit_response
  4. Next turn, the agent receives the answer as context

Note: This is a special verb that returns control to the user and does
NOT return a normal tool result. The loop recognizes it and emits the
ask_question event instead of appending a tool_result.
"""
from __future__ import annotations

from typing import Any
import uuid
from datetime import datetime, timezone

from gemia.tools._context import ToolContext
from gemia.tools.ask import (
    AskQuestion,
    AskAnswer,
    AskControlType,
    SelectControl,
    MultiSelectControl,
    TextControl,
    SliderControl,
    PanelControl,
    CustomPanelControl,
    validate_ask_answer,
)


def dispatch(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Emit an ask question and return metadata.

    Args:
        title: human-readable title
        description: optional longer description
        controls: dict of {control_key: control_spec}
            where control_spec is one of:
              {type: "select", options: [...], default?: ...}
              {type: "multi_select", options: [...], min?: 0, max?: ...}
              {type: "text", placeholder?: "", pattern?: ..., min_length?: 0, max_length?: ...}
              {type: "slider", min: num, max: num, step?: 1, default?: ...}
              {type: "panel", fields: {...}, description?: ""}
              {type: "custom_panel", schema: {...}, validator?: "..."}

    Returns:
        {
            "question_id": str,
            "status": "question_emitted",
            "message": "Waiting for user response"
        }

    This verb is special: the loop will emit an 'ask_question' SSE event
    to the user instead of a normal tool_result.
    """
    title = args.get("title", "Question")
    description = args.get("description", "")
    controls_spec = args.get("controls", {})

    if not controls_spec:
        return {
            "error": "no controls specified",
            "error_code": "E_ELICIT_NO_CONTROLS",
        }

    # Build controls from spec
    try:
        controls = _build_controls(controls_spec)
    except Exception as e:
        return {
            "error": f"invalid control specification: {e}",
            "error_code": "E_ELICIT_INVALID_SPEC",
        }

    # Create question
    question_id = f"ask_{uuid.uuid4().hex[:12]}"
    question = AskQuestion(
        question_id=question_id,
        title=title,
        description=description,
        controls=controls,
        metadata={
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "session_id": ctx.session_id,
        },
    )

    # Store in session for later reference (optional, for tracking)
    # In a real implementation, this might be persisted in the session state
    ctx.session_state["_pending_questions"] = ctx.session_state.get("_pending_questions", {})
    ctx.session_state["_pending_questions"][question_id] = question.to_dict()

    # Return metadata; the loop will handle emitting the ask_question event
    return {
        "question_id": question_id,
        "status": "question_emitted",
        "message": "Waiting for user response",
        "question": question.to_dict(),  # Include full question for the loop/SSE
    }


def _build_controls(spec: dict[str, Any]) -> dict[str, Any]:
    """Parse control specifications into control objects."""
    controls = {}

    for key, ctrl_spec in spec.items():
        ctrl_type = ctrl_spec.get("type")

        if ctrl_type == AskControlType.SELECT:
            controls[key] = SelectControl(
                options=ctrl_spec.get("options", []),
                default=ctrl_spec.get("default"),
            )

        elif ctrl_type == AskControlType.MULTI_SELECT:
            controls[key] = MultiSelectControl(
                options=ctrl_spec.get("options", []),
                min=ctrl_spec.get("min", 0),
                max=ctrl_spec.get("max"),
            )

        elif ctrl_type == AskControlType.TEXT:
            controls[key] = TextControl(
                placeholder=ctrl_spec.get("placeholder", ""),
                multiline=ctrl_spec.get("multiline", False),
                pattern=ctrl_spec.get("pattern"),
                min_length=ctrl_spec.get("min_length", 0),
                max_length=ctrl_spec.get("max_length"),
            )

        elif ctrl_type == AskControlType.SLIDER:
            controls[key] = SliderControl(
                min=ctrl_spec.get("min", 0),
                max=ctrl_spec.get("max", 100),
                step=ctrl_spec.get("step", 1),
                default=ctrl_spec.get("default"),
            )

        elif ctrl_type == AskControlType.PANEL:
            panel_fields = {}
            for fkey, fspec in ctrl_spec.get("fields", {}).items():
                ftype = fspec.get("type")
                if ftype == AskControlType.SELECT:
                    panel_fields[fkey] = SelectControl(
                        options=fspec.get("options", []),
                        default=fspec.get("default"),
                    )
                elif ftype == AskControlType.MULTI_SELECT:
                    panel_fields[fkey] = MultiSelectControl(
                        options=fspec.get("options", []),
                        min=fspec.get("min", 0),
                        max=fspec.get("max"),
                    )
                elif ftype == AskControlType.TEXT:
                    panel_fields[fkey] = TextControl(
                        placeholder=fspec.get("placeholder", ""),
                        multiline=fspec.get("multiline", False),
                        pattern=fspec.get("pattern"),
                        min_length=fspec.get("min_length", 0),
                        max_length=fspec.get("max_length"),
                    )
                elif ftype == AskControlType.SLIDER:
                    panel_fields[fkey] = SliderControl(
                        min=fspec.get("min", 0),
                        max=fspec.get("max", 100),
                        step=fspec.get("step", 1),
                        default=fspec.get("default"),
                    )
                else:
                    raise ValueError(f"unsupported panel field type: {ftype}")

            controls[key] = PanelControl(
                fields=panel_fields,
                description=ctrl_spec.get("description", ""),
            )

        elif ctrl_type == AskControlType.CUSTOM_PANEL:
            controls[key] = CustomPanelControl(
                schema=ctrl_spec.get("schema", {}),
            )

        else:
            raise ValueError(f"unsupported control type: {ctrl_type}")

    return controls


def dispatch_respond(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Process user response to a previous ask question.

    This is called internally when the user submits an answer.

    Args:
        question_id: the question_id from the ask_question event
        answers: {control_key: value} dict

    Returns:
        {
            "question_id": str,
            "status": "answer_received",
            "validated": {control_key: validated_value}
        }
    """
    question_id = args.get("question_id")
    answers_spec = args.get("answers", {})

    if not question_id:
        return {
            "error": "question_id required",
            "error_code": "E_ELICIT_NO_QUESTION_ID",
        }

    # Retrieve the question from session state
    pending = ctx.session_state.get("_pending_questions", {})
    if question_id not in pending:
        return {
            "error": f"question {question_id} not found or already answered",
            "error_code": "E_ELICIT_UNKNOWN_QUESTION",
        }

    question_dict = pending[question_id]
    question = AskQuestion.from_dict(question_dict)
    answer = AskAnswer(
        question_id=question_id,
        answers=answers_spec,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Validate
    validated, error = validate_ask_answer(question, answer)
    if error:
        return {
            "error": f"validation failed: {error}",
            "error_code": "E_ELICIT_INVALID_ANSWER",
        }

    # Mark as answered
    del pending[question_id]

    return {
        "question_id": question_id,
        "status": "answer_received",
        "validated": validated,
    }
