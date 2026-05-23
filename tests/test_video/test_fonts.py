from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

from gemia.video import fonts as fontlib


def setup_function() -> None:
    fontlib._cached_font_catalog.cache_clear()


def teardown_function() -> None:
    fontlib._cached_font_catalog.cache_clear()


def test_font_catalog_resolves_id_and_family(monkeypatch, tmp_path: Path) -> None:
    font_path = tmp_path / "PingFang-Test.ttf"
    font_path.write_bytes(b"fake")

    class FakeFont:
        def getname(self) -> tuple[str, str]:
            return ("PingFang Test", "Regular")

    monkeypatch.setattr(fontlib, "font_roots", lambda **_kwargs: [tmp_path])
    monkeypatch.setattr(ImageFont, "truetype", lambda *_args, **_kwargs: FakeFont())
    fontlib.get_font_catalog(refresh=True)

    catalog = fontlib.get_font_catalog()

    assert len(catalog) == 1
    assert catalog[0].is_default
    assert catalog[0].supports_cjk_hint
    assert fontlib.resolve_font_path({"font_id": catalog[0].id}) == str(font_path.resolve())
    assert fontlib.resolve_font_path({"family": "PingFang Test"}) == str(font_path.resolve())


def test_font_catalog_payload_is_json_ready(monkeypatch, tmp_path: Path) -> None:
    font_path = tmp_path / "Helvetica-Test.otf"
    font_path.write_bytes(b"fake")

    class FakeFont:
        def getname(self) -> tuple[str, str]:
            return ("Helvetica Test", "Bold")

    monkeypatch.setattr(fontlib, "font_roots", lambda **_kwargs: [tmp_path])
    monkeypatch.setattr(ImageFont, "truetype", lambda *_args, **_kwargs: FakeFont())
    fontlib.get_font_catalog(refresh=True)

    payload = fontlib.font_catalog_payload()

    assert payload["count"] == 1
    assert payload["default_font"]["family"] == "Helvetica Test"
    assert payload["fonts"][0]["style"] == "Bold"


def test_google_fonts_payload_uses_metadata_client(monkeypatch) -> None:
    monkeypatch.setattr(
        fontlib,
        "get_google_fonts",
        lambda **_kwargs: [
            fontlib.GoogleFontRecord(
                family="Noto Sans SC",
                category="sans-serif",
                variants=["regular"],
                subsets=["chinese-simplified", "latin"],
                files={"regular": "https://fonts.gstatic.com/notosanssc.ttf"},
            )
        ],
    )

    payload = fontlib.google_fonts_payload(limit=1)

    assert payload["available"] is True
    assert payload["count"] == 1
    assert payload["fonts"][0]["family"] == "Noto Sans SC"
