import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no real desktop needed

import pytest

from agentping.core.engine import Engine
from agentping.runtime import build_runtime
from tests.conftest import FakeBackend


@pytest.fixture
def stack(qtbot):
    """A real runtime + engine + controller with the OS toast replaced by a fake."""
    from agentping.desktop.controller import Controller
    from agentping.desktop.popup import PopupBackend, PopupBridge, PopupManager

    runtime = build_runtime(toast=False)
    toast = FakeBackend("toast")
    runtime.notifier.add_backend("toast", toast)
    bridge = PopupBridge()
    runtime.notifier.add_backend("popup", PopupBackend(bridge))
    popups = PopupManager(bridge)
    engine = Engine(runtime.router, tick_seconds=0.05)
    engine.start()
    controller = Controller(runtime, engine)
    yield runtime, engine, controller, toast, popups
    popups.close_all()
    engine.stop()
    runtime.close()
