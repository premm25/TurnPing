# Configuration

Settings live in `%LOCALAPPDATA%\TurnPing\config.toml` (override the folder with
`TURNPING_HOME`). The Settings page writes this file. Manual edits are fine and are
picked up when TurnPing starts. Unknown keys are errors, so typos are reported. If the
file is invalid, TurnPing runs on defaults, shows the error in the dashboard and in
`turnping doctor`, and never overwrites it silently. Saving from Settings replaces it
after copying the broken file to `config.broken-<timestamp>.toml`.

All keys are optional. Defaults:

```toml
config_version = 1

[general]
enabled = true                  # master switch (events are still recorded when off)
notification_backend = "auto"   # "auto" | "toast" | "popup"
close_to_tray = true            # closing the window keeps the app running
launch_app_on_event = true      # hooks start the app if it isn't running
show_dashboard_on_start = true  # ignored for background starts (sign-in, hooks)
log_level = "INFO"              # DEBUG | INFO | WARNING | ERROR

[privacy]
include_message_preview = false # put a redacted ~160-char preview of the agent's last message in notifications
show_project_path = true        # show the project folder name in notifications

[history]
retention_days = 30             # 1..3650
max_events = 5000               # 100..1000000

[sound]
enabled = true                  # Windows sound scheme (SystemAsterisk / SystemExclamation)

[quiet_hours]
enabled = false
start = "22:00"                 # local time, HH:MM; may wrap past midnight
end = "07:00"
allow_urgent = true             # high/urgent alerts still show (silently) during quiet hours

[reminders]
enabled = true
initial_delay_seconds = 180     # >= 30
repeat_interval_seconds = 300   # >= 30
max_reminders = 2               # per alert; 0 disables
categories = ["PERMISSION_NEEDED", "INPUT_NEEDED"]
stop_on_dismiss = true          # dismissing (not just opening) an alert stops its reminders

[dedup]
window_seconds = 10             # identical keyless events within this window are retries

[events.RESPONSE_STOPPED]
notify = true
priority = "normal"             # low | normal | high | urgent
sound = true
# Defaults for the other categories:
#   TASK_COMPLETED    notify, normal, sound
#   INPUT_NEEDED      notify, high,   sound
#   PERMISSION_NEEDED notify, urgent, sound
#   TASK_FAILED       notify, high,   sound
#   SESSION_RESUMED / SESSION_ENDED / UNKNOWN   record only, low, no sound

[providers.claude-code]
enabled = true
notify_idle_prompt = false      # also alert on Claude's idle_prompt (~60 s after a response)

[providers.codex]
enabled = true

[providers.gemini-cli]
enabled = true
```

## Priorities

| Priority | Toast | Popup |
|---|---|---|
| low | normal duration | 6 s |
| normal | normal duration | 9 s |
| high | long duration (a reminder for it uses the `reminder` scenario) | 25 s |
| urgent | `reminder` scenario: stays until acted on | stays until dismissed |

Every toast has **Open session** and **Dismiss** buttons when it can be clicked through.

## Other runtime state

- **Pause** (tray, dashboard, `turnping pause`/`resume`) is stored in the database and
  survives restarts. While paused, events are recorded and reminders are cancelled.
- **Start with Windows** is the registry entry described in
  [INSTALLATION.md](INSTALLATION.md#start-with-windows). The checkbox reflects the
  registry, not the config file.

## Environment variables

| Variable | Purpose |
|---|---|
| `TURNPING_HOME` | Data folder (config, database, logs, IPC key). Each folder gets its own pipe, so test instances never collide. |
| `TURNPING_USER_HOME` | Home folder used to locate agent config files (testing). |
