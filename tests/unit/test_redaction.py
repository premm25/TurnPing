import pytest

from turnping.security.redaction import (
    describe_payload_shape,
    redact_mapping,
    redact_text,
    sanitize_message,
)


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-abcdefghijklmnop",
        "sk-proj-abcdefghijklmnopqrstuv",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "github_pat_11ABCDEFG0123456789abcdef",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyA-abcdefghijklmnopqrstuvwxyz12345",
        "xoxb-1234567890-abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
    ],
)
def test_known_secret_formats_redacted(secret):
    assert secret not in redact_text(f"value is {secret} ok")


def test_key_value_secrets_redacted():
    out = redact_text("password=hunter2 api_key: 'abc def' token \"xyz\"")
    assert "hunter2" not in out and "abc def" not in out and "xyz" not in out
    assert "password" in out  # the key name survives for context


def test_bearer_redacted():
    assert "abcdefgh123" not in redact_text("Authorization: Bearer abcdefgh123")


def test_private_key_block_redacted():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEvw\n-----END RSA PRIVATE KEY-----"
    assert "MIIEvw" not in redact_text(text)


def test_sanitize_message():
    assert sanitize_message(None) is None
    assert sanitize_message(123) is None
    assert sanitize_message("   ") is None
    assert sanitize_message("a\tb\nc") == "a b c"
    assert sanitize_message("x" * 500, limit=10).endswith("\u2026")


def test_redact_mapping_drops_sensitive_keys():
    out = redact_mapping(
        {"prompt": "secret plans", "tool_input": {"command": "x"}, "cwd": "C:/a", "n": 3}
    )
    assert out["prompt"] == "[OMITTED]"
    assert out["tool_input"] == "[OMITTED]"
    assert out["cwd"] == "C:/a"
    assert out["n"] == 3


def test_payload_shape_has_no_values():
    shape = describe_payload_shape({"prompt": "my secret", "n": 1})
    assert shape == {"prompt": "str", "n": "int"}
    assert "my secret" not in str(shape)
