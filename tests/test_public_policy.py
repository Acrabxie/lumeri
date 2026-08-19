"""The published policy page must keep matching the code it describes.

Two obligations from Waffo's AIGC requirements are easy to satisfy once and then
quietly break: naming the models actually in use, and listing the prohibited
categories individually. Both are statements about code that lives elsewhere, so
both need a test that reds when the code moves and the page does not — otherwise
the page degrades into a false statement, which is worse than having none.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gemia.model_strength import MEDIA_MODEL_PRIORITY
from gemia.moderation.policy import Category

REPO = Path(__file__).resolve().parents[1]
POLICY_PAGE = REPO / "static" / "v3" / "policy.html"
MODELS_JSON = REPO / "static" / "v3" / "models.json"


@pytest.fixture(scope="module")
def page_text() -> str:
    return POLICY_PAGE.read_text(encoding="utf-8")


def test_model_disclosure_is_not_stale():
    """models.json must match what the exporter would write today.

    Run ``scripts/export_model_disclosure.py`` after changing
    ``MEDIA_MODEL_PRIORITY``. Mirrors how ``test_v3_contract`` guards the
    contract copies.
    """
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    from export_model_disclosure import build  # noqa: PLC0415

    committed = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    assert committed == build(), (
        "static/v3/models.json is stale — run scripts/export_model_disclosure.py. "
        "A disclosure that names models the platform no longer uses is a false "
        "statement to the payment processor, not merely an outdated page."
    )


def test_every_generation_model_is_disclosed():
    """No model can be reachable at runtime without appearing on the page."""
    committed = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    disclosed = {m for surface in committed["surfaces"] for m in surface["models"]}
    for slot in ("image", "video", "audio"):
        for backend_models in MEDIA_MODEL_PRIORITY.get(slot, {}).values():
            for model in backend_models:
                bare = model.split("/", 1)[1] if "/" in model else model
                assert bare in disclosed, f"{model} is callable but not disclosed"


def test_disclosure_names_are_specific(page_text: str):
    """Vague wording is explicitly rejected by the AIGC requirements."""
    committed = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    disclosed = [m for surface in committed["surfaces"] for m in surface["models"]]
    assert disclosed, "no models disclosed at all"
    for name in disclosed:
        # "AI technology" or "30+ models" is called out as unacceptable; a real
        # identifier carries a version.
        assert any(ch.isdigit() for ch in name), f"{name!r} is not a specific model name"


def test_all_six_prohibited_categories_are_listed_individually(page_text: str):
    """The page must name each category, not fold them into a catch-all."""
    headings = {
        Category.SEXUAL: "Sexual content",
        Category.VIOLENCE: "Graphic violence",
        Category.HATE: "Hate speech",
        Category.MINORS: "Child-unsafe content",
        Category.DEEPFAKE: "Deepfakes and impersonation",
        Category.INTELLECTUAL_PROPERTY: "Copyright and trademark infringement",
    }
    # Every enum member must have a heading here: adding a category to the policy
    # without adding it to the page should fail rather than pass silently.
    assert set(headings) == set(Category), "policy.Category changed — update the page"
    for heading in headings.values():
        assert heading in page_text, f"policy page is missing the {heading!r} section"


def test_page_states_enforcement_and_reporting(page_text: str):
    """Enforcement actions and a reporting channel are both required."""
    assert "abuse@lumeri.io" in page_text, "no reporting address published"
    for phrase in ("suspension", "termination"):
        assert phrase in page_text.lower(), f"no stated enforcement action: {phrase}"


def test_page_describes_both_screening_stages(page_text: str):
    """The page claims what the code actually does — both halves of it."""
    assert "Before generation" in page_text
    assert "After generation, before display" in page_text
    # The claim that swapping models cannot bypass screening is load-bearing for
    # the BYOK carve-out further down the page.
    assert "Switching models does not bypass it" in page_text
