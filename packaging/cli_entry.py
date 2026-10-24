"""PyInstaller entry for agentping-cli.exe (console; used by agent hooks)."""

from agentping.cli import main

raise SystemExit(main())
