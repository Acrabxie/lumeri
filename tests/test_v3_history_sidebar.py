from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_history_is_a_rightmost_layout_sidebar() -> None:
    html = (ROOT / "static/v3/index.html").read_text(encoding="utf-8")
    css = (ROOT / "static/v3/v3.css").read_text(encoding="utf-8")

    assert html.index('id="chat-rail"') < html.index('id="history-drawer"')
    assert "grid-template-columns: 1fr 400px 280px" in css
    assert ".history-drawer-head" in css
    assert "border-left: 1px solid var(--m3-outline-variant)" in css


def test_selecting_history_keeps_sidebar_open_and_marks_selection() -> None:
    source = (ROOT / "static/v3/v3.js").read_text(encoding="utf-8")
    selection_handler = source.split(
        'body.querySelectorAll(".history-row").forEach((btn) => {', 1
    )[1].split("async function loadHistorySession", 1)[0]

    assert "await loadHistorySession(id)" in selection_handler
    assert "toggleHistoryDrawer(false)" not in selection_handler
    assert 'row.classList.toggle("is-active", selected)' in selection_handler
    assert 'row.setAttribute("aria-current", "true")' in selection_handler
