from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workspace_keeps_preview_and_side_modules_in_one_board() -> None:
    html = (ROOT / "static/v3/index.html").read_text(encoding="utf-8")

    assert 'id="workspace-board"' in html
    assert 'class="workspace-module workspace-preview-module" data-workspace-module="preview"' in html
    assert 'id="stage-panel" aria-label="工作区模块"' in html
    assert html.index('id="empty-state"') < html.index('id="stage-panel"')
    assert html.index('id="stage-panel"') < html.index('id="timeline-drawer"')
    assert html.index('id="timeline-drawer"') < html.index('</div>\n  </section>')


def test_default_modules_are_simultaneous_and_persisted() -> None:
    source = (ROOT / "static/v3/v3.js").read_text(encoding="utf-8")

    assert 'const DEFAULT_MODULES = ["timeline", "outline", "tasks"]' in source
    assert 'lumeri:v3:module-layout' in source
    assert 'data-panel-body="${k}"' in source
    assert 'stageTabs.filter((k) => PANEL_MODULES.has(k)).forEach(refreshPanel)' in source
    assert 'previewStage.dataset.tab = panel ? "panel" : "preview"' not in source
    assert 'if (panelView !==' not in source


def test_modular_layout_uses_draggable_non_overlapping_grid() -> None:
    css = (ROOT / "static/v3/v3.css").read_text(encoding="utf-8")
    source = (ROOT / "static/v3/v3.js").read_text(encoding="utf-8")

    assert 'grid-template-columns: repeat(12, minmax(0, 1fr))' in css
    assert 'repeat(var(--workspace-rows, 10), minmax(52px, 1fr))' in css
    assert '.module-resize-edge-x' in css
    assert '.module-resize-edge-y' in css
    assert '.workspace-side-module.is-focused' in css
    assert 'WorkspaceLayout.packModules' in source
    assert 'data-module-drag="${k}"' in source
    assert 'lumeri:v3:workspace-order' in source
    assert 'lumeri:v3:workspace-sizes' in source
