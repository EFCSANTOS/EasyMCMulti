# EZ Minecraft Server Launcher — Webapp Migration Plan (v2)

## 1. Objective

Transform the existing **EZ Minecraft Server Launcher** into an English-language web application that lets authorized users manage the Minecraft server from any device on the same local network (LAN).

The web application should replace the Tkinter interface while reusing the repository's existing `core/` functionality wherever practical. It must preserve playit.gg support and provide a safe foundation for multi-client management.

Repository: https://github.com/MatheoZimmer/ez-minecraft-serveur

> **Implementation rule:** Inspect the actual repository before connecting routes to core functions. The names and signatures in this plan are targets, not guarantees. Do not assume example methods such as `download_server()`, `backup_world()`, `current_address()`, or `ServerProcess.is_alive()` exist until verified.

## 2. Scope and requirements

### Features

- Serve the management interface over HTTP from the host machine.
- Allow authorized devices on the same LAN to access it.
- Show server state, player count, loader/version, LAN address, public address, and playit.gg tunnel address when available.
- Create/configure, start, stop, and restart a server using existing core functionality.
- Stream console output to multiple browser clients in real time.
- Send console commands from the browser.
- Manage supported server settings, versions, mods, and plugins.
- Create and download world backups.
- Start and monitor the playit.gg agent on the host.
- Translate maintained UI text, errors, scripts, and documentation to English; remove the French README section.
- Preserve the desktop launcher if feasible, or document clearly if the webapp becomes the primary entry point.

### Non-functional requirements

- Prevent accidental duplicate Minecraft or playit processes.
- Do not block the asynchronous web server during downloads, installations, backups, or other long-running work.
- Authenticate all management endpoints and WebSocket connections.
- Validate inputs and restrict file access to application-owned directories.
- Handle crashes, disconnects, and unavailable external services gracefully.
- Prioritize Windows support; consider Docker only after the Windows implementation works.

## 3. Proposed project structure

```text
ez-minecraft-serveur/
├── launcher.py                 # Existing desktop entry point, if retained
├── core/                       # Existing business logic
├── webapp/
│   ├── __init__.py
│   ├── app.py                  # FastAPI app and lifecycle hooks
│   ├── state.py                # Shared process/configuration state
│   ├── routes.py               # UI, REST API and WebSocket routes
│   ├── auth.py                 # Authentication and authorization
│   ├── schemas.py              # Pydantic request/response models
│   ├── services.py             # Adapters around existing core modules
│   └── websocket_manager.py    # Multi-client console broadcasting
├── templates/
│   └── index.html
├── static/
│   ├── app.js
│   └── style.css
├── tests/
│   ├── test_auth.py
│   ├── test_api.py
│   ├── test_server_lifecycle.py
│   └── test_playit.py
├── requirements.txt
├── .env.example
└── README.md
```

### Architecture principles

1. **Reuse the core.** `webapp/services.py` adapts existing functionality instead of duplicating downloader, Java, configuration, Modrinth, or playit logic.
2. **Keep routes thin.** Validate requests, invoke a service, and return structured responses.
3. **Use one process owner.** A single state/service instance coordinates server lifecycle operations.
4. **Move blocking work off the event loop.** Use worker threads or background tasks as appropriate.
5. **Keep protocol values intact.** Translate interface text, not Minecraft property keys or required enum values.

## 4. Dependencies

Inspect existing dependency management first. Add compatible versions of these packages if not already present:

```text
fastapi
uvicorn[standard]
jinja2
python-multipart
itsdangerous
```

Pin versions after compatibility testing and keep a reproducible dependency setup. `tkinter` is part of Python's standard library on typical Windows installations; it does not need to be uninstalled, just excluded from the webapp startup path.

## 5. Repository audit before coding

Inspect the real APIs and behavior of at least:

- `core/server_process.py`
- `core/playit.py`
- `core/config_store.py`
- `core/downloaders.py`
- `core/java_manager.py`
- `core/modrinth.py`
- `core/netinfo.py`
- `core/sysinfo.py`
- `launcher.py`

Record each existing feature, its actual function signatures, how it reports errors, whether it blocks, and whether it starts subprocesses.

