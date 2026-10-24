"""Contract tests: documented provider payloads -> AgentPing events.

Fixtures in tests/fixtures/<provider>/ are copied from (or minimally adapted from) the
examples in each provider's official hook documentation, checked 2026-10-24.
"""

import json

import pytest

from agentping.adapters.base import AdapterError, AdapterOptions
from agentping.adapters.claude_code import ClaudeCodeAdapter
from agentping.adapters.codex import CodexAdapter
from agentping.adapters.gemini_cli import GeminiCliAdapter
from agentping.adapters.generic import GenericAdapter
from agentping.adapters.registry import BUILTIN_PROVIDER_IDS, AdapterRegistry
from agentping.contracts.events import EventCategory as C
from tests.conftest import FIXTURES, load_fixture

SENSITIVE_SNIPPETS = [
    "rm -rf",
    "hunter2",
    "sk-abcdef",
    "npm test",
    "secret token",
    "train the model",
    "git push",
    "fix the bug",
    "Training finished",
    "Fixed it",
    "I've completed",
]


def assert_no_leak(event):
    blob = json.dumps(event.to_wire())
    for snippet in SENSITIVE_SNIPPETS:
        assert snippet not in blob, f"leaked {snippet!r}"


# ----- Claude Code ---------------------------------------------------------------------

CLAUDE_CASES = {
    "stop": (C.RESPONSE_STOPPED, "Stop"),
    "stop_with_background": (C.RESPONSE_STOPPED, "Stop"),
    "stop_failure": (C.TASK_FAILED, "StopFailure"),
    "notification_permission": (C.PERMISSION_NEEDED, "Notification:permission_prompt"),
    "notification_idle": (C.INPUT_NEEDED, "Notification:idle_prompt"),
    "notification_elicitation": (C.INPUT_NEEDED, "Notification:elicitation_dialog"),
    "permission_request": (C.PERMISSION_NEEDED, "PermissionRequest"),
    "session_start": (C.SESSION_RESUMED, "SessionStart"),
    "session_end": (C.SESSION_ENDED, "SessionEnd"),
    "user_prompt_submit": (C.SESSION_RESUMED, "UserPromptSubmit"),
    "post_tool_use": (C.SESSION_RESUMED, "PostToolUse"),
}


@pytest.mark.parametrize("name, expected", CLAUDE_CASES.items())
def test_claude_code_fixtures(name, expected):
    event = ClaudeCodeAdapter().translate(load_fixture("claude_code", name))
    assert event is not None
    assert (event.category, event.native_type) == expected
    assert event.provider == "claude-code"
    assert event.session_id == "abc123"
    assert event.project_path == "C:/src/webapp"
    assert_no_leak(event)


def test_claude_stop_is_never_task_completed():
    event = ClaudeCodeAdapter().translate(load_fixture("claude_code", "stop"))
    assert event.category is C.RESPONSE_STOPPED


def test_claude_stop_preview_is_opt_in_and_redacted():
    payload = load_fixture("claude_code", "stop")
    off = ClaudeCodeAdapter().translate(payload)
    on = ClaudeCodeAdapter().translate(payload, AdapterOptions(include_message_preview=True))
    assert "refactoring" not in (off.message or "")
    assert "refactoring" in on.message
    assert "sk-abcdefghijklmnop" not in on.message


def test_claude_background_tasks_counted_not_copied():
    event = ClaudeCodeAdapter().translate(load_fixture("claude_code", "stop_with_background"))
    assert event.metadata["background_tasks"] == 1
    assert "tail -f" not in json.dumps(event.to_wire())


def test_claude_dedup_key_uses_prompt_id():
    a = ClaudeCodeAdapter().translate(load_fixture("claude_code", "stop"))
    b = ClaudeCodeAdapter().translate(load_fixture("claude_code", "stop"))
    assert a.dedup_key == b.dedup_key and a.event_id != b.event_id
    assert "p-001" in a.dedup_key


def test_claude_idle_has_no_dedup_key_without_prompt_id():
    assert (
        ClaudeCodeAdapter().translate(load_fixture("claude_code", "notification_idle")).dedup_key
        is None
    )


@pytest.mark.parametrize("name", ["notification_auth_success", "subagent_stop"])
def test_claude_irrelevant_events_ignored(name):
    assert ClaudeCodeAdapter().translate(load_fixture("claude_code", name)) is None


def test_claude_unknown_event_maps_to_unknown():
    event = ClaudeCodeAdapter().translate(
        {"hook_event_name": "BrandNewEvent2027", "session_id": "x"}
    )
    assert event.category is C.UNKNOWN


@pytest.mark.parametrize(
    "payload",
    [{}, {"hook_event_name": ""}, {"hook_event_name": 5}, {"session_id": "x"}, [], "Stop"],
)
def test_claude_malformed_payloads_raise_adapter_error(payload):
    with pytest.raises(AdapterError):
        ClaudeCodeAdapter().translate(payload)


def test_claude_missing_optional_fields_ok():
    event = ClaudeCodeAdapter().translate({"hook_event_name": "Stop"})
    assert event.category is C.RESPONSE_STOPPED
    assert event.session_id is None and event.session_key_inferred


def test_claude_wrong_types_for_optional_fields_ignored():
    event = ClaudeCodeAdapter().translate(
        {"hook_event_name": "Stop", "session_id": 42, "cwd": ["x"]}
    )
    assert event.session_id is None and event.project_path is None


def test_claude_hook_response_is_silent():
    r = ClaudeCodeAdapter().hook_response(load_fixture("claude_code", "permission_request"))
    assert r.stdout == "" and r.exit_code == 0


# ----- Codex -------------------------------------------------------------------------

