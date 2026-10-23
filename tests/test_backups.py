"""Backups, schedule and retention (FEAT-01), storage cleanup (FEAT-15)."""
from support import *  # noqa: F401,F403


def test_legacy_backup_and_mod_routes_still_work():
    response = client.get("/api/backups", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "backups" in response.json()

    response = client.get("/api/mods", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "mods" in response.json()


def test_backup_restore_round_trip_and_validation(monkeypatch):
    monkeypatch.setattr(common_module, "_effective_config", lambda: {"loader": "vanilla", "version": "1.21.7"})
    app.state.runtime.process = None
    server_dir = paths.server_dir("vanilla", "1.21.7")
    good = _make_backup(server_dir, "world-backup-20260101-000000.zip", ["world/level.dat"])
    _make_backup(server_dir, "world-backup-evil.zip", ["world/../../escape.txt"])

    listed = client.get("/api/server/backups", auth=("admin", "s3cret-pass")).json()["backups"]
    assert good in listed

    response = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"), json={"backup_path": good})
    assert response.status_code == 200
    assert os.path.isfile(os.path.join(server_dir, "world", "level.dat"))

    response = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"),
                           json={"backup_path": "world-backup-evil.zip"})
    assert response.status_code == 400
    assert not os.path.exists(os.path.join(paths.DATA_DIR, "escape.txt"))

    outside = _make_backup(paths.DATA_DIR, "world-backup-outside.zip", ["world/level.dat"])
    response = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"), json={"backup_path": outside})
    assert response.status_code == 400


def test_backup_names_old_and_new_parse():
    assert backups_core.parse("world-backup-20260101-000000.zip")["label"] == "manual"
    old = backups_core.parse("world-backup-my-world-20261022-101010.zip")
    assert old["folder"] == "my-world" and old["label"] == "manual"
    new = backups_core.parse("world-backup-my-world-20261022-101010-auto-pre-update.zip")
    assert new["folder"] == "my-world" and new["label"] == "auto-pre-update"
    assert new["created"] == datetime.datetime(2026, 10, 22, 10, 10, 10)
    assert backups_core.parse("world-backup-evil.zip") is None


def test_retention_keeps_recent_and_one_per_day_and_never_manual():
    now = datetime.datetime(2026, 10, 22, 12, 0, 0)

    def item(name, hours_ago, label="auto"):
        return {"name": name, "folder": "world", "label": label, "created": now - datetime.timedelta(hours=hours_ago)}

    items = [item(f"a{h}", h) for h in (1, 2, 3, 4, 30, 31, 55, 80, 200)] + [item("m", 500, "manual")]
    doomed = {entry["name"] for entry in backups_core.to_prune(items, keep_recent=3, keep_daily=3, now=now)}
    # kept: newest 3 (a1 a2 a3) + newest of each of the last 3 days (a1 today, a30 yesterday, a55 two days ago)
    assert doomed == {"a4", "a31", "a80", "a200"}
    assert "m" in {entry["name"] for entry in backups_core.to_prune(items, 3, 3, prune_manual=True, now=now)}


def test_schedule_backs_up_only_a_changing_world(fake_server):
    sdir = _world_dir()
    services = app.state.services
    app.state.runtime.backup_dirty = False
    now = time.time()
    services.backup_tick(now)
    assert backups_core.list_backups(sdir) == []  # stopped and unchanged: nothing to do
    _start()
    fake_server[0].send = lambda command: (fake_server[0].sent.append(command),
                                           command == "save-all flush" and services.append_console_line(
                                               "[12:00:00] [Server thread/INFO]: Saved the game"))
    services.backup_tick(now)
    [first] = backups_core.list_backups(sdir)
    assert first["label"] == "auto"
    assert fake_server[0].sent == ["save-off", "save-all flush", "save-on"]
    services.backup_tick(now + 60)
    assert len(backups_core.list_backups(sdir)) == 1  # not due yet
    services.backup_tick(now + 6 * 3600 + 5)
    assert len(backups_core.list_backups(sdir)) == 2


def test_manual_backup_is_labelled_and_survives_pruning():
    sdir = _world_dir()
    config_store.save({"backup_keep_recent": 1, "backup_keep_daily": 0})
    manual = client.post("/api/server/backup", auth=AUTH).json()
    assert manual["ok"] and manual["archive"].endswith("-manual.zip")
    services = app.state.services
    services.safety_backup("update")
    time.sleep(1.1)
    services.safety_backup("update")
    names = [item["name"] for item in backups_core.list_backups(sdir)]
    assert sum(name.endswith("-manual.zip") for name in names) == 1
    assert sum("auto-pre-update" in name for name in names) == 1  # keep_recent=1 pruned the older one
    listing = client.get("/api/server/backups", auth=AUTH).json()
    assert listing["schedule"]["keep_recent"] == 1 and listing["total_size"] > 0
    assert {item["label"] for item in listing["items"]} == {"manual", "auto-pre-update"}


def test_low_disk_skips_the_backup_and_alerts(monkeypatch, sent_emails):
    _world_dir()
    client.post("/api/server/backup", auth=AUTH)
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(webapp_module_backups.shutil, "disk_usage", lambda path: usage(10, 10, 0))
    response = client.post("/api/server/backup", auth=AUTH)
    assert response.status_code == 500 and "Not enough free space" in response.json()["detail"]
    assert _wait_until(lambda: any("Backup failed" in subject for subject, _ in sent_emails))


def test_deleting_a_world_makes_a_safety_backup_first(monkeypatch):
    sdir = _world_dir("old-world")
    _world_dir("world")
    response = client.delete("/api/worlds/old-world", auth=AUTH)
    assert response.status_code == 200
    assert [item["label"] for item in backups_core.list_backups(sdir)] == ["auto-pre-delete"]
    _world_dir("other")
    monkeypatch.setattr(backups_core, "create", lambda *args: (_ for _ in ()).throw(OSError(28, "disk full")))
    response = client.delete("/api/worlds/other", auth=AUTH)
    assert response.status_code == 500 and os.path.isdir(os.path.join(sdir, "other"))


def test_backup_settings_are_validated():
    assert client.put("/api/config", auth=AUTH, json={"backup_interval_hours": 500}).status_code == 422
    assert client.put("/api/config", auth=AUTH, json={"backup_keep_recent": 0}).status_code == 422
    assert client.put("/api/config", auth=AUTH, json={"backup_interval_hours": 12}).status_code == 200


def test_dashboard_scripts_do_not_redefine_each_others_globals():
    # The scripts share one global scope: a second top-level function/const with the same name
    # silently replaces the first (as nearly happened with formatBytes).
    names = re.findall(r"(?m)^(?:async function|function|const|let)\s+([A-Za-z_$][\w$]*)", _dashboard_js())
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], duplicates


