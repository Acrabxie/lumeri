import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import base64
import pytest

from gemia.ai.audio_client import AudioClient, resolve21_ai_speech_generator, SpeechGenerationArtifact
from gemia.audio.effects import text_to_speech as local_text_to_speech
from gemia.primitives_common import ensure_path_exists


@pytest.fixture
def mock_temp_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)

@pytest.fixture
def mock_gemini_api_key():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_api_key"}):
        yield

@pytest.fixture
def mock_local_tts():
    with patch("gemia.ai.audio_client.local_text_to_speech") as mock_tts:
        mock_tts.return_value = "mock_audio_file.mp3"
        yield mock_tts

class TestAudioClient:

    def test_init_no_api_key_no_dry_run_raises_error(self):
        # Ensure API key is not set
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="Set GEMINI_API_KEY"):
                AudioClient(dry_run=False)

    def test_init_no_api_key_with_dry_run_succeeds(self):
        with patch.dict(os.environ, {}, clear=True):
            client = AudioClient(dry_run=True)
            assert client.dry_run is True

    @patch("gemia.ai.audio_client.local_text_to_speech")
    def test_generate_speech_dry_run(self, mock_tts, mock_temp_dir):
        client = AudioClient(dry_run=True)
        text = "Hello, this is a dry run."
        output_dir = str(mock_temp_dir / "dry_run_output")
        ensure_path_exists(output_dir)

        artifact = client.generate_speech_from_text(text, output_dir)

        mock_tts.assert_called_once_with(
            text,
            str(Path(output_dir) / f"speech_{Path(output_dir).name}.mp3"),
            voice="Samantha",
            rate=175
        )
        assert artifact["dry_run_activated"] is True
        assert artifact["text"] == text
        assert Path(artifact["output_audio_path"]).name == "speech_dry_run_output.mp3"
        assert Path(artifact["output_audio_path"]).exists() # local_text_to_speech creates it, but mock doesn't
        assert Path(artifact["output_audio_path"]).parent == Path(output_dir)
        
        # Manually create the dummy audio file for the path to exist for testing
        # Path(artifact["output_audio_path"]).write_bytes(b"dummy_audio_content")

        artifact_file = Path(output_dir) / f"speech_artifact_{Path(output_dir).name}.json"
        assert artifact_file.exists()
        loaded_artifact = json.loads(artifact_file.read_text())
        assert loaded_artifact["dry_run_activated"] is True
        assert loaded_artifact["text"] == text

    @patch("gemia.ai.audio_client.AudioClient._post_json")
    def test_generate_speech_api_call(self, mock_post_json, mock_gemini_api_key, mock_temp_dir):
        # Mock the Gemini API response
        mock_post_json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"inlineData": {"mimeType": "audio/mpeg", "data": base64.b64encode(b"real audio data").decode("ascii")}},
                        ]
                    }
                }
            ]
        }
        
        client = AudioClient(dry_run=False)
        text = "Hello, this is a real API call."
        output_dir = str(mock_temp_dir / "api_call_output")
        ensure_path_exists(output_dir)

        artifact = client.generate_speech_from_text(text, output_dir)

        mock_post_json.assert_called_once()
        args, kwargs = mock_post_json.call_args
        payload = args[1] # payload is the second argument

        assert payload["contents"][0]["parts"][0]["text"] == text
        assert payload["generationConfig"]["audioConfig"]["voice"] == "default"
        assert payload["generationConfig"]["audioConfig"]["performance"] == "neutral"
        assert artifact["dry_run_activated"] is False
        assert Path(artifact["output_audio_path"]).exists()
        assert Path(artifact["output_audio_path"]).read_bytes() == b"real audio data"

        artifact_file = Path(output_dir) / f"speech_artifact_{Path(output_dir).name}.json"
        assert artifact_file.exists()
        loaded_artifact = json.loads(artifact_file.read_text())
        assert loaded_artifact["dry_run_activated"] is False
        assert loaded_artifact["text"] == text

    def test_resolve21_ai_speech_generator_primitive_dry_run(self, mock_local_tts, mock_temp_dir):
        text = "Testing the primitive function with dry run."
        output_dir = str(mock_temp_dir / "primitive_dry_run")
        ensure_path_exists(output_dir)

        artifact = resolve21_ai_speech_generator(
            text=text,
            output_dir=output_dir,
            voice="custom_voice",
            performance="expressive",
            timing_metadata_requested=True,
            dry_run=True
        )

        mock_local_tts.assert_called_once()
        assert artifact["dry_run_activated"] is True
        assert artifact["voice"] == "custom_voice"
        assert artifact["performance"] == "expressive"
        assert artifact["timing_metadata_requested"] is True
        assert artifact["model"] == "dry-run-local-tts"
        # Path(artifact["output_audio_path"]).write_bytes(b"dummy_audio_content")
        assert Path(artifact["output_audio_path"]).exists()

    @patch("gemia.ai.audio_client.AudioClient._post_json")
    def test_resolve21_ai_speech_generator_primitive_api_call(self, mock_post_json, mock_gemini_api_key, mock_temp_dir):
        mock_post_json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"inlineData": {"mimeType": "audio/mpeg", "data": base64.b64encode(b"primitive audio").decode("ascii")}},
                        ]
                    }
                }
            ]
        }

        text = "Testing the primitive function with API call."
        output_dir = str(mock_temp_dir / "primitive_api_call")
        ensure_path_exists(output_dir)

        artifact = resolve21_ai_speech_generator(
            text=text,
            output_dir=output_dir,
            dry_run=False,
            model_tier="pro"
        )
        
        mock_post_json.assert_called_once()
        args, kwargs = mock_post_json.call_args
        payload = args[1] # payload is the second argument

        assert payload["contents"][0]["parts"][0]["text"] == text
        assert payload["generationConfig"]["audioConfig"]["voice"] == "default"
        assert artifact["dry_run_activated"] is False
        assert Path(artifact["output_audio_path"]).exists()
        assert Path(artifact["output_audio_path"]).read_bytes() == b"primitive audio"

    @patch("gemia.ai.audio_client.AudioClient._post_json")
    def test_api_call_no_audio_in_response_raises_error(self, mock_post_json, mock_gemini_api_key, mock_temp_dir):
        mock_post_json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "No audio here."},
                        ]
                    }
                }
            ]
        }
        client = AudioClient(dry_run=False)
        output_dir = str(mock_temp_dir / "no_audio_output")
        ensure_path_exists(output_dir)
        with pytest.raises(RuntimeError, match="Gemini returned no audio"):
            client.generate_speech_from_text("some text", output_dir)
