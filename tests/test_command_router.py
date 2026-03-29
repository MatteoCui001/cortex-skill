"""
Tests for the command router -- no network, all API calls mocked.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add scripts to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from command_router import CortexClient, route, RouterResult


@pytest.fixture
def mock_client():
    client = MagicMock(spec=CortexClient)
    client.base_url = "http://127.0.0.1:8420/api/v1"
    client.workspace = "default"
    return client


class TestInboxCommand:

    def test_inbox_keyword(self, mock_client):
        mock_client.get_notifications.return_value = [
            {"id": "abc12345-full-id", "title": "New signal", "status": "pending"}
        ]
        result = route("inbox", mock_client)
        assert result.action == "inbox"
        assert "1 notification" in result.summary
        mock_client.get_notifications.assert_called_once()

    def test_inbox_chinese(self, mock_client):
        mock_client.get_notifications.return_value = []
        result = route("收件箱", mock_client)
        assert result.action == "inbox"
        assert "No pending" in result.summary

    def test_inbox_empty(self, mock_client):
        mock_client.get_notifications.return_value = []
        result = route("通知", mock_client)
        assert result.action == "inbox"
        assert "No pending" in result.summary


class TestActionCommands:

    def test_read_notification(self, mock_client):
        mock_client.notification_action.return_value = {"status": "read"}
        result = route("read abc12345", mock_client)
        assert result.action == "read"
        mock_client.notification_action.assert_called_once_with("abc12345", "read")

    def test_ack_notification(self, mock_client):
        mock_client.notification_action.return_value = {"status": "acked"}
        result = route("ack abc12345", mock_client)
        assert result.action == "ack"

    def test_dismiss_notification(self, mock_client):
        mock_client.notification_action.return_value = {"status": "dismissed"}
        result = route("dismiss abc12345", mock_client)
        assert result.action == "dismiss"

    def test_useful_feedback(self, mock_client):
        mock_client.signal_feedback.return_value = {"verdict": "useful"}
        result = route("useful sig123", mock_client)
        assert result.action == "feedback"
        mock_client.signal_feedback.assert_called_once_with("sig123", "useful")

    def test_not_useful_feedback(self, mock_client):
        mock_client.signal_feedback.return_value = {"verdict": "not_useful"}
        result = route("not_useful sig123", mock_client)
        assert result.action == "feedback"


class TestIngestCommands:

    def test_url_ingest(self, mock_client):
        mock_client.ingest_url.return_value = {"title": "Some Article", "id": "e1"}
        result = route("https://example.com/article", mock_client)
        assert result.action == "ingest_url"
        mock_client.ingest_url.assert_called_once_with("https://example.com/article", "")

    def test_url_with_annotation(self, mock_client):
        mock_client.ingest_url.return_value = {"title": "Article", "id": "e2"}
        result = route("这篇很好 https://example.com/article", mock_client)
        assert result.action == "ingest_url"
        mock_client.ingest_url.assert_called_once_with(
            "https://example.com/article", "这篇很好",
        )

    def test_text_ingest(self, mock_client):
        mock_client.ingest_text.return_value = {"title": "note", "id": "e3"}
        result = route("今天聊了恒辉，创始人很有想法", mock_client)
        assert result.action == "ingest_text"
        mock_client.ingest_text.assert_called_once()


class TestOpenClawSink:

    def test_dry_run_mode(self, capsys):
        from openclaw_sink import OpenClawSink
        sink = OpenClawSink(ingress_url="")
        ok, detail = sink.send({"title": "test"})
        assert ok is True
        assert detail == "dry-run"
        captured = capsys.readouterr()
        assert "test" in captured.out


class TestConfigLoader:

    def test_client_from_config_defaults(self, tmp_path, monkeypatch):
        """client_from_config returns defaults when no config file exists."""
        monkeypatch.setattr(
            "command_router.SKILL_CONFIG_PATH", tmp_path / "missing.yaml"
        )
        from command_router import client_from_config
        client = client_from_config()
        assert client.base_url == "http://127.0.0.1:8420/api/v1"
        assert client.workspace == "default"
        assert client.api_token == ""

    def test_client_from_config_reads_yaml(self, tmp_path, monkeypatch):
        """client_from_config reads skill_config.yaml correctly."""
        cfg = tmp_path / "skill_config.yaml"
        cfg.write_text(
            'cortex:\n'
            '  base_url: http://10.0.0.1:9999/api/v1\n'
            '  api_token: "test-token-123"\n'
            '  workspace: myws\n'
        )
        monkeypatch.setattr("command_router.SKILL_CONFIG_PATH", cfg)
        from command_router import client_from_config
        client = client_from_config()
        assert client.base_url == "http://10.0.0.1:9999/api/v1"
        assert client.api_token == "test-token-123"
        assert client.workspace == "myws"
