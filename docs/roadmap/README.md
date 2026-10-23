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
| — | All fixes have shipped (FIX-01 … FIX-06). | | | |

## Features
| ID | Title | Priority | Effort | Depends on |
|---|---|---|---|---|
| — | All features have shipped (FEAT-01 … FEAT-18). | | | |

## Code quality
| ID | Title | Priority | Effort | Depends on |
|---|---|---|---|---|
| — | All code-quality items have shipped (QUAL-01, QUAL-02). | | | |

## Suggested order
1. **Make remote access safe and installs reliable** — done: FIX-01 (login sessions + lockout),
   FIX-02 (background installs), FIX-03 (atomic writes), FIX-04 (lifecycle lock).
2. **Friends can join without the owner** — done: FIX-05, FEAT-02, FEAT-03, FEAT-04, FEAT-05
   (FEAT-04's optional "recommended client mods" list was left for later).
3. **Hands-off running** — done: FEAT-01, FEAT-06, FEAT-08, FEAT-07.
4. **Polish** — done: FIX-06, FEAT-09 … FEAT-18, QUAL-01, QUAL-02 (2026-10-23).

Open follow-ups noted while shipping (not roadmap items yet): FEAT-04's optional "recommended
client mods" list; the real-world checks listed as "not yet" in `docs/knowledge/verification.md`
(Prism import of the mod pack, a real Discord webhook, a real Cloudflare named tunnel, the PlayIt
Bedrock tunnel type on a real account, PWA install on a phone).

## Maintenance
When an item ships: move its key facts into `docs/knowledge/` (workflows, data-model,
verification), then delete the roadmap file and its row here — the knowledge base stays the source
of truth for what exists; this folder only holds what doesn't yet.
