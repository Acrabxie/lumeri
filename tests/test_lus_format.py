"""Tests for the .lus skill file format parser/validator/serializer.

Spec: docs/lus-skill-format.md (WP1 test gate, §11). Covers:

(a) round-trip — ``serialize_lus(*parse_lus(t)) == t`` byte-exactly for BOTH
    §9 reference fixtures, plus a generated matrix (CJK, flow-style YAML
    metadata, JSON-object metadata);
(b) one red case per typed error code (all 15 ``E_LUS_*``), with ``field``
    assertions for representative ``E_LUS_META_FIELD`` shapes, plus
    check-order tests;
(c) all five ``W_LUS_*`` warning codes;
(d) checksum: both fixtures verify; a mutated body is ``W_LUS_CHECKSUM_STALE``
    non-strict and ``E_LUS_CHECKSUM`` strict;
(e) both fixtures parse under the ``gemia.ai.skill_yaml`` fallback parser
    with PyYAML monkeypatched away.

The two fixture files under tests/fixtures/lus/ are the spec §9 examples
embedded VERBATIM (byte-exact, checksums real) — they are mandatory parser
fixtures per the spec.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gemia.lus import (
    LusValidationError,
    body_checksum,
    derive_name,
    detect_language,
    extract_tools_used,
    parse_lus,
    scan_lus_meta,
    serialize_lus,
    validate_lus,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lus"
FIXTURE_ZH_BYTES = (FIXTURE_DIR / "beat-cut-rough-cut.lus").read_bytes()
FIXTURE_EN_BYTES = (FIXTURE_DIR / "pitch-deck-title-cards.lus").read_bytes()
FIXTURE_ZH = FIXTURE_ZH_BYTES.decode("utf-8")
FIXTURE_EN = FIXTURE_EN_BYTES.decode("utf-8")


# ── helpers ──────────────────────────────────────────────────────────────

MINIMAL_BODY = (
    "\n## When to use\n"
    "When the user asks for a quick demo pass over the current cut.\n"
    "\n## Steps\n"
    "1. Call `get_timeline` and summarize the tracks.\n"
)


def minimal_meta(**over) -> dict:
    meta = {
        "name": "demo-skill",
        "version": "1.0.0",
        "lus_version": 1,
        "title": "Demo skill",
        "description": "When the user asks for a demo pass, run the demo steps.",
        "triggers": ["demo"],
        "domain": "general",
        "tools_used": ["get_timeline"],
        "parameters": {"type": "object", "properties": {}},
        "author": "lumeri-agent",
        "created_at": "2026-07-06T08:00:00+00:00",
        "updated_at": "2026-07-06T08:00:00+00:00",
        "language": "en",
        "safety": {"requires_paid_generation": False, "mutates_project": False},
    }
    meta.update(over)
    return meta


def build_lus(body: str = MINIMAL_BODY, magic: str = "#!lus/1", omit=(),
              checksum="auto", extra_lines=(), **over) -> str:
    """Assemble a .lus document with flow-style (JSON-scalar) metadata —
    JSON is a YAML subset, so this doubles as the flow-style read case."""
    meta = minimal_meta(**over)
    lines = [magic, "---"]
    for key, value in meta.items():
        if key in omit:
            continue
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    if checksum == "auto":
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        lines.append(f'checksum: "sha256:{digest}"')
    elif checksum is not None:
        lines.append(f"checksum: {json.dumps(checksum)}")
    lines.extend(extra_lines)
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def expect_error(text, code, field=None, **kwargs) -> LusValidationError:
    with pytest.raises(LusValidationError) as excinfo:
        validate_lus(text, **kwargs)
    err = excinfo.value
    assert err.code == code, f"expected {code}, got {err.code}: {err.message}"
    if field is not None:
        assert err.field == field, f"expected field {field!r}, got {err.field!r}"
    return err


# ── (a) fixtures: parse, zero warnings, checksum, byte-exact round-trip ──


def test_fixture_bytes_match_spec_sizes() -> None:
    # §9 states the exact on-disk sizes — guards fixture integrity.
    assert len(FIXTURE_ZH_BYTES) == 2716
    assert len(FIXTURE_EN_BYTES) == 2549


def test_fixture_zh_parses_clean_and_checksum_verifies() -> None:
    meta, body, warnings = validate_lus(FIXTURE_ZH, filename="beat-cut-rough-cut.lus")
    assert warnings == []
    assert meta.name == "beat-cut-rough-cut"
    assert meta.title == "卡点粗剪"
    assert meta.lus_version == 1
    assert meta.domain == "video"
    assert meta.language == "zh"
    assert meta.safety_requires_paid_generation is False
    assert meta.safety_mutates_project is True
    assert meta.triggers[0] == "卡点"
    assert "timeline_insert_clip" in meta.tools_used
    assert meta.parameters["required"] == ["music_asset"]
    assert meta.checksum == body_checksum(body)


def test_fixture_en_parses_clean_and_checksum_verifies() -> None:
    meta, body, warnings = validate_lus(FIXTURE_EN, filename="pitch-deck-title-cards.lus")
    assert warnings == []
    assert meta.name == "pitch-deck-title-cards"
    assert meta.domain == "deck"
    assert meta.language == "en"
    assert meta.safety_requires_paid_generation is True
    assert meta.parameters["properties"]["headings"]["items"] == {"type": "string"}
    assert meta.checksum == body_checksum(body)


def test_fixture_roundtrip_byte_exact() -> None:
    # WP1 (a): serialize_lus(*parse_lus(t)) == t, byte for byte.
    assert serialize_lus(*parse_lus(FIXTURE_ZH)) == FIXTURE_ZH
    assert serialize_lus(*parse_lus(FIXTURE_EN)) == FIXTURE_EN


def test_validate_accepts_bytes_input() -> None:
    meta, _body, warnings = validate_lus(FIXTURE_ZH_BYTES)
    assert meta.name == "beat-cut-rough-cut"
    assert warnings == []


def test_roundtrip_generated_matrix() -> None:
    cases = [
        {},  # plain en
        {   # CJK title/description/triggers
            "title": "卡点粗剪",
            "description": "根据节拍点快速拼装粗剪时间线。",
            "triggers": ["卡点", "beat cut"],
            "language": "zh",
        },
        {   # nested parameters schema
            "parameters": {
                "type": "object",
                "properties": {
                    "headings": {"type": "array", "items": {"type": "string"},
                                 "description": "Cards, in display order."},
                    "count": {"type": "integer", "minimum": 2},
                },
                "required": ["headings"],
            },
        },
    ]
    for over in cases:
        text = build_lus(**over)
        meta1, body1 = parse_lus(text)
        canonical = serialize_lus(meta1, body1)
        meta2, body2 = parse_lus(canonical)
        assert meta2 == meta1
        assert body2 == body1
        # canonical output is a fixed point (byte-stable)
        assert serialize_lus(meta2, body2) == canonical


def test_json_object_metadata_block_parses_identically() -> None:
    # JSON ⊂ YAML (§3.1): a single JSON object between the fences is valid.
    meta = minimal_meta()
    meta["checksum"] = body_checksum(MINIMAL_BODY)
    text = "#!lus/1\n---\n" + json.dumps(meta, ensure_ascii=False) + "\n---\n" + MINIMAL_BODY
    parsed, body, warnings = validate_lus(text)
    assert warnings == []
    assert parsed.name == "demo-skill"
    assert body == MINIMAL_BODY
    # and it canonicalizes to the same bytes as the flow-style variant
    assert serialize_lus(parsed, body) == serialize_lus(*parse_lus(build_lus()))


# ── (b) red cases: one per typed error code, in spec §6.1 order ──────────


def test_e_lus_encoding_invalid_utf8_bytes() -> None:
    expect_error(b"\xff\xfe\x00bad", "E_LUS_ENCODING")


def test_e_lus_encoding_bom() -> None:
    expect_error("\ufeff" + build_lus(), "E_LUS_ENCODING")


def test_e_lus_encoding_cr() -> None:
    expect_error(build_lus().replace("\n", "\r\n", 1), "E_LUS_ENCODING")


def test_e_lus_too_large() -> None:
    huge_body = "\n## When to use\nx\n\n## Steps\n1. do\n" + ("y" * 66_000) + "\n"
    expect_error(build_lus(body=huge_body), "E_LUS_TOO_LARGE")


def test_e_lus_magic() -> None:
    for bad in ("", "#!lus/01", "#!lus/1.2", " #!lus/1", "#!LUS/1", "#!lus/"):
        err = expect_error(build_lus(magic=bad) if bad else "", "E_LUS_MAGIC")
        assert err.line == 1


def test_e_lus_version_unsupported_major() -> None:
    err = expect_error(build_lus(magic="#!lus/2"), "E_LUS_VERSION")
    # message names the file's major and the supported set (§2.2)
    assert "2" in err.message and "1" in err.message


def test_e_lus_meta_open() -> None:
    text = "#!lus/1\nname: demo\n---\n" + MINIMAL_BODY
    err = expect_error(text, "E_LUS_META_OPEN")
    assert err.line == 2


def test_e_lus_meta_too_large() -> None:
    filler = "\n".join(f"filler_{i}: value" for i in range(600))  # > 8 KiB
    text = "#!lus/1\n---\n" + filler + "\n---\n" + MINIMAL_BODY
    expect_error(text, "E_LUS_META_TOO_LARGE")
    # no fence at all, but the file continues past 8 KiB
    text2 = "#!lus/1\n---\n" + filler + "\n"
    expect_error(text2, "E_LUS_META_TOO_LARGE")


def test_e_lus_meta_unterminated() -> None:
    expect_error("#!lus/1\n---\nname: demo-skill\n", "E_LUS_META_UNTERMINATED")


def test_e_lus_meta_parse_forbidden_features_and_non_mapping() -> None:
    # anchor / alias / tag are forbidden even though PyYAML accepts them
    expect_error(build_lus(extra_lines=["anchored: &a 1"]), "E_LUS_META_PARSE")
    expect_error(build_lus(extra_lines=["aliased: *a"]), "E_LUS_META_PARSE")
    expect_error(build_lus(extra_lines=["tagged: !!str hi"]), "E_LUS_META_PARSE")
    # non-mapping metadata
    expect_error("#!lus/1\n---\n- a\n- b\n---\n" + MINIMAL_BODY, "E_LUS_META_PARSE")


def test_e_lus_meta_field_missing_required() -> None:
    expect_error(build_lus(omit=("name",)), "E_LUS_META_FIELD", field="name")
    expect_error(build_lus(omit=("triggers",)), "E_LUS_META_FIELD", field="triggers")
    expect_error(build_lus(omit=("safety",)), "E_LUS_META_FIELD", field="safety")


def test_e_lus_meta_field_name_not_kebab() -> None:
    err = expect_error(build_lus(name="Bad_Name"), "E_LUS_META_FIELD", field="name")
    assert "kebab" in err.message


def test_e_lus_meta_field_bad_semver() -> None:
    expect_error(build_lus(version="1.0"), "E_LUS_META_FIELD", field="version")
    expect_error(build_lus(version="1.0.0-rc1"), "E_LUS_META_FIELD", field="version")


def test_e_lus_meta_field_lus_version_mismatch() -> None:
    expect_error(build_lus(lus_version=2), "E_LUS_META_FIELD", field="lus_version")


def test_e_lus_meta_field_title_and_description() -> None:
    expect_error(build_lus(title=""), "E_LUS_META_FIELD", field="title")
    expect_error(build_lus(title="t" * 81), "E_LUS_META_FIELD", field="title")
    expect_error(build_lus(description="line one\nline two"),
                 "E_LUS_META_FIELD", field="description")


def test_e_lus_meta_field_triggers() -> None:
    expect_error(build_lus(triggers=[]), "E_LUS_META_FIELD", field="triggers")
    expect_error(build_lus(triggers=[f"t{i}" for i in range(17)]),
                 "E_LUS_META_FIELD", field="triggers")
    expect_error(build_lus(triggers=["Demo", "demo"]),
                 "E_LUS_META_FIELD", field="triggers")
    expect_error(build_lus(triggers=["x" * 65]), "E_LUS_META_FIELD", field="triggers")


def test_e_lus_meta_field_domain_language_enums() -> None:
    expect_error(build_lus(domain="music"), "E_LUS_META_FIELD", field="domain")
    expect_error(build_lus(language="fr"), "E_LUS_META_FIELD", field="language")


def test_e_lus_meta_field_tools_used() -> None:
    expect_error(build_lus(tools_used=["Bad-Tool"]),
                 "E_LUS_META_FIELD", field="tools_used")
    expect_error(build_lus(tools_used=[f"tool_{i}" for i in range(33)]),
                 "E_LUS_META_FIELD", field="tools_used")


def test_e_lus_meta_field_parameters_subset() -> None:
    # root must be type: object
    expect_error(build_lus(parameters={"type": "array"}),
                 "E_LUS_META_FIELD", field="parameters.type")
    # representative dotted path: bad nested type value
    expect_error(
        build_lus(parameters={"type": "object",
                              "properties": {"foo": {"type": "blob"}}}),
        "E_LUS_META_FIELD", field="parameters.properties.foo.type")
    # key outside the subset
    expect_error(
        build_lus(parameters={"type": "object", "properties": {},
                              "patternProperties": {}}),
        "E_LUS_META_FIELD", field="parameters.patternProperties")
    # required ⊄ properties
    expect_error(
        build_lus(parameters={"type": "object", "properties": {},
                              "required": ["ghost"]}),
        "E_LUS_META_FIELD", field="parameters.required")
    # nesting depth > 4
    deep = {"type": "object", "properties": {"a": {"type": "array", "items": {
        "type": "object", "properties": {"b": {"type": "object", "properties": {
            "c": {"type": "string"}}}}}}}}
    err = expect_error(build_lus(parameters=deep), "E_LUS_META_FIELD")
    assert err.field.endswith("properties.c")
    # > 16 properties at root
    wide = {"type": "object",
            "properties": {f"p{i}": {"type": "string"} for i in range(17)}}
    expect_error(build_lus(parameters=wide),
                 "E_LUS_META_FIELD", field="parameters.properties")


def test_e_lus_meta_field_timestamps() -> None:
    expect_error(build_lus(created_at="2026-07-06T08:00:00"),  # naive
                 "E_LUS_META_FIELD", field="created_at")
    expect_error(build_lus(created_at="not-a-date"),
                 "E_LUS_META_FIELD", field="created_at")
    expect_error(build_lus(updated_at="2026-07-05T08:00:00+00:00"),  # < created
                 "E_LUS_META_FIELD", field="updated_at")


def test_e_lus_meta_field_safety_shapes() -> None:
    # representative field assertion: dotted safety key
    expect_error(
        build_lus(safety={"requires_paid_generation": False}),
        "E_LUS_META_FIELD", field="safety.mutates_project")
    expect_error(
        build_lus(safety={"requires_paid_generation": False,
                          "mutates_project": "yes"}),
        "E_LUS_META_FIELD", field="safety.mutates_project")


def test_e_lus_meta_field_malformed_checksum_is_hard_error() -> None:
    # malformed ≠ stale: hard field error even in non-strict mode (§3.2)
    expect_error(build_lus(checksum="sha256:XYZ"),
                 "E_LUS_META_FIELD", field="checksum")
    expect_error(build_lus(checksum="md5:" + "0" * 32),
                 "E_LUS_META_FIELD", field="checksum")


def test_e_lus_body_empty() -> None:
    expect_error(build_lus(body="\n\n"), "E_LUS_BODY_EMPTY")


def test_e_lus_body_section_variants() -> None:
    # missing ## Steps
    expect_error(build_lus(body="\n## When to use\nWhen asked.\n"),
                 "E_LUS_BODY_SECTION")
    # non-blank content before ## When to use
    expect_error(build_lus(
        body="\nIntro prose.\n\n## When to use\nWhen asked.\n\n## Steps\n1. Do it.\n"),
        "E_LUS_BODY_SECTION")
    # duplicate known heading
    expect_error(build_lus(
        body="\n## When to use\nWhen asked.\n\n## Steps\n1. Do.\n\n## Steps\n2. Redo.\n"),
        "E_LUS_BODY_SECTION")
    # ## Steps without a numbered item
    expect_error(build_lus(
        body="\n## When to use\nWhen asked.\n\n## Steps\n- bullet, not numbered\n"),
        "E_LUS_BODY_SECTION")
    # optional sections out of order (Examples before Pitfalls)
    expect_error(build_lus(
        body="\n## When to use\nWhen asked.\n\n## Steps\n1. Do.\n\n"
             "## Examples\nx\n\n## Pitfalls\ny\n"),
        "E_LUS_BODY_SECTION")
    # unknown heading before ## Steps (allowed only after it, §4.1)
    expect_error(build_lus(
        body="\n## When to use\nWhen asked.\n\n## Bogus\nx\n\n## Steps\n1. Do.\n"),
        "E_LUS_BODY_SECTION")


def test_body_unknown_heading_after_steps_is_allowed() -> None:
    text = build_lus(
        body="\n## When to use\nWhen asked.\n\n## Steps\n1. Do.\n\n## See also\nrelated\n")
    meta, _body, warnings = validate_lus(text)
    assert meta.name == "demo-skill"
    assert warnings == []


def test_e_lus_body_fence_unbalanced() -> None:
    expect_error(build_lus(
        body="\n## When to use\nWhen asked.\n\n## Steps\n1. Do.\n\n"
             "## Examples\n```json\n{\"tool\": \"get_timeline\"}\n"),
        "E_LUS_BODY_FENCE")


def test_e_lus_secret_never_echoes_match() -> None:
    secret = "sk-abcdefghij0123456789"
    err = expect_error(build_lus(
        body=f"\n## When to use\nUse token {secret} here.\n\n## Steps\n1. Do.\n"),
        "E_LUS_SECRET")
    assert secret not in err.message
    assert secret not in str(err)


def test_e_lus_secret_other_patterns() -> None:
    for payload in (
        "AKIA0123456789ABCDEF",
        "Bearer abcdefghijklmnop.qrstuvwx",
        "-----BEGIN RSA PRIVATE KEY-----",
        "password = hunter2hunter2",
    ):
        expect_error(build_lus(
            body=f"\n## When to use\n{payload}\n\n## Steps\n1. Do.\n"),
            "E_LUS_SECRET")


def test_e_lus_abs_path() -> None:
    for payload in (
        "read /Users/alice/clip.mp4",
        "check /Volumes/SSD/media",
        "open /home/bob/x",
        "look in ~/Movies/raw",
        "win path C:\\Users\\bob\\clip.mp4",
    ):
        expect_error(build_lus(
            body=f"\n## When to use\n{payload}\n\n## Steps\n1. Do.\n"),
            "E_LUS_ABS_PATH")


def test_e_lus_checksum_strict_and_stale_nonstrict() -> None:
    mutated = FIXTURE_ZH.replace("0.05s", "0.06s")
    # non-strict: warning, not error (D3)
    meta, _body, warnings = validate_lus(mutated)
    assert [w.code for w in warnings] == ["W_LUS_CHECKSUM_STALE"]
    assert meta.checksum is not None
    # strict: typed error
    expect_error(mutated, "E_LUS_CHECKSUM", strict=True)


# ── check-order tests (error precedence is deterministic, §6.1) ──────────


def test_order_too_large_beats_bad_magic() -> None:
    expect_error("#!wrong\n" + ("x" * 66_000), "E_LUS_TOO_LARGE")


def test_order_encoding_beats_too_large() -> None:
    expect_error("#!wrong\r\n" + ("x" * 66_000), "E_LUS_ENCODING")


def test_order_meta_field_beats_body_section() -> None:
    # bad domain AND missing ## Steps → meta field wins (checked earlier)
    expect_error(build_lus(domain="music", body="\n## When to use\nx\n"),
                 "E_LUS_META_FIELD", field="domain")


def test_order_secret_beats_strict_checksum() -> None:
    text = build_lus(
        body="\n## When to use\nsk-abcdefghij0123456789\n\n## Steps\n1. Do.\n",
        checksum="sha256:" + "0" * 64)
    expect_error(text, "E_LUS_SECRET", strict=True)


# ── (c) warnings — all five W_LUS_* codes ────────────────────────────────


def test_w_lus_unknown_tool() -> None:
    text = build_lus(tools_used=["get_timeline", "mystery_verb"])
    _meta, _body, warnings = validate_lus(text, known_tools=frozenset({"get_timeline"}))
    unknown = [w for w in warnings if w.code == "W_LUS_UNKNOWN_TOOL"]
    assert len(unknown) == 1
    assert unknown[0].field == "tools_used"
    assert "mystery_verb" in unknown[0].message
    # known_tools=None skips the check entirely
    _meta, _body, warnings = validate_lus(text)
    assert all(w.code != "W_LUS_UNKNOWN_TOOL" for w in warnings)


def test_w_lus_unknown_field_preserved_in_extra_and_roundtrip() -> None:
    text = build_lus(extra_lines=['x_future: "hi"'])
    meta, body, warnings = validate_lus(text)
    assert [w.code for w in warnings] == ["W_LUS_UNKNOWN_FIELD"]
    assert meta.extra == {"x_future": "hi"}
    # preserved on round-trip (§3.2)
    meta2, _body2, warnings2 = validate_lus(serialize_lus(meta, body))
    assert meta2.extra == {"x_future": "hi"}
    assert [w.code for w in warnings2] == ["W_LUS_UNKNOWN_FIELD"]


def test_w_lus_unknown_field_extra_safety_key() -> None:
    text = build_lus(safety={"requires_paid_generation": False,
                             "mutates_project": False, "reviewed": True})
    _meta, _body, warnings = validate_lus(text)
    fields = [w.field for w in warnings if w.code == "W_LUS_UNKNOWN_FIELD"]
    assert "safety.reviewed" in fields


def test_w_lus_checksum_missing() -> None:
    _meta, _body, warnings = validate_lus(build_lus(checksum=None))
    assert [w.code for w in warnings] == ["W_LUS_CHECKSUM_MISSING"]
    # missing stays a warning even in strict mode (§6.1: E_LUS_CHECKSUM
    # requires the checksum to be PRESENT)
    _meta, _body, warnings = validate_lus(build_lus(checksum=None), strict=True)
    assert [w.code for w in warnings] == ["W_LUS_CHECKSUM_MISSING"]


def test_w_lus_name_mismatch_only_with_filename() -> None:
    _meta, _body, warnings = validate_lus(FIXTURE_ZH, filename="wrong-name.lus")
    assert [w.code for w in warnings] == ["W_LUS_NAME_MISMATCH"]
    _meta, _body, warnings = validate_lus(FIXTURE_ZH)
    assert warnings == []


# ── (e) fallback parser (PyYAML absent) ──────────────────────────────────


def test_fixtures_parse_under_fallback_yaml_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    from gemia.ai import skill_yaml

    monkeypatch.setattr(skill_yaml, "_pyyaml", None)
    for text, expected in ((FIXTURE_ZH, "beat-cut-rough-cut"),
                           (FIXTURE_EN, "pitch-deck-title-cards")):
        meta, body, warnings = validate_lus(text)
        assert meta.name == expected
        assert warnings == []
        assert serialize_lus(meta, body) == text  # byte-stable without PyYAML


# ── helper functions (§7.1 derivations) ──────────────────────────────────


def test_derive_name() -> None:
    assert derive_name("Pitch-deck style title cards") == "pitch-deck-style-title-cards"
    assert derive_name("  Denoise then Sharpen! ") == "denoise-then-sharpen"
    cjk = derive_name("卡点粗剪")
    assert cjk.startswith("skill-") and len(cjk) == len("skill-") + 8
    assert cjk == derive_name("卡点粗剪")  # deterministic


def test_detect_language() -> None:
    assert detect_language("Only English prose here.") == "en"
    assert detect_language("全中文的说明，包含 `analyze_media` 工具名。") == "zh"
    assert detect_language("Half English half 中文 mixed prose with lots of English words here 中") == "mixed"


def test_extract_tools_used() -> None:
    known = frozenset({"get_timeline", "timeline_insert_clip", "project_export"})
    text = "1. Call `get_timeline` twice.\n2. `timeline_insert_clip` then get_timeline again."
    assert extract_tools_used(text, known) == ["get_timeline", "timeline_insert_clip"]
    assert extract_tools_used(text, frozenset()) == []


def test_scan_lus_meta_prefix_only() -> None:
    meta = scan_lus_meta(FIXTURE_ZH_BYTES[:8192])
    assert meta.name == "beat-cut-rough-cut"
    assert meta.title == "卡点粗剪"
    with pytest.raises(LusValidationError):
        scan_lus_meta(b"not a lus file")
