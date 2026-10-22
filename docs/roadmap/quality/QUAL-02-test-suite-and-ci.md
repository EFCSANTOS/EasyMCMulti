# QUAL-02 — Test suite structure, `core/` unit tests and CI

| Type | Priority | Effort | Depends on |
|---|---|---|---|
| Code quality | P2 | M | — (easier after QUAL-01) |

## Why
The app now runs unattended 24/7 and updates itself from git (`auto_update_install` exists). A
broken commit on the tracked branch can take a friends server down without anyone touching it.
Tests that run automatically on every push are the cheapest protection.

## Current state (testing.md)
- One file, `tests/test_webapp_smoke.py` (~57 KB, 76 tests), one shared module-level `client`.
- Order-dependent tests (`test_server_lifecycle_start_and_stop` relies on the previous test's
  `DummyProcess`; run alone it would download and start a real server).
- Hidden network calls (`api.ipify.org`, Mojang manifest).
- No `conftest.py`, `pytest.ini`, CI workflow, or `core/` unit tests.

## Proposal
1. **Structure:** `tests/conftest.py` with the shared fixtures (`isolated_data_dir`, `fake_*`,
   a fresh `app`/`client` per test), then split by area: `test_auth.py`, `test_lifecycle.py`,
   `test_backups.py`, `test_worlds.py`, `test_mods.py`, `test_remote.py`, `test_alerts.py`,
   `test_updater.py`, `test_pages.py`.
2. **Hermetic by default:** an autouse fixture that blocks real network (`requests` adapters /
   `socket.connect` patched) unless a test is marked `@pytest.mark.network`; stub `public_ip`.
3. **No order dependence:** each lifecycle test sets up its own fake process.
4. **`core/` unit tests:** `server_process.update_properties` round trip, `java_manager.required_java`
   table, `downloaders._version_key`/`_neoforge_to_mc`, `worlds.import_zip` edge cases,
   `netinfo` fallbacks, and each new pure module from the roadmap (`diagnose`, `backups.prune`,
   `activity.summary`, `modpack`).
5. **CI:** GitHub Actions on push/PR to `development` and `main`: matrix Windows + Ubuntu, Python
   3.13; `pip install -r requirements-dev.txt`; `pytest -q`; `node --check` on the JS files;
   `bash -n` on the shell scripts. Branch protection on `main` requires it, so the self-updater
   only ever fast-forwards to green commits.
6. `pytest.ini` / `pyproject.toml` with `testpaths`, markers, and `-p no:cacheprovider` if the
   cache folder is unwanted.

## Implementation notes
- Update `CLAUDE.md` "Test" command and `docs/knowledge/testing.md` once the split lands.
- Keep the JS↔API contract test — it catches the most common class of past bugs (verification.md
  "Fixed 2026-10-22").

## Acceptance criteria
- `pytest -q` passes with the network disabled, in any order (e.g. with the `pytest-randomly`
  plugin, or running any single file alone), on Windows and Linux in CI.
