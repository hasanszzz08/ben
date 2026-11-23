"""Tests for BEN configuration, security, and Anthropic Claude API client manager."""

from unittest.mock import MagicMock, patch
import pytest

from ben.config import Settings, sanitize_key
from ben.core.client import (
    AnthropicClientManager,
    ClaudeConnectionResult,
    GeminiClientManager,
    redact_sensitive
)


def test_sanitize_key_placeholders():
    """Verify placeholders and whitespace are stripped and treated as None."""
    assert sanitize_key(None) is None
    assert sanitize_key("") is None
    assert sanitize_key("   ") is None
    assert sanitize_key("your_anthropic_api_key_here") is None
    assert sanitize_key("your_gemini_api_key_here") is None
    assert sanitize_key("'your_actual_key_here'") is None
    assert sanitize_key('"your_actual_gemini_api_key_here"') is None
    assert sanitize_key("  valid_custom_key_12345  ") == "valid_custom_key_12345"


def test_settings_diagnostics_hides_secret():
    """Verify diagnostics only reports existence and char count, never the key itself."""
    fake_key = "sk-ant-api03-abcdef1234567890"
    settings = Settings(anthropic_api_key=fake_key)
    diag = settings.get_api_key_diagnostics()

    assert diag["key_exists"] is True
    assert diag["char_count"] == len(fake_key)
    assert fake_key not in str(diag)


def test_settings_default_values():
    """Verify default configurations align with project requirements."""
    settings = Settings(anthropic_api_key="sk-ant-test-key-12345")
    assert settings.api_key == "sk-ant-test-key-12345"
    assert settings.ben_model == "claude-3-5-sonnet-20241022"
    assert settings.ben_host == "127.0.0.1"
    assert settings.ben_port == 8000
    assert "BEN" in settings.ben_system_instruction


def test_redact_sensitive():
    """Verify sensitive key string is redacted from error text."""
    secret = "sk-ant-api03-super_secret_key"
    error_msg = f"Failed to authenticate with key {secret} on server."
    cleaned = redact_sensitive(error_msg, secret)
    assert secret not in cleaned
    assert "[REDACTED_API_KEY]" in cleaned


def test_client_manager_unconfigured():
    """Verify client manager correctly reports unconfigured state when key is missing."""
    with patch.object(Settings, "api_key", new=property(lambda self: None)):
        mgr = AnthropicClientManager(api_key=None)
        assert mgr.is_configured is False
        assert mgr.key_char_count == 0
        result = mgr.test_connection()
        assert result.success is False
        assert "No valid Anthropic API key" in result.error


def test_client_manager_configured():
    """Verify client manager recognizes API key and creates client."""
    mgr = AnthropicClientManager(api_key="sk-ant-test-key-12345", model="claude-3-5-sonnet-20241022")
    assert mgr.is_configured is True
    assert mgr.key_char_count == len("sk-ant-test-key-12345")
    assert mgr.default_model == "claude-3-5-sonnet-20241022"
    client = mgr.client
    assert client is not None


def test_client_manager_successful_connection_mocked():
    """Verify test_connection returns proper success response when API succeeds."""
    mgr = AnthropicClientManager(api_key="sk-ant-test-key-12345", model="claude-3-5-sonnet-20241022")

    mock_client = MagicMock()
    mock_block = MagicMock()
    mock_block.text = "Hello! I am BEN, operational and ready."
    mock_response = MagicMock()
    mock_response.content = [mock_block]
    mock_client.messages.create.return_value = mock_response

    with patch.object(mgr, "_client", mock_client):
        result = mgr.test_connection(prompt="Status check")
        assert result.success is True
        assert result.model == "claude-3-5-sonnet-20241022"
        assert result.response == "Hello! I am BEN, operational and ready."
        assert result.latency_ms > 0
        assert result.char_count == len("sk-ant-test-key-12345")
        assert result.error is None


def test_client_manager_error_handling_mocked():
    """Verify test_connection handles API errors gracefully without crashing."""
    mgr = AnthropicClientManager(api_key="sk-ant-test-key-12345", model="claude-3-5-sonnet-20241022")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API rate limit exceeded")

    with patch.object(mgr, "_client", mock_client):
        result = mgr.test_connection(prompt="Status check")
        assert result.success is False
        assert "rate limit exceeded" in result.error


def test_backward_compatibility_alias():
    """Verify GeminiClientManager alias works seamlessly."""
    mgr = GeminiClientManager(api_key="test-key")
    assert isinstance(mgr, AnthropicClientManager)