**Deliverable:** a mapping from current desktop actions to core methods and proposed web service adapters. Do not implement routes against guessed method names.

## 6. Shared state and lifecycle

Implement `webapp/state.py` as the single owner of runtime state. It should track:

- The active server process adapter, if any.
- The active profile, loader, and Minecraft version.
- Whether creation, startup, shutdown, installation, or backup is in progress.
- A bounded console history buffer.
- playit.gg agent state and last known tunnel details, if available.
- Locks that prevent conflicting operations.

Use an `asyncio.Lock` for operations coordinated in one event loop. If the core is called from threads or another entry point, add suitable thread-safe coordination too. A plain `threading.Lock` does not automatically make async operations safe.

### Lifecycle behavior

- **Create:** reject conflicting operations; validate loader, version, and RAM before writing files.
- **Start:** reject duplicate starts and verify that the configured server files exist.
- **Stop:** request graceful shutdown first; escalate only after a documented timeout.
- **Restart:** wait for the old process to exit before starting another.
- **Crash:** record exit status and update state; apply auto-restart only if supported/configured.
- **Application shutdown:** define explicitly whether the Minecraft server should continue running or be stopped. Never terminate it accidentally.

Inspect the existing process API, callbacks, output reader, and working-directory assumptions before adapting it.

## 7. Console streaming and WebSockets

The console must work for several browser clients at the same time.

### Server-to-browser output

If the core already provides an output callback, adapt it. Otherwise, add a small callback/queue around the existing subprocess output reader without breaking desktop logging.

Recommended approach:

- `WebSocketManager` tracks connected clients.
- Each client has an `asyncio.Queue`.
- If a callback runs in another thread, schedule queue writes onto the event loop with `loop.call_soon_threadsafe(...)`.
- A dedicated coroutine sends queued messages to each client.
- Remove clients and cancel sender tasks on disconnect.
- Bound each queue so slow clients cannot consume unlimited memory; drop old lines or disconnect a client that falls too far behind.

Keep a bounded server-side history (for example, 2,000 lines) and send a limited recent history (for example, 500 lines) to new clients.

**Important:** Do not call asynchronous WebSocket methods directly from a subprocess reader thread. Starlette WebSockets do not provide a general synchronous `send_nowait()` method.

### Commands

Prefer an authenticated REST endpoint (`POST /api/server/command`) for console commands and use the WebSocket for output only. If commands are accepted over WebSocket, use concurrent send/receive tasks and validate message size and authorization.

In the frontend, render console output with `textContent`, never `innerHTML`.

## 8. Proposed API

Use Pydantic models for JSON bodies where appropriate. Return consistent error responses and never expose stack traces, credentials, environment variables, or sensitive filesystem paths.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Serve the web UI |
| `GET` | `/health` | Minimal health check |
| `GET` | `/api/status` | Server state, player count, addresses, current operation |
| `GET` | `/api/versions/{loader}` | List supported versions |
| `GET` | `/api/config` | Read active configuration |
| `PUT` | `/api/config` | Validate and save configuration |
| `POST` | `/api/server/create` | Prepare a server |
| `POST` | `/api/server/start` | Start the configured server |
| `POST` | `/api/server/stop` | Gracefully stop the server |
| `POST` | `/api/server/restart` | Restart the server |
| `POST` | `/api/server/command` | Send a console command |
| `POST` | `/api/server/backup` | Create a world backup |
| `GET` | `/api/server/backups` | List available backups |
| `GET` | `/api/server/backups/{backup_id}` | Download an approved backup |
| `GET` | `/api/mods/search` | Search Modrinth with compatibility filters |
| `POST` | `/api/mods/install` | Install a validated project/version |
| `DELETE` | `/api/mods/{mod_id}` | Remove a validated mod/plugin |
| `POST` | `/api/playit/start` | Start or attach to the host's playit agent |
| `POST` | `/api/playit/stop` | Stop the agent if supported safely |
| `GET` | `/api/playit/status` | Return agent state and tunnel details |
| `WS` | `/ws/console` | Stream console output |

This is a target contract, not a claim that every underlying operation already exists. Use the actual core API; return a clear `501 Not Implemented` for optional features that are not yet supported.

### Input validation

