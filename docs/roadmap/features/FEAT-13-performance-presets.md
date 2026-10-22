# FEAT-13 — Performance presets and JVM flags

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Feature | P2 | S | — |

## Why
The owner picks RAM by guessing. Too little → crashes with `OutOfMemoryError`; too much on a phone
→ Android kills Termux or the whole system slows down. View and simulation distance matter more
for lag than almost anything else, and well-known JVM garbage-collection flags reduce lag spikes
for free.

## Current state
- `downloaders.launch_args` → `java -Xms<ram>M -Xmx<ram>M …` with no GC flags.
- RAM input: `min=512 step=256`, default 2048, no upper bound or advice.
- Host total memory is known (`/api/system/metrics` → `ramTotal`).

## Proposal
- **Preset picker** on Server setup: *Phone / low-power*, *Balanced*, *PC — many mods*. Each sets
  RAM, `view_distance`, `simulation_distance` (e.g. phone: 1.5–2 GB, 6/4; balanced: 3 GB, 10/8;
  modded PC: 6 GB, 10/8), all still editable afterwards.
- **RAM advice** next to the input: recommended range from host total RAM and loader (modded needs
  more), red warning above ~70% of host RAM (phones: ~50%, Android needs headroom).
- **Optimised JVM flags** switch (default on for new servers): G1GC with the widely used tuning set
  ("Aikar's flags": `-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200
  -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch …`), with the
  >12 GB variant chosen automatically. `-XX:+AlwaysPreTouch` off on phones (slow start, memory
  pressure).
- **Extra JVM args** text field for advanced users (validated: one line, no `-jar`/`@` tokens).
- Paper only: hint to pre-generate the world with a plugin (Chunky) to remove exploration lag.

## Implementation notes
- `core/downloaders.py::launch_args(loader, java_exe, sdir, ram_mb, extra_flags=())` — flags list
  built in a new `jvm_flags(ram_mb, is_termux)` helper.
- Config keys: `jvm_preset_flags` (bool), `jvm_extra_args` (str); FIX-05 validation.
- Tests: flags present/absent per switch; phone variant drops `AlwaysPreTouch`; extra args with
  `-jar` → 422.

## Acceptance criteria
- Choosing a preset on a 4 GB phone never suggests more than ~2 GB and the server starts.
