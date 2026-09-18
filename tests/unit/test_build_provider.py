"""Unit tests for spark.reasoning.provider.build_provider.

Verifies construction alone is cheap and offline: it must not require a
network call, an API key, or even the optional SDK to be installed for
providers that aren't being constructed — and must map each
spark.config.PROVIDER_NAMES string to the right concrete class.
"""
from __future__ import annotations

import pytest

from spark.config import AppSettings, ProviderSettings
from spark.reasoning.provider import BaseLLMProvider, build_provider
from spark.reasoning.providers.anthropic import AnthropicProvider
from spark.reasoning.providers.gemini import GeminiProvider
from spark.reasoning.providers.ollama import OllamaProvider
from spark.reasoning.providers.openai import OpenAIProvider
from spark.reasoning.providers.stub import StubProvider


def test_build_provider_maps_every_configured_provider_name_to_the_right_class():
    settings = AppSettings()
    for name, cls in [
        ("gemini", GeminiProvider),
        ("anthropic", AnthropicProvider),
        ("openai", OpenAIProvider),
        ("ollama", OllamaProvider),
    ]:
        provider = build_provider(name, settings.provider_settings(name))
        assert isinstance(provider, cls)
        assert isinstance(provider, BaseLLMProvider)
        assert provider.model == settings.providers[name].model


def test_build_provider_constructs_stub_without_provider_settings_model():
    provider = build_provider("stub", ProviderSettings())
    assert isinstance(provider, StubProvider)


def test_build_provider_rejects_unknown_name():
    with pytest.raises(ValueError):
        build_provider("not-a-real-provider", ProviderSettings(model="x"))


def test_build_provider_passes_ollama_base_url_through():
    settings = ProviderSettings(model="llama3.2", base_url="http://example:1234")
    provider = build_provider("ollama", settings)
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://example:1234"


def test_build_provider_construction_needs_no_network_or_api_key(monkeypatch):
    """Construction must never touch the OS keyring or any SDK's client —
    only an actual `_raw_complete()` call should. Patches keyring itself
    (the single real implementation spark.secrets.get_api_key calls into)
    to blow up if construction ever reaches it eagerly.
    """
    import keyring

    def _boom(service, username):
        raise AssertionError(
            f"build_provider must not read the keyring for {username!r} at construction time"
        )

    monkeypatch.setattr(keyring, "get_password", _boom)

    for name in ("gemini", "anthropic", "openai"):
        provider = build_provider(name, ProviderSettings(model="some-model"))
        assert provider.model == "some-model"
        assert provider._client is None
