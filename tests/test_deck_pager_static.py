import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "static" / "v3" / "deck.html"
CSS_PATH = ROOT / "static" / "v3" / "deck.css"
JS_PATH = ROOT / "static" / "v3" / "deck.js"
V3_JS_PATH = ROOT / "static" / "v3" / "v3.js"


def test_deck_pager_is_self_contained_and_referrer_safe() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert '<meta name="referrer" content="no-referrer"' in html
    assert 'default-src \'none\'' in html
    assert 'img-src \'self\'' in html
    assert '<link rel="stylesheet" href="/v3/deck.css"' in html
    assert '<script src="/v3/deck.js" defer>' in html
    assert 'referrerpolicy="no-referrer"' in html
    assert "https://" not in html
    assert "http://" not in html


def test_deck_pager_source_locks_asset_urls_and_avoids_html_injection() -> None:
    source = JS_PATH.read_text(encoding="utf-8")

    assert 'params.getAll("session_id")' in source
    assert 'params.getAll("frame")' in source
    assert "MAX_FRAMES = 512" in source
    assert "/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/" in source
    assert "/^(0|[1-9]\\d*):(0|[1-9]\\d*):([A-Za-z0-9_-]{1,64})$/" in source
    assert "`/sessions/${encodeURIComponent(sessionId)}/assets/${encodeURIComponent(assetId)}`" in source
    assert "innerHTML" not in source
    assert "document.write" not in source
    assert "eval(" not in source
    assert "fetch(" not in source
    assert "textContent" in source


def test_deck_pager_has_required_navigation_and_next_frame_preload() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")

    for key in ("Spacebar", "ArrowRight", "PageDown", "ArrowLeft", "PageUp", "Home", "End"):
      assert key in source
    assert "isFormControlOrEditable(event.target)" in source
    assert 'stage.addEventListener("click"' in source
    assert "new root.Image()" in source
    assert "preloadNextFrame()" in source
    assert "aspect-ratio: 16 / 9" in css
    assert "object-fit: contain" in css


def test_main_v3_ui_only_surfaces_same_origin_deck_pager_urls() -> None:
    source = V3_JS_PATH.read_text(encoding="utf-8")

    assert "tc.pagerUrl = safeDeckPagerUrl(ev.result?.pager_url)" in source
    assert 'parsed.origin !== window.location.origin' in source
    assert 'parsed.pathname !== "/v3/deck.html"' in source
    assert "present deck ↗" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_deck_query_parser_and_navigation_execute_without_browser_dependencies() -> None:
    node_program = f"""
const core = require({json.dumps(str(JS_PATH))});
const valid = core.parseDeckQuery("?session_id=session_1&frame=2:3:img_second&frame=0:0:img_first");
const empty = core.parseDeckQuery("?session_id=session_1");
const invalid = [
  core.parseDeckQuery("?session_id=../bad&frame=0:0:img_1"),
  core.parseDeckQuery("?session_id=session_1&frame=-1:0:img_1"),
  core.parseDeckQuery("?session_id=session_1&frame=0:0:..%2Fsecret"),
  core.parseDeckQuery("?session_id=session_1&other=value"),
  core.parseDeckQuery("?session_id=session_1&session_id=session_2"),
];
const tooMany = new URLSearchParams({{ session_id: "session_1" }});
for (let i = 0; i < 513; i += 1) tooMany.append("frame", `0:${{i}}:img_${{i}}`);
invalid.push(core.parseDeckQuery(`?${{tooMany.toString()}}`));
const output = {{
  valid,
  empty,
  invalid: invalid.map((entry) => entry.ok),
  url: core.assetUrl("session_1", "img-safe_2"),
  navigation: [
    core.navigationIndex(0, "previous", 3),
    core.navigationIndex(0, "next", 3),
    core.navigationIndex(1, "last", 3),
    core.navigationIndex(2, "next", 3),
    core.navigationIndex(2, "first", 3),
  ],
}};
process.stdout.write(JSON.stringify(output));
"""
    completed = subprocess.run(
        [shutil.which("node"), "-e", node_program],
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(completed.stdout)

    assert output["valid"]["ok"] is True
    assert output["valid"]["sessionId"] == "session_1"
    assert output["valid"]["frames"] == [
        {"slideIndex": 2, "buildIndex": 3, "assetId": "img_second"},
        {"slideIndex": 0, "buildIndex": 0, "assetId": "img_first"},
    ]
    assert output["empty"]["ok"] is True
    assert output["empty"]["frames"] == []
    assert output["invalid"] == [False, False, False, False, False, False]
    assert output["url"] == "/sessions/session_1/assets/img-safe_2"
    assert output["navigation"] == [0, 1, 2, 2, 0]
