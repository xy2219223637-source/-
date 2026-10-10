from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.tts import synthesize_speech


class DoubaoTTSAuthenticationTests(unittest.TestCase):
    @staticmethod
    def settings(api_key: str):
        return SimpleNamespace(
            doubao_tts_api_key=api_key,
            doubao_tts_resource_id="seed-tts-2.0",
            doubao_tts_speaker="voice-id",
        )

    def test_requires_new_console_api_key(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("app.services.tts.get_settings", return_value=self.settings("")):
            with self.assertRaisesRegex(RuntimeError, "API Key"):
                synthesize_speech("测试", Path(directory) / "speech.mp3")

    def test_sends_only_new_x_api_key_authentication(self):
        audio = b"fake-mp3"
        response = unittest.mock.MagicMock()
        response.iter_lines.return_value = [json.dumps({
            "code": 0, "data": base64.b64encode(audio).decode("ascii"),
        })]
        client = unittest.mock.MagicMock()
        client.__enter__.return_value = client
        client.stream.return_value.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as directory, \
                patch("app.services.tts.get_settings", return_value=self.settings("new-api-key")), \
                patch("app.services.tts.httpx.Client", return_value=client):
            output = Path(directory) / "speech.mp3"
            synthesize_speech("测试", output)
            self.assertEqual(output.read_bytes(), audio)
        headers = client.stream.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Api-Key"], "new-api-key")
        self.assertNotIn("X-Api-App-Id", headers)
        self.assertNotIn("X-Api-Access-Key", headers)


if __name__ == "__main__":
    unittest.main()
