"""The old engine and script names still work where saved data or settings use them."""
from __future__ import annotations

import pytest

from lumeri import legacy_names
from lumeri.asset_identity import build_entity_reference, parse_entity_reference
from lumeri.legacy_names import (
    apply_legacy_env,
    current_import_name,
    current_registry_id,
    current_uri,
)

OLD_ENGINE = "gem" + "ia"
OLD_SCRIPT = "lumer" + "ai"


def test_old_env_names_fill_current_names():
    env = {
        f"{OLD_ENGINE.upper()}_HOST": "0.0.0.0",
        f"{OLD_SCRIPT.upper()}_PROJECT_ROOT": "/tmp/p",
        "LUMERI_PORT": "9000",
        f"{OLD_ENGINE.upper()}_PORT": "7788",
    }
    filled = apply_legacy_env(env)
    assert env["LUMERI_HOST"] == "0.0.0.0"
    assert env["LUMERI_PROJECT_ROOT"] == "/tmp/p"
    assert env["LUMERI_PORT"] == "9000"  # a current value always wins
    assert sorted(filled) == ["LUMERI_HOST", "LUMERI_PROJECT_ROOT"]


def test_bare_prefix_is_ignored():
    env = {f"{OLD_ENGINE.upper()}_": "x"}
    assert apply_legacy_env(env) == []
    assert "LUMERI_" not in env


def test_old_import_root_maps_to_script_runtime():
    assert current_import_name(OLD_SCRIPT) == "lumeri_script"
    assert current_import_name(f"{OLD_SCRIPT}.patches") == "lumeri_script.patches"
    assert current_import_name("json") == "json"


def test_sandbox_accepts_scripts_that_use_the_old_import():
    pytest.importorskip("cv2")
    from lumeri_script import sandbox

    sandbox.validate_script(f"import {OLD_SCRIPT} as lm\n")
    sandbox.validate_script("import lumeri_script as lm\n")
    with pytest.raises(sandbox.SandboxViolation):
        sandbox.validate_script("import os\n")


def test_old_uri_scheme_maps_to_current():
    assert current_uri(f"{OLD_ENGINE}://session/s1/timeline") == "lumeri://session/s1/timeline"
    assert current_uri(f"{OLD_ENGINE.upper()}://media/a") == "lumeri://media/a"
    assert current_uri("https://example.com") == "https://example.com"
    assert current_uri("no-scheme") == "no-scheme"


def test_saved_entity_reference_with_old_scheme_still_parses():
    current = build_entity_reference(asset_id="a1", account_id="local", fingerprint="f" * 16)
    old = build_entity_reference(asset_id="a1", account_id="local", fingerprint="f" * 16, namespace=OLD_ENGINE)
    assert current.startswith("lumeri://media/")
    old_ref, current_ref = parse_entity_reference(old), parse_entity_reference(current)
    assert (old_ref.account_id, old_ref.asset_id, old_ref.fingerprint, old_ref.version_id) == (
        current_ref.account_id,
        current_ref.asset_id,
        current_ref.fingerprint,
        current_ref.version_id,
    )


def test_old_registry_ids_resolve():
    pytest.importorskip("cv2")
    from lumeri.registry import get_info, resolve

    old = f"{OLD_ENGINE}.picture.color.color_grade"
    assert current_registry_id(old) == "lumeri.picture.color.color_grade"
    assert resolve(old) is resolve("lumeri.picture.color.color_grade")
    assert get_info(old).func is get_info("lumeri.picture.color.color_grade").func


def test_module_lists_only_old_names_it_maps():
    assert legacy_names.LEGACY_IMPORT_NAMES == {OLD_SCRIPT: "lumeri_script"}
    assert legacy_names.LEGACY_URI_SCHEMES == {OLD_ENGINE: "lumeri"}