def test_storage_usage_breaks_down_the_data_folder(storage_tree):
    data = client.get("/api/storage?fresh=true", auth=AUTH).json()
    items = {item["id"]: item for item in data["items"]}
    assert items["server:vanilla-1.21.7"]["active"] and not items["server:vanilla-1.21.7"]["deletable"]
    assert items["server:vanilla-1.21.7"]["size"] == 1000
    assert items["server:fabric-1.20.1"]["deletable"] and items["server:fabric-1.20.1"]["size"] == 4000
    assert items["backups:vanilla-1.21.7"]["size"] == 500 and not items["backups:vanilla-1.21.7"]["deletable"]
    assert items["prerestore:vanilla-1.21.7/world-pre-restore-20260901-120000"]["size"] == 300
    assert items["installer:vanilla-1.21.7"]["size"] == 200 and items["oldlogs:vanilla-1.21.7"]["size"] == 50
    assert not items["java:jre-21"]["deletable"]  # 1.21.7 needs Java 21
    assert items["java:jre-8"]["deletable"]  # nobody needs Java 8 here (1.20.1 needs 17)
    assert data["disk"]["free"] > 0 and data["total"] >= 1000 + 4000 + 500 + 300 + 200 + 50 + 700 + 600


def test_storage_cleanup_is_safe(storage_tree, fake_server):
    assert client.post("/api/storage/delete", auth=AUTH, json={"id": "server:vanilla-1.21.7"}).status_code == 409
    assert client.post("/api/storage/delete", auth=AUTH, json={"id": "server:../../etc"}).status_code == 404
    assert client.post("/api/storage/delete", auth=AUTH, json={"id": "backups:vanilla-1.21.7"}).status_code == 409
    response = client.post("/api/storage/delete", auth=AUTH, json={"id": "server:fabric-1.20.1"})
    assert response.status_code == 200 and "freed" in response.json()["message"]
    assert not os.path.exists(storage_tree["old"])
    assert client.post("/api/storage/delete", auth=AUTH, json={"id": "java:jre-8"}).status_code == 200
    assert client.post("/api/storage/delete", auth=AUTH, json={"id": "oldlogs:vanilla-1.21.7"}).status_code == 200
    assert os.path.exists(os.path.join(storage_tree["active"], "world", "level.dat"))
    _start()
    assert client.post("/api/storage/delete", auth=AUTH, json={"id": "installer:vanilla-1.21.7"}).status_code == 409


def test_low_space_alert_is_sent_once(monkeypatch, sent_emails):
    app.state.runtime.low_space_alerted = False
    monkeypatch.setattr(storage_core, "disk", lambda path=None: {"total": 100 * 2**30, "free": 2**29, "low": True})
    app.state.services.storage_tick()
    app.state.services.storage_tick()
    assert _wait_until(lambda: any("Storage almost full" in s for s, _ in sent_emails))
    time.sleep(0.1)
    assert sum("Storage almost full" in s for s, _ in sent_emails) == 1
    monkeypatch.setattr(storage_core, "disk", lambda path=None: {"total": 100 * 2**30, "free": 50 * 2**30, "low": False})
    app.state.services.storage_tick()
    assert app.state.runtime.low_space_alerted is False
