"""Concrete :class:`~spark.reasoning.provider.BaseLLMProvider` implementations.

Deliberately does not import the four real providers eagerly — each has an
optional SDK dependency (``google-genai``, ``anthropic``, ``openai``; the
``ollama`` provider only needs ``httpx``, already a hard dependency) that may
not be installed, and importing it here would make the whole package
uninstallable/unimportable without every extra. Use
``spark.reasoning.provider.build_provider(name, settings)`` to construct one
by name, or import a specific module (e.g.
``from spark.reasoning.providers.gemini import GeminiProvider``) directly.
"""
from __future__ import annotations
