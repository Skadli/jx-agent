"""Shared outbound HTTP headers."""

from __future__ import annotations

CODEX_CLI_VERSION = "0.135.0"
CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_USER_AGENT = f"{CODEX_ORIGINATOR}/{CODEX_CLI_VERSION} (Windows 10.0.19045; x86_64) unknown"


def codex_user_agent() -> str:
    """Return the pinned Codex CLI-style User-Agent."""
    return CODEX_USER_AGENT


def codex_request_headers() -> dict[str, str]:
    """Return a fresh header dict for OpenAI-compatible SDK clients."""
    return {
        "originator": CODEX_ORIGINATOR,
        "User-Agent": CODEX_USER_AGENT,
    }