- Allow only known loader names supported by the repository.
- Validate versions against lists returned by the core/download source.
- Enforce a configurable RAM limit while reserving memory for Windows and other applications.
- Validate player names, numeric settings, project IDs, backup IDs, and file names.
- Never accept arbitrary filesystem paths from the browser.
- Never interpolate user input into shell command strings; use subprocess argument arrays.
- Require confirmation for destructive operations such as deleting worlds or removing mods.

## 9. Authentication and security

The panel can start/stop the server, execute console commands, install code, and access backups. Authentication is mandatory even on a home LAN.

### Recommended baseline

- Read the admin username and strong password from environment variables or a local configuration file excluded from version control.
- Fail startup with a clear message if required credentials are missing; do not silently change credentials every launch.
- Use a reviewed session-cookie flow (for example, `itsdangerous`) or another established authentication approach.
- Set cookies `HttpOnly` and `SameSite`; set `Secure` when HTTPS is enabled.
- Protect state-changing requests against CSRF when cookie authentication is used.
- Rate-limit login attempts where practical.
- Protect both HTTP routes and WebSocket handshakes.
- Validate WebSocket `Origin` against an allowlist.
- Do not put credentials in query strings; URLs may be stored in browser history and logs.
- Keep `/health` minimal and do not disclose server details to unauthenticated users.
- Disable interactive API documentation in production unless explicitly required and protected.

**Basic Auth is not encryption.** If Basic Auth is used, use HTTPS. On a trusted LAN used for initial testing, restrict access at the firewall and understand that plain HTTP does not protect credentials from network observation.

Do not expose the management UI directly to the public internet. For remote management, use a VPN or a properly configured HTTPS reverse proxy with strong authentication. The Minecraft playit.gg tunnel and the web management panel are separate services and must have separate access policies.

## 10. LAN access and Windows firewall

Bind the app to the host's network interfaces:

```python
uvicorn.run("webapp.app:app", host="0.0.0.0", port=8080)
```

Authorized users can visit:

```text
http://<HOST-LAN-IP>:8080
```

Replace `<HOST-LAN-IP>` with the host computer's actual LAN IPv4 address.

On Windows:

1. Allow TCP port `8080` through Windows Defender Firewall on the **Private** profile only.
2. Do not add a Public-profile rule without a specific, reviewed need.
3. Consider a DHCP reservation so the host's LAN address remains stable.
4. Test from a second device on the same LAN.
5. Do not configure router port forwarding for the management port.

Provide configurable bind address and port. `127.0.0.1` should remain available for local-only use.

## 11. playit.gg integration

The migration must preserve playit.gg support through the existing `core/playit.py` implementation where possible.

### Expected behavior

- The playit.gg agent runs on the **host machine**, alongside the Minecraft server.
- The browser sends an authenticated request to the webapp.
- The host invokes the existing playit integration.
- The browser displays agent status, authorization URL (when required), and tunnel address when available.
- The browser never runs the agent itself.
- The playit.gg tunnel forwards Minecraft traffic; it does not automatically publish the management UI.

### Authorization flow

First inspect `core/playit.py` to determine how it starts the agent, detects authorization URLs, waits for approval, and obtains the tunnel address.

If authorization requires a browser:

1. Capture the actual authorization URL emitted by the agent, if available.
2. Return it to the authenticated UI as structured data.
3. Display a clearly labelled **Open playit.gg authorization** link.
4. Open the link only after an explicit user click.
5. Keep the agent running on the host while authorization completes.
6. Refresh the agent status and display the assigned tunnel address when available.

Do not automatically open a browser on the host in response to an HTTP request; the host may be headless or running as a service. Do not assume that methods such as `current_address()` exist until verified.

### Reliability and security

- Treat authorization URLs as sensitive until their behavior is verified; avoid logging them publicly.
- Do not put credentials or tokens in URLs unless the provider's documented flow requires it.
- Handle missing agent binaries, startup failures, disconnections, and missing tunnel addresses.
- Prevent duplicate agent processes when users click Start repeatedly.
- Make start/stop operations idempotent where practical.
- Display Minecraft server state, playit agent state, and public tunnel address as separate fields.
- Test that restarting Minecraft does not create duplicate tunnels.
- Document that tunnel availability depends on the agent, provider service, account, and tunnel configuration.

