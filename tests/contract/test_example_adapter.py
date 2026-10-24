"""The documented example adapter must keep working with the unchanged core."""

import importlib.util
from pathlib import Path

from agentping.adapters.registry import AdapterRegistry
from agentping.core.policy import Action
from tests.conftest import Harness

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "example_adapter.py"


def load_example():
    spec = importlib.util.spec_from_file_location("example_adapter", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AcmeAgentAdapter


def test_example_adapter_plugs_into_core_without_changes():
    adapter_cls = load_example()
    harness = Harness()
    registry = AdapterRegistry()
    registry.register(adapter_cls())
    harness.router.registry = registry
    adapter = registry.get("acme-agent")
    harness.config.reminders.initial_delay_seconds = 30

    perm = adapter.translate(
        {"event": "approval.requested", "conversation": "c-42", "tool": "shell"}
    )
    result = harness.process(perm)
    assert result.decision.action is Action.NOTIFY
    assert harness.backend.shown[-1].title == "Acme Agent needs your permission"
    assert result.reminder_scheduled  # resolution signal declared -> escalation enabled

    resumed = harness.process(adapter.translate({"event": "user.replied", "conversation": "c-42"}))
    assert resumed.reminder_resolved
    assert adapter.translate({"event": "telemetry.ping"}) is None
