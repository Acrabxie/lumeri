from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v3_composer_exposes_accessible_voice_input() -> None:
    html = (ROOT / "static/v3/index.html").read_text(encoding="utf-8")

    assert 'id="send-btn"' in html
    assert 'aria-label="语音输入"' in html
    assert 'id="voice-input-status"' in html
    assert 'aria-live="polite"' in html


def test_voice_recognition_is_review_before_send_and_has_fallbacks() -> None:
    source = (ROOT / "static/v3/v3.js").read_text(encoding="utf-8")

    assert "voiceInputBtn" not in source
    assert "window.SpeechRecognition || window.webkitSpeechRecognition" in source
    assert "recognition.interimResults = true" in source
    assert "语音已转成文字，请确认后发送" in source
    assert "此浏览器不支持语音输入" in source
    assert "麦克风权限被拒绝" in source
    assert "navigator.mediaDevices.getUserMedia({ audio: true })" in source
    assert "正在申请麦克风权限" in source
    assert "recognition.start()" in source
    assert source.index("navigator.mediaDevices.getUserMedia({ audio: true })") < source.index("recognition.start()")
    assert "els.sendBtn.click()" not in source[source.index("function startVoiceInput"):source.index("// Starter suggestion chips")]


def test_voice_input_respects_reduced_motion() -> None:
    css = (ROOT / "static/v3/v3.css").read_text(encoding="utf-8")

    assert "@keyframes voice-listening-pulse" in css
    assert ".send-btn.is-listening::after" in css