## 12. Frontend requirements

Build a responsive, English-language interface.

### Dashboard

- Server state: stopped, starting, running, stopping, crashed.
- Current loader and Minecraft version.
- Online player count, when available.
- RAM allocation and host memory information, when supported.
- LAN connection address.
- playit.gg agent state and public tunnel address.
- Current operation/progress, such as downloading, installing, backing up, or starting.

### Server controls

- Select loader and version from server-provided lists.
- Configure RAM.
- Create/prepare, start, stop, and restart.
- Download a world backup.
- Show clear success/error notifications.
- Disable conflicting actions while operations are in progress.

### Configuration

- Expose settings actually supported by the core and Minecraft server.
- Load saved values on page load.
- Validate inputs before submission.
- Distinguish saved settings from settings requiring a restart.
- Preserve Minecraft property keys and value formats.

### Console

- Real-time output through WebSocket.
- Bounded client-side history.
- Auto-scroll unless the user has scrolled away from the bottom.
- Command input with Enter-to-send.
- Clear connection/disconnection indicator.
- Safe text rendering; never treat server output as HTML.

### Mods and plugins

- Search Modrinth with loader/version compatibility filters.
- Display project name, summary, and compatible versions based on API results.
- Confirm before installing or removing files.
- Explain when a restart is required.
- Restrict file operations to the correct server directory and supported loader.

### Admin

Implement player management and common actions only when supported by the core/server type: whitelist, kick, ban, op/de-op, game mode, time, weather, save actions, and announcements. Require confirmation for high-impact actions. Do not show a success state until the underlying operation reports success.

### Accessibility and maintainability

- Support desktop, tablet, and mobile layouts.
- Use accessible labels, keyboard navigation, visible focus states, and readable console text.
- Keep secrets out of HTML and JavaScript.
- Keep frontend dependencies minimal unless a framework offers a clear benefit.

## 13. English-only cleanup

Translate maintained user-facing text and documentation. Do not rewrite Git history merely to translate commit messages.

Review:

- `README.md`: keep English documentation and remove the French-language section.
- `launcher.py`: translate visible labels, dialogs, errors, and help text if the desktop UI remains.
- `core/`: translate user-facing messages and maintained comments where useful.
- `CHANGELOG.md`: translate maintained release notes if desired; historical commit messages remain unchanged.
- `build.bat`, `run.bat`, and other scripts: translate printed messages.
- HTML templates, JavaScript, accessibility labels, and API errors.
- Tests and fixtures that assert UI strings.

Example PowerShell scan:

```powershell
Get-ChildItem -Recurse -File -Include *.py,*.md,*.bat,*.html,*.js |
  Select-String -Pattern 'Démarrer|Arrêter|Sauvegarde|joueur|monde|réseau|Version française|Fonctionnalités|Générer|Matériel cible'
```

Review matches manually instead of translating every accented character indiscriminately. Preserve third-party license notices and external proper names where necessary.

## 14. Testing strategy

### Unit tests

- Authentication success/failure.
- Rejection of invalid loaders, versions, RAM values, paths, and backup IDs.
- Lifecycle locks prevent conflicting operations.
- Console history and per-client queues remain bounded.
- Multiple WebSocket clients receive output safely.
- Disconnecting a client cleans up its queue and tasks.
- Long-running work does not block status requests.
- Errors do not expose credentials, stack traces, or sensitive paths.

### Integration tests

- Prepare and start a test server using a supported loader/version.
- Stop gracefully and verify process exit.
- Test restart and crash recovery where supported.
- Create and download a backup; verify the archive.
- Search/install/remove a test mod in a disposable server directory.
- Start the playit agent, complete authorization, and verify status when provider setup permits.
- Verify repeated playit start requests do not create duplicate agents.
- Confirm the web management port is not exposed through the Minecraft tunnel.

### LAN acceptance test

1. Start the webapp on the host.
2. Open the UI from a second computer or phone on the same LAN.
3. Verify login is required.
4. Start and stop the Minecraft server from the second device.
5. Open two browser sessions and verify both receive console output.
6. Verify unauthenticated HTTP and WebSocket access is rejected.
7. Verify the firewall rule is restricted to the intended network profile.
8. Verify playit.gg exposes only the configured Minecraft tunnel.

