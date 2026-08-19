"""Platform-side content moderation for Lumeri.

The public 《可接受使用与 AI 内容规则》 lists six prohibited categories. This
package is the enforcement side of that promise: every hosted generation call
screens its prompt here before it reaches a model provider, and every refusal
is recorded.

Typical use at a call site::

    from gemia.moderation import guard_prompt

    guard_prompt(prompt, surface="image.generate")   # raises on refusal

``guard_prompt`` is the one function call sites should need. It screens,
records the refusal, and raises :class:`gemia.errors.ContentPolicyError`.
"""
from __future__ import annotations

from gemia.errors import ContentPolicyError
from gemia.moderation.checker import Verdict, check_prompt
from gemia.moderation.backends import resolve_backend
from gemia.moderation.ledger import (
    record_output_rejection,
    record_rejection,
    record_unscreened,
)
from gemia.moderation.output import (
    OutputModerationUnavailable,
    OutputVerdict,
    check_output,
    platform_funded,
    screening_required,
)
from gemia.moderation.policy import Category, Signal
from gemia.moderation.surfaces import SCREENED_TOOL_ARGS

__all__ = [
    "SCREENED_TOOL_ARGS",
    "Category",
    "ContentPolicyError",
    "Signal",
    "Verdict",
    "OutputVerdict",
    "check_output",
    "check_prompt",
    "guard_prompt",
    "guard_tool_args",
    "guard_tool_output",
    "record_rejection",
]


def guard_tool_args(tool_name: str, args: dict) -> None:
    """Screen a tool call's generative arguments before it executes.

    Applied centrally at dispatch so a tool cannot escape screening by being
    called from a different code path — the dispatcher is reached from the agent
    loop, from subtasks, and from the session manager.

    Args:
        tool_name: The verb being dispatched, e.g. ``"generate_video"``.
        args: The parsed tool arguments.

    Raises:
        ContentPolicyError: If any screened argument is refused.
    """
    for key in SCREENED_TOOL_ARGS.get(tool_name, ()):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            guard_prompt(value, surface=f"tool.{tool_name}")


def guard_prompt(text: str, *, surface: str) -> Verdict:
    """Screen ``text`` and raise if the policy refuses it.

    Args:
        text: The prompt about to be sent to a model provider.
        surface: Call-site identifier recorded in the ledger, e.g.
            ``"video.generate"`` or ``"agent.turn"``.

    Returns:
        The allowing :class:`Verdict`, so a caller can inspect non-blocking
        signals if it wants to.

    Raises:
        ContentPolicyError: If the request must be refused. The error carries
            ``recovery="none"`` so the agent loop treats it as final rather than
            re-attempting with a reworded prompt.
    """
    verdict = check_prompt(text)
    if verdict.allowed:
        return verdict
    record_rejection(verdict, surface=surface, text=text)
    raise ContentPolicyError(
        verdict.reason,
        category=verdict.category.value if verdict.category else "",
        blocking_id=verdict.blocking_id,
        detail=f"content policy refusal at {surface}: {verdict.blocking_id}",
    )


async def guard_tool_output(tool_name: str, result: object, ctx: object) -> None:
    """Screen a generation tool's product before the caller can show it.

    The output-side twin of :func:`guard_tool_args`, applied at the same choke
    point so a tool cannot escape it by being reached from the agent loop, a
    subtask, or the session manager.

    A refusal removes the generated file. The alternative — leaving it on disk
    behind a withheld id — means the platform is storing exactly the material it
    just refused to show, which is the same reasoning that keeps refused prompts
    out of the ledger.

    Args:
        tool_name: The verb that produced the asset, e.g. ``"generate_image"``.
        result: The tool's return value. Anything without an ``asset_id`` is
            left alone; screening is not a schema check.
        ctx: The :class:`~gemia.tools._context.ToolContext`, used to resolve the
            asset and to decide whether this generation was platform-funded.

    Raises:
        ContentPolicyError: If the generated media is refused, or if screening
            is required and no detector could answer.
    """
    if tool_name not in SCREENED_TOOL_ARGS:
        return
    if not isinstance(result, dict):
        return
    asset_id = result.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id:
        return
    if not platform_funded(ctx):
        # User-funded generation: the money never passed through the merchant of
        # record, so the platform is not the seller of this result. The
        # prompt-side guard still ran, and it runs whatever model was swapped in.
        return

    registry = getattr(ctx, "registry", None)
    if registry is None or not registry.contains(asset_id):
        return
    record = registry.get(asset_id)
    surface = f"tool.{tool_name}"

    try:
        verdict = await check_output(record.kind, record.path, backend=resolve_backend())
    except OutputModerationUnavailable as exc:
        if screening_required():
            raise ContentPolicyError(
                "This generation could not be checked against the content "
                "policy, so it was not shown. Please try again shortly.",
                detail=f"output moderation unavailable: {exc}",
            ) from exc
        record_unscreened(
            surface=surface, asset_id=asset_id, kind=record.kind, reason=str(exc)
        )
        return

    if verdict.allowed:
        return

    record_output_rejection(
        verdict,
        surface=surface,
        asset_id=asset_id,
        kind=record.kind,
        path=record.path,
    )
    try:
        record.path.unlink()
    except OSError:
        # Enforcement is the withheld result, not the file removal; a locked
        # file must not turn a refusal into a crash that shows the asset anyway.
        pass
    raise ContentPolicyError(
        verdict.reason,
        category=verdict.category.value if verdict.category else "",
        detail=f"generated {record.kind} refused by {verdict.detector}",
    )
