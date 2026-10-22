# Roadmap — improvements, features and fixes

Proposals for the next iterations of EZ Minecraft Server Launcher. Written 2026-10-22 against the
code and `docs/knowledge/` as of that date.

## Scope: a server for a group of friends
Every item here is sized for **one owner, one server, 2–15 friends**, hosted on a home PC or an
Android phone. That means:
- no database, no containers, no clustering — files in `EZ_Server_Data/` stay the persistence layer;
- prefer the Python standard library; every new dependency must also install on Termux;
- the owner is not a sysadmin: features should work with defaults and explain themselves in the UI;
- friends are trusted but not admins — they should be able to join easily, not manage the host.

Things deliberately left out are listed in [not-doing.md](not-doing.md).

## Legend
- **Priority** — P0: fix before relying on the feature it affects · P1: high value for a friends
  server · P2: nice to have · P3: someday.
- **Effort** — S: under half a day · M: one to two days · L: several days.
- Every file follows the same layout: *Why*, *Current state*, *Proposal*, *Implementation notes*,
  *Acceptance criteria*, *Keep it small*.

## Fixes (correctness, security, robustness)
| ID | Title | Priority | Effort | Depends on |
|---|---|---|---|---|
| [FIX-06](fixes/FIX-06-status-endpoint-side-effects.md) | Make `GET /api/status` a pure read | P2 | S | — |

## Features
| ID | Title | Priority | Effort | Depends on |
|---|---|---|---|---|
| [FEAT-01](features/FEAT-01-scheduled-backups-and-retention.md) | Scheduled backups and retention | P1 | M | FIX-03 |
| [FEAT-02](features/FEAT-02-whitelist-and-ops-manager.md) | Whitelist and operators manager | P1 | M | FIX-05 |
| [FEAT-03](features/FEAT-03-friend-invite-and-status-page.md) | Friend invite card and public status page | P1 | M | — |
| [FEAT-04](features/FEAT-04-client-modpack-export.md) | Client modpack export (`.mrpack`) | P1 | M | — |
| [FEAT-05](features/FEAT-05-discord-webhook-notifications.md) | Discord webhook notifications | P1 | S | — |
| [FEAT-06](features/FEAT-06-scheduled-restarts-and-idle-shutdown.md) | Scheduled restarts and idle shutdown | P2 | M | FEAT-01 |
| [FEAT-07](features/FEAT-07-mod-update-checker.md) | Mod and plugin update checker | P2 | M | — |
| [FEAT-08](features/FEAT-08-crash-diagnosis-and-logs.md) | Crash diagnosis and log viewer | P2 | M | — |
| [FEAT-09](features/FEAT-09-version-upgrade-world-migration.md) | Version upgrade with world migration | P2 | M | FEAT-01, FEAT-07 |
| [FEAT-10](features/FEAT-10-friend-accounts-with-roles.md) | Friend accounts with limited permissions | P2 | M | FIX-01, FIX-05 |
| [FEAT-11](features/FEAT-11-live-console-and-chat.md) | Live console, command history and chat box | P2 | S | FIX-01 |
| [FEAT-12](features/FEAT-12-player-activity-and-playtime.md) | Player activity log and playtime | P2 | S | FIX-03 |
| [FEAT-13](features/FEAT-13-performance-presets.md) | Performance presets and JVM flags | P2 | S | — |
| [FEAT-14](features/FEAT-14-server-identity-and-properties-editor.md) | Server icon, MOTD preview, full properties editor | P2 | S | — |
| [FEAT-15](features/FEAT-15-disk-usage-and-cleanup.md) | Disk usage view and cleanup | P2 | S | — |
| [FEAT-16](features/FEAT-16-installable-mobile-app.md) | Installable phone app (PWA) | P3 | S | — |
| [FEAT-17](features/FEAT-17-stable-remote-address.md) | Stable remote dashboard address | P3 | M | FIX-01 |
| [FEAT-18](features/FEAT-18-bedrock-crossplay.md) | Bedrock crossplay (Geyser + Floodgate) | P3 | M | — |

## Code quality
| ID | Title | Priority | Effort | Depends on |
|---|---|---|---|---|
| [QUAL-01](quality/QUAL-01-split-app-module.md) | Split `webapp/app.py` into routers and page modules | P2 | M | — |
| [QUAL-02](quality/QUAL-02-test-suite-and-ci.md) | Test suite structure, `core/` unit tests and CI | P2 | M | — |

## Suggested order
1. **Make remote access safe and installs reliable** — done: FIX-01 (login sessions + lockout),
   FIX-02 (background installs), FIX-03 (atomic writes), FIX-04 (lifecycle lock).
2. **Friends can join without the owner** — FEAT-02, FEAT-03, FEAT-04, FEAT-05 (FIX-05 has shipped).
3. **Hands-off running** — FEAT-01, FEAT-06, FEAT-08, FEAT-07.
4. **Polish** — everything else, picking by what the group actually asks for.
QUAL-01 is easiest right before a batch of new pages (FEAT-02/03/10), so routes land in their new homes.

## Maintenance
When an item ships: move its key facts into `docs/knowledge/` (workflows, data-model,
verification), then delete the roadmap file and its row here — the knowledge base stays the source
of truth for what exists; this folder only holds what doesn't yet.