## 15. Packaging and deployment

### Windows

- Add a documented command to start the webapp.
- Ensure templates and static files are included in the packaged build.
- Verify subprocess working directories and paths in PyInstaller builds.
- Write runtime data to a known writable data directory.
- Do not assume PyInstaller `--onefile` automatically handles writable resources correctly.
- Keep credentials outside the executable and repository.
- Provide `.env.example` with placeholders only and exclude real `.env` files from version control.

### Optional Docker support

Add Docker only after the Windows webapp works. Document persistent storage for:

- Server installations and worlds.
- Profiles/configuration.
- Backups.
- playit agent state, if supported.

Keep ownership of server files explicit. Avoid running as root in Linux containers unless required and justified. Verify that the playit agent supports the container's architecture and runtime.

## 16. Implementation milestones

### Milestone 1 — Audit the repository

- Inspect the actual core APIs and process behavior.
- Map existing desktop features to core methods and web adapters.
- Identify operations that block or need callbacks.
- Identify unsupported features before designing routes.

**Deliverable:** a verified core-to-web feature map.

### Milestone 2 — Secure minimal web service

- Add FastAPI and lifecycle setup.
- Add authentication and `/health`.
- Implement `/api/status`.
- Add safe start/stop adapters around the real process API.
- Verify LAN access and firewall instructions.

**Deliverable:** an authenticated service that reports state and controls a test server.

### Milestone 3 — Dashboard and console

- Implement bounded history and WebSocket broadcasting.
- Build the responsive dashboard and server controls.
- Test simultaneous clients, slow consumers, and disconnects.

**Deliverable:** a real-time web console.

### Milestone 4 — Configuration and backups

- Add validated configuration endpoints.
- Implement backup creation/list/download.
- Move blocking work off the event loop.
- Add lifecycle conflict handling and progress reporting.

**Deliverable:** configuration and backup management through the browser.

### Milestone 5 — Mods and playit.gg

- Connect Modrinth operations through the existing core.
- Implement playit start/status/authorization using the actual code.
- Display the authorization link without opening a browser on the host automatically.
- Test duplicate-start prevention and tunnel persistence.

**Deliverable:** mod management and playit.gg integration in the web UI.

### Milestone 6 — English cleanup and packaging

- Translate maintained user-facing strings and documentation.
- Remove the French README section.
- Package the application and verify static/template resources.
- Run unit, integration, and LAN acceptance tests.

**Deliverable:** documented English-language webapp build.

## 17. Definition of done

- [ ] An authorized LAN device can access the web UI.
- [ ] Unauthenticated clients cannot use management endpoints or the console WebSocket.
- [ ] The UI reports accurate server state and uses only verified core APIs.
- [ ] Lifecycle operations cannot accidentally create duplicate processes.
- [ ] Console output works for multiple browser clients.
- [ ] Configuration and backups work against real server files.
- [ ] Mod operations validate compatibility and file locations.
- [ ] playit.gg runs on the host and its authorization link/status are displayed correctly.
- [ ] The management panel is not unintentionally exposed through playit.gg or router port forwarding.
- [ ] Maintained UI and documentation are in English and the French README section is removed.
- [ ] Windows packaging and LAN setup instructions are tested.
- [ ] Tests cover authentication, lifecycle conflicts, WebSockets, and playit behavior.

## 18. Critical implementation warnings

Do not copy the earlier illustrative code without validating it against the repository:

- Starlette WebSockets do not provide a general synchronous `send_nowait()` API.
- A subprocess reader thread must not call async WebSocket methods directly.
- WebSocket authentication must be handled explicitly; do not assume HTTP Basic Auth middleware protects WebSockets.
- Never put credentials in WebSocket query parameters.
- `async` route handlers still block if they call synchronous downloads or process operations directly.
- Verify all method names and signatures against the source before use.
- Keep the management UI and Minecraft playit.gg tunnel as separate network surfaces.

Build a small, secure vertical slice first, then add features incrementally while preserving existing core behavior.
