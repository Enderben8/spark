"""Unit tests for spark.secrets (the keyring-backed API key store).

Mocks the `keyring` package itself so these tests don't depend on this
machine actually having a usable OS credential store (this sandbox does
not — see PROVIDERS_NOTES.md's "cross-cutting: spark/secrets.py" section:
keyring.get_keyring() resolves to the 'fail' backend here, which is exactly
the case get_api_key must handle gracefully).
"""
from __future__ import annotations

import keyring
import keyring.errors
import pytest

from spark import secrets


def test_get_api_key_returns_stored_value(monkeypatch):
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "secret-123" if username == "openai" else None
    )
    assert secrets.get_api_key("openai") == "secret-123"


def test_get_api_key_returns_none_when_not_set(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    assert secrets.get_api_key("gemini") is None


def test_get_api_key_returns_none_instead_of_raising_when_backend_unavailable(monkeypatch):
    def _raise(service, username):
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "get_password", _raise)
    # Must not raise — this is exactly what this sandbox's real keyring does.
    assert secrets.get_api_key("anthropic") is None


def test_set_api_key_calls_keyring_with_the_spark_service_name(monkeypatch):
    calls = []
    monkeypatch.setattr(keyring, "set_password", lambda service, username, password: calls.append(
        (service, username, password)
    ))
    secrets.set_api_key("openai", "sk-abc123")
    assert calls == [("spark", "openai", "sk-abc123")]


def test_set_api_key_propagates_backend_errors(monkeypatch):
    def _raise(service, username, password):
        raise keyring.errors.PasswordSetError("boom")

    monkeypatch.setattr(keyring, "set_password", _raise)
    with pytest.raises(keyring.errors.PasswordSetError):
        secrets.set_api_key("openai", "sk-abc123")
