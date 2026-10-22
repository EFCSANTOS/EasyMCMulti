# FEAT-18 — Bedrock crossplay (Geyser + Floodgate)

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P3 | M | FIX-05 (Bedrock name prefix) |

## Why
In most friend groups somebody plays on a console, tablet or phone (Bedrock Edition) and can't join
a Java server. Geyser translates the protocol so Bedrock players join the same world; Floodgate
lets them join without owning a Java account.

## Current state
- Mod browser installs Modrinth projects; Geyser and Floodgate are published on Modrinth for Paper
  and Fabric (and NeoForge for Geyser).
- PlayIt integration creates only a `minecraft-java` tunnel (`core/playit.py::ensure_tunnel`).
  The owner's PlayIt account allows 4 ports (workflows.md §14 field notes).

## Proposal
- Remote page → **"Let Bedrock friends join"** switch (Paper / Fabric / NeoForge only):
  1. Install Geyser + Floodgate for the loader (normal Modrinth install, with dependencies).
  2. Set Geyser's Bedrock port (default 19132, UDP) and `auth-type: floodgate` in its config after
     the first start generates it.
  3. Create a PlayIt **Bedrock (UDP) tunnel** for that port and show its address next to the Java
     one, plus on the invite card / status page (FEAT-03).
- Whitelist (FEAT-02): Floodgate players appear with a prefix (default `.`); the whitelist UI
  accepts "Bedrock gamertag" and adds the prefix.
- Vanilla/Forge: switch disabled with an explanation.

## Implementation notes
- `core/playit.py`: generalise `ensure_tunnel` to take the tunnel type (`minecraft-bedrock`) and
  local port; it already passes `tunnel_type` and matches by type — store both addresses in
  `playit_config.json` (`address`, `bedrock_address`).
- Geyser config is YAML; edit only the two keys with a line-based replace (no YAML dependency),
  after verifying the file exists.
- Verify on the owner's PlayIt account that the Bedrock tunnel type is accepted (see the PlayIt
  API surprises recorded in workflows.md §14) before building the UI.
- Tests: tunnel request carries the Bedrock type and port; config edit is idempotent.

## Acceptance criteria
- A friend on Minecraft for Android/console adds the shown address and port and joins the Java
  world.

## Keep it small
No Bedrock-native server option; no Geyser resource-pack conversion; no Bedrock skin support.
