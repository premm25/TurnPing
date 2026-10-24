"""Redaction helpers applied before anything is logged, stored or displayed.

The goal is defence in depth: adapters already avoid copying prompts or tool input,
but any free text that does pass through (e.g. a provider's notification message) is
scrubbed of likely secrets and truncated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MAX_MESSAGE_CHARS = 240

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),  # OpenAI/Anthropic-style keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google API key
    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),  # Slack
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
    # Bearer before the key/value rule, so "Authorization: Bearer <tok>" loses the token.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)\b"
        r"(\s*[:=]\s*|\s+)(\"[^\"]*\"|'[^']*'|\S+)"
    ),
]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

SENSITIVE_KEYS = frozenset(
    {
        "prompt",
        "prompt_response",
        "last_assistant_message",
        "tool_input",
        "tool_response",
        "transcript_path",
        "input-messages",
        "last-assistant-message",
        "env",
        "environment",
        "headers",
        "authorization",
        "token",
        "api_key",
        "password",
        "secret",
    }
)


def redact_text(text: str) -> str:
    out = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        else:
            out = pattern.sub("[REDACTED]", out)
    return out


def sanitize_message(text: object, limit: int = MAX_MESSAGE_CHARS) -> str | None:
    """Return a single-line, redacted, length-limited string or None."""
    if not isinstance(text, str):
        return None
    cleaned = _CONTROL_CHARS.sub("", text)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None
    cleaned = redact_text(cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def describe_payload_shape(payload: Mapping[str, Any]) -> dict[str, str]:
    """Summarise a raw payload for diagnostics without copying its values.

    Produces ``{key: type_name}`` so adapter failures can be debugged from logs without
    ever logging prompts, tool input or transcripts.
    """
    return {str(k)[:64]: type(v).__name__ for k, v in list(payload.items())[:50]}


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Drop sensitive keys and redact string values (shallow)."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if str(key).lower() in SENSITIVE_KEYS:
            out[key] = "[OMITTED]"
        elif isinstance(value, str):
            out[key] = sanitize_message(value, limit=200)
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = f"<{type(value).__name__}>"
    return out
