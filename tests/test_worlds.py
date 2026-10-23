"""Worlds: upload, switch, rename, delete, download."""
from support import *  # noqa: F401,F403


def test_world_import_finds_nested_singleplayer_save(tmp_path):
    from core import worlds

    sdir = tmp_path / "server"
    sdir.mkdir()
    zip_path = _write_zip(tmp_path / "w.zip", {
        "saves/My World/level.dat": "x",
        "saves/My World/region/r.0.0.mca": "chunk",
        "saves/My World/DIM-1/region/r.0.0.mca": "nether",
        "__MACOSX/saves/My World/._level.dat": "junk",
    })
    folder = worlds.import_zip(str(sdir), zip_path, "My World")
    assert folder == "my-world"
    assert (sdir / "my-world" / "region" / "r.0.0.mca").read_text() == "chunk"
    assert (sdir / "my-world" / "DIM-1" / "region" / "r.0.0.mca").exists()
    listed = worlds.list_worlds(str(sdir), "world")
    entry = next(w for w in listed if w["folder"] == "my-world")
    assert entry["name"] == "My World" and entry["exists"] and entry["source"] == "upload"


def test_world_import_keeps_paper_dimensions_together(tmp_path):
    from core import worlds

    sdir = tmp_path / "server"
    sdir.mkdir()
    zip_path = _write_zip(tmp_path / "p.zip", {
        "world/level.dat": "x", "world_nether/level.dat": "n", "world_the_end/level.dat": "e",
    })
    folder = worlds.import_zip(str(sdir), zip_path, "Paper world")
    assert (sdir / f"{folder}_nether" / "level.dat").exists()
    assert (sdir / f"{folder}_the_end" / "level.dat").exists()
    folders = [w["folder"] for w in worlds.list_worlds(str(sdir), folder)]
    assert folders == [folder]  # dimension folders are not listed as separate worlds


def test_world_import_rejects_bad_zips(tmp_path):
    from core import worlds

    sdir = tmp_path / "server"
    sdir.mkdir()
    with pytest.raises(ValueError, match="No Minecraft world"):
        worlds.import_zip(str(sdir), _write_zip(tmp_path / "a.zip", {"readme.txt": "hi"}), "A")
    with pytest.raises(ValueError, match="Unsafe"):
        worlds.import_zip(str(sdir), _write_zip(tmp_path / "b.zip", {"w/level.dat": "x", "w/../../evil.txt": "x"}), "B")
    assert not (tmp_path / "evil.txt").exists()
    assert os.listdir(sdir) == []  # failed imports leave nothing behind


def test_worlds_endpoints_create_switch_rename_delete(monkeypatch):
    original = common_module._effective_config
    app.state.runtime.process = None
    sdir = common_module._server_dir_for_config(original())
    os.makedirs(os.path.join(sdir, "world"), exist_ok=True)  # the default world, as after a first start
    with open(os.path.join(sdir, "world", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("x")
    created = client.post("/api/worlds", auth=("admin", "s3cret-pass"), json={"name": "Survival 2", "seed": "123"}).json()
    folder = created["folder"]
    entry = next(w for w in created["worlds"] if w["folder"] == folder)
    assert entry["exists"] is False and entry["seed"] == "123"

    client.post(f"/api/worlds/{folder}/activate", auth=("admin", "s3cret-pass"))
    config = original()
    assert config["level_name"] == folder and config["level_seed"] == "123"
    assert common_module._server_properties_from_config(config)["level-name"] == folder

    renamed = client.post(f"/api/worlds/{folder}/rename", auth=("admin", "s3cret-pass"), json={"name": "Hardcore"}).json()
    assert next(w for w in renamed["worlds"] if w["folder"] == folder)["name"] == "Hardcore"

    response = client.delete(f"/api/worlds/{folder}", auth=("admin", "s3cret-pass"))
    assert response.status_code == 409  # active world can't be deleted
    client.post("/api/worlds/world/activate", auth=("admin", "s3cret-pass"))
    response = client.delete(f"/api/worlds/{folder}", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert folder not in [w["folder"] for w in response.json()["worlds"]]
    assert client.post("/api/worlds/../x/activate", auth=("admin", "s3cret-pass")).status_code in {400, 404}


def test_world_upload_download_and_switch_restarts_running_server(fake_server):
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    os.makedirs(os.path.join(sdir, "world"), exist_ok=True)
    with open(os.path.join(sdir, "world", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("x")
    upload = client.post("/api/worlds/upload", auth=("admin", "s3cret-pass"),
                         files={"file": ("Island.zip", _zip_bytes({"Island/level.dat": "x", "Island/region/a.mca": "a"}),
                                         "application/zip")},
                         data={"name": "Island"})
    assert upload.status_code == 200
    folder = upload.json()["folder"]

    download = client.get(f"/api/worlds/{folder}/download", auth=("admin", "s3cret-pass"))
    assert download.status_code == 200
    import io
    assert f"{folder}/region/a.mca" in zipfile.ZipFile(io.BytesIO(download.content)).namelist()

    _start()
    switch = client.post(f"/api/worlds/{folder}/activate", auth=("admin", "s3cret-pass"))
    assert switch.status_code == 202 and "Switching" in switch.json()["message"]
    _wait_idle()
    assert "restarted" in app.state.runtime.operation_message
    assert len(fake_server) == 2 and not fake_server[0].is_running() and fake_server[1].is_running()
    client.post("/api/worlds/world/activate", auth=("admin", "s3cret-pass"))
    _wait_idle()


def test_backup_follows_the_active_world(monkeypatch):
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "level_name": "skyblock"})
    app.state.runtime.process = None
    sdir = common_module._server_dir_for_config(original())
    os.makedirs(os.path.join(sdir, "skyblock"), exist_ok=True)
    with open(os.path.join(sdir, "skyblock", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("v1")
    backup = client.post("/api/server/backup", auth=("admin", "s3cret-pass")).json()
    assert backup["ok"] and "world-backup-skyblock-" in backup["archive"]
    with open(os.path.join(sdir, "skyblock", "level.dat"), "w", encoding="utf-8") as handle:
        handle.write("v2")
    restored = client.post("/api/server/backups/restore", auth=("admin", "s3cret-pass"),
                           json={"backup_path": backup["archive"]})
    assert restored.status_code == 200
    with open(os.path.join(sdir, "skyblock", "level.dat"), encoding="utf-8") as handle:
        assert handle.read() == "v1"
    assert any(name.startswith("skyblock-pre-restore-") for name in os.listdir(sdir))
