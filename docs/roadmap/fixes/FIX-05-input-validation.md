# FIX-05 — Validate player names, commands and config values

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Fix (correctness, security) | P1 | S | — |

## Why
Two kinds of bad input reach places they shouldn't:
1. **Newlines in text sent to the server's stdin.** `POST /api/admin/kick {"player": "x\nstop"}`
   writes two lines, so the server runs `kick x` *and* `stop`. Today only the owner can call it, and
   the owner can already type any console command, so impact is low — but FEAT-02 (whitelist) and
   FEAT-10 (friend accounts that may kick but not stop) make this a real privilege escalation.
2. **Config values with the wrong type.** `PUT /api/config {"ram_mb": "lots"}` is saved as-is;
   the next start crashes in `int(config.get("ram_mb", 2048))` with a 500. Ports outside 1–65535,
   `ram_mb` larger than the machine, or `max_players: -1` are accepted the same way.

## Current state
- `webapp/app.py::admin_action` builds `f"kick {player}"` from the raw payload.
- `POST /api/server/command` checks only for empty input.
- data-model.md: "No schema validation: any payload key is persisted."

## Proposal
- **Player names:** `^[A-Za-z0-9_]{3,16}$` (Java Edition names). Floodgate/Bedrock names
  (FEAT-18) start with a configurable prefix such as `.` — allow that prefix explicitly.
- **Console commands:** reject any `\r` or `\n`; cap length at 256 (the server's own limit).
- **Config:** a Pydantic model (FastAPI already ships Pydantic) `ConfigUpdate` with every key from
  `_default_config`, all optional, `extra="forbid"`, ranges:
  `ram_mb` 512–(host RAM), `server_port` 1024–65535, `max_players` 1–100, `view_distance` 2–32,
  `simulation_distance` 2–32, `gamemode`/`difficulty`/`level_type` as `Literal`s, `motd` ≤ 59 chars
  on one line. Errors come back as 422 with the field name, and the dashboard shows them on the
  matching input.

## Implementation notes
- `webapp/app.py`: `_PLAYER_NAME = re.compile(...)`, `_require_player(name)`; use it in
  `admin_action` and every future player endpoint.
- Keep reading old configs leniently: `_effective_config` coerces bad stored values back to
  defaults with a console warning, so an already-broken config doesn't crash start.
- `dashboard.js`: map 422 `detail[].loc` to `CONFIG_FIELDS` ids and mark the input invalid.
- Tests: newline player → 400; newline command → 400; `ram_mb: "lots"` → 422; unknown key → 422;
  stored garbage → start still works with defaults.

## Acceptance criteria
- Nothing a client sends can produce more than one line on the server's stdin.
- Every config field rejects out-of-range values with a message naming the field.