CODEX_CASES = {
    "stop": (C.RESPONSE_STOPPED, "Stop", "thr_123"),
    "permission_request": (C.PERMISSION_NEEDED, "PermissionRequest", "thr_123"),
    "session_start": (C.SESSION_RESUMED, "SessionStart", "thr_123"),
    "session_end": (C.SESSION_ENDED, "SessionEnd", "thr_123"),
    "post_tool_use": (C.SESSION_RESUMED, "PostToolUse", "thr_123"),
    "legacy_notify": (C.RESPONSE_STOPPED, "notify:agent-turn-complete", "thr_456"),
}


@pytest.mark.parametrize("name, expected", CODEX_CASES.items())
def test_codex_fixtures(name, expected):
    event = CodexAdapter().translate(load_fixture("codex", name))
    assert (event.category, event.native_type, event.session_id) == expected
    assert event.provider == "codex"
    assert_no_leak(event)


def test_codex_stop_requires_json_stdout():
    assert CodexAdapter().hook_response(load_fixture("codex", "stop")).stdout == "{}"
    assert CodexAdapter().hook_response(load_fixture("codex", "permission_request")).stdout == ""


def test_codex_dedup_on_turn_id():
    a = CodexAdapter().translate(load_fixture("codex", "stop"))
    b = CodexAdapter().translate(
        load_fixture("codex", "legacy_notify") | {"thread-id": "thr_123", "turn-id": "turn_9"}
    )
    assert a.dedup_key == b.dedup_key  # hooks and legacy notify for the same turn collapse


def test_codex_malformed():
    with pytest.raises(AdapterError):
        CodexAdapter().translate({"type": "something-else"})


# ----- Gemini CLI --------------------------------------------------------------------

GEMINI_CASES = {
    "after_agent": (C.RESPONSE_STOPPED, "AfterAgent"),
    "notification_permission": (C.PERMISSION_NEEDED, "Notification"),
    "session_start": (C.SESSION_RESUMED, "SessionStart"),
    "session_end": (C.SESSION_ENDED, "SessionEnd"),
    "before_agent": (C.SESSION_RESUMED, "BeforeAgent"),
}


@pytest.mark.parametrize("name, expected", GEMINI_CASES.items())
def test_gemini_fixtures(name, expected):
    event = GeminiCliAdapter().translate(load_fixture("gemini_cli", name))
    assert (event.category, event.native_type) == expected
    assert event.session_id == "g-1"
    assert event.timestamp.tzinfo is not None
    assert_no_leak(event)


def test_gemini_permission_tool_name_kept_command_dropped():
    event = GeminiCliAdapter().translate(load_fixture("gemini_cli", "notification_permission"))
    assert event.metadata["tool_name"] == "run_shell_command"


def test_gemini_always_prints_json():
    for name in GEMINI_CASES:
        assert GeminiCliAdapter().hook_response(load_fixture("gemini_cli", name)).stdout == "{}"
    assert GeminiCliAdapter().hook_response(None).stdout == "{}"


def test_gemini_model_events_ignored():
    assert GeminiCliAdapter().translate(load_fixture("gemini_cli", "before_model")) is None


def test_gemini_bad_timestamp_rejected():
    with pytest.raises(AdapterError):
        GeminiCliAdapter().translate({"hook_event_name": "AfterAgent", "timestamp": "yesterday"})


def test_gemini_escalation_disabled_by_capability():
    assert not GeminiCliAdapter.capabilities.supports_escalation


# ----- Generic -----------------------------------------------------------------------


def generic():
    return GenericAdapter(reserved_provider_ids=BUILTIN_PROVIDER_IDS)


def test_generic_valid():
    event = generic().translate(load_fixture("generic", "valid"))
    assert event.provider == "my-build"
    assert event.category is C.TASK_COMPLETED
    assert event.event_id == "build42done"
    assert event.dedup_key == "my-build:build42done"


@pytest.mark.parametrize("name", ["bad_category", "reserved_provider", "unknown_field"])
def test_generic_rejections(name):
    with pytest.raises(AdapterError):
        generic().translate(load_fixture("generic", name))


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "x", "category": "TASK_COMPLETED", "schema_version": 2},
        {"provider": "Bad Name", "category": "TASK_COMPLETED"},
        {"provider": "x", "category": "TASK_COMPLETED", "metadata": "nope"},
        {"provider": "x", "category": "TASK_COMPLETED", "timestamp": "never"},
        {"category": "TASK_COMPLETED"},
    ],
)
def test_generic_invalid(payload):
    with pytest.raises(AdapterError):
        generic().translate(payload)


def test_generic_text_is_data_not_code():
    event = generic().translate(
        {
            "provider": "x",
            "category": "TASK_COMPLETED",
            "message": "$(rm -rf ~) `calc.exe` <script>",
        }
    )
    assert event.message == "$(rm -rf ~) `calc.exe` <script>"  # stored verbatim, never executed


def test_registry_rejects_duplicate_registration():
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        registry.register(ClaudeCodeAdapter())


def test_every_fixture_file_is_covered():
    covered = (
        {f"claude_code/{n}" for n in CLAUDE_CASES}
        | {f"codex/{n}" for n in CODEX_CASES}
        | {f"gemini_cli/{n}" for n in GEMINI_CASES}
        | {
            "claude_code/notification_auth_success",
            "claude_code/subagent_stop",
            "gemini_cli/before_model",
            "generic/valid",
            "generic/bad_category",
            "generic/reserved_provider",
            "generic/unknown_field",
        }
    )
    files = {f"{p.parent.name}/{p.stem}" for p in FIXTURES.rglob("*.json")}
    assert files == covered
