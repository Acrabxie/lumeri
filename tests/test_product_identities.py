from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_product_identities.py"
SPEC = importlib.util.spec_from_file_location("check_product_identities", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity)


def test_repository_identity_contract_is_valid() -> None:
    contract = identity.load_contract(ROOT / "config" / "product-identities.json")

    assert contract["products"]["video"]["apple_bundle_id"] == "com.acrab.lumeri.video"
    assert contract["products"]["quanta"]["apple_bundle_id"] == "com.acrab.lumeri.quanta"
    assert contract["products"]["video"]["android_application_id"] == "com.acrab.lumeri.video"


def test_apple_checker_accepts_product_and_test_bundle_ids(tmp_path: Path) -> None:
    contract = identity.load_contract(ROOT / "config" / "product-identities.json")
    project = tmp_path / "project.pbxproj"
    project.write_text(
        "\n".join(
            (
                "PRODUCT_BUNDLE_IDENTIFIER = com.acrab.lumeri.video;",
                "PRODUCT_BUNDLE_IDENTIFIER = com.acrab.lumeri.video.tests;",
            )
        ),
        encoding="utf-8",
    )

    assert identity.check_apple_project(contract, "video", project) == []


def test_android_checker_rejects_old_application_id(tmp_path: Path) -> None:
    contract = identity.load_contract(ROOT / "config" / "product-identities.json")
    gradle_file = tmp_path / "build.gradle.kts"
    gradle_file.write_text('applicationId = "com.xiehaibo.lumeri"\n', encoding="utf-8")

    errors = identity.check_android_gradle(contract, "video", gradle_file)

    assert errors
    assert "com.acrab.lumeri.video" in errors[0]


def test_contract_rejects_noncanonical_product_identity(tmp_path: Path) -> None:
    contract_file = tmp_path / "product-identities.json"
    contract_file.write_text(
        """{
          "schema": "lumeri.product-identities.v1",
          "bundle_id_prefix": "com.acrab.lumeri",
          "products": {
            "quanta": {
              "model": "quanta",
              "apple_bundle_id": "com.acrab.Lumeri-Quanta",
              "android_application_id": "com.acrab.lumeri.quanta"
            }
          }
        }""",
        encoding="utf-8",
    )

    with pytest.raises(identity.ContractError):
        identity.load_contract(contract_file)
