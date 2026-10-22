# Not doing (and why)

Ideas that come up for Minecraft panels but don't fit a single server run for friends. Revisit only
if the project's goal changes.

| Idea | Why not |
|---|---|
| Multiple servers running at once / proxy networks (Velocity, BungeeCord) | One group plays one world at a time. Worlds already switch via `level-name` (workflows.md §19), which covers "survival + creative" without a proxy. |
| Database (SQLite, Postgres) | JSON files in `EZ_Server_Data/` are enough for a handful of players and are easy to back up by copying a folder. Append-only JSONL covers logs (FEAT-12). |
| Docker / Kubernetes images | The main hosts are a Windows PC and an Android phone; neither benefits. The Termux and `run.bat` paths are the deployment story. |
| Billing, quotas, multi-tenant hosting | Not a hosting company. |
| Full RBAC with custom roles | Two roles (owner, friend) cover the use case — FEAT-10. |
| Localization layer | Project rule: English-only UI and code (CLAUDE.md, conventions.md). |
| Frontend framework / build step (React, bundlers) | Plain JS works on every device without a toolchain; the dashboard is small. Revisit only if `dashboard.js` becomes unmanageable even after splitting it into files. |
| Wake-on-join (a fake listener that boots the server when someone connects) | Tempting for idle shutdown (FEAT-06), but it means speaking the Minecraft handshake protocol and racing the real server for the port. Use an existing tool (e.g. lazymc) if ever needed; "Start" from the dashboard or a friend account (FEAT-10) is simpler. |
| Public internet exposure of the dashboard without a tunnel (port forwarding) | Tunnels already solve reachability with TLS; opening a router port adds risk with no gain. |
| Anti-cheat, economy, land-claim systems | That's plugin/mod territory — install them from the mod browser. |
