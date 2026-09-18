"""OS-keyring-backed storage for provider API keys.

BUILD_SPEC.md §5 / §12.3 and ``config.py``'s own module docstring are both
explicit that API keys must never touch a plaintext config file — "API keys
go in Windows Credential Manager, never in a plaintext config file". This
module is the concrete helper that makes that true: it stores/retrieves
secrets through the third-party ``keyring`` package (already a hard
dependency of this project — see ``pyproject.toml``'s ``keyring>=25.2``),
which abstracts the platform credential store: Windows Credential Manager on
the shipped platform, macOS Keychain, or a Secret Service / KWallet backend
on Linux dev machines.

Everything is stored under one keyring *service* name, ``"spark"``, keyed by
*username* = the provider name (``"gemini"``, ``"anthropic"``, ``"openai"``,
...) — i.e. one "password" entry per provider, matching how Settings/§12.3
think about credentials (one key per provider, not per model or per run).

**Verification note**: confirmed directly against the ``keyring`` package
actually installed in this repo's venv (25.x series) — ``keyring.get_password``
/ ``keyring.set_password`` take ``(service_name, username, password=...)`` and
have had that exact signature since very early keyring releases; this is not
a new-generation-SDK situation like the four LLM providers next to this
module, so there was no "which API is current" question to research here.

**Important, sandbox-specific finding** (worth a human's attention on a real
dev machine too): in *this* sandbox there is no OS keyring backend at all —
``keyring.get_keyring()`` resolves to ``keyring.backends.fail.Keyring``, and
calling ``get_password`` raises ``keyring.errors.NoKeyringError`` rather than
returning ``None``. A brand-new Linux machine with no Secret Service/KWallet
running, or a CI box, can hit the exact same thing. :func:`get_api_key`
treats that as "no key configured" (returns ``None``, logs a warning) rather
than letting it crash the caller — the whole point of centralizing this is
that provider modules shouldn't each need to know keyring can fail this way.
"""
from __future__ import annotations

from spark.logsetup import get_logger

log = get_logger("secrets")

_SERVICE_NAME = "spark"


def get_api_key(provider_name: str) -> str | None:
    """Return the stored API key for ``provider_name``, or ``None``.

    Never raises: a missing/misconfigured OS keyring backend, or no entry
    having been set yet, both come back as ``None`` so callers can treat
    "not installed" and "not configured" as the same "no key available"
    case and raise their own clear ``ProviderUnavailableError``.
    """
    try:
        import keyring
    except ImportError:
        log.warning("the 'keyring' package is not installed; cannot read stored API keys")
        return None
    try:
        return keyring.get_password(_SERVICE_NAME, provider_name)
    except Exception as exc:  # a genuinely unavailable backend, not "key not found"
        log.warning(
            "keyring backend unavailable while reading the %s API key (%s); "
            "treating as no key configured",
            provider_name,
            exc,
        )
        return None


def set_api_key(provider_name: str, key: str) -> None:
    """Store ``key`` for ``provider_name`` in the OS credential store.

    Unlike :func:`get_api_key`, this does not swallow errors — it is called
    from an explicit user action (e.g. Settings' "Save API key" button), and
    silently failing to save a key the user just typed in would be worse
    than surfacing the error.
    """
    import keyring

    keyring.set_password(_SERVICE_NAME, provider_name, key)
