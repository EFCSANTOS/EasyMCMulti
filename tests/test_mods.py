"""Mods: Modrinth browser and install, client mod pack (FEAT-04), update checker (FEAT-07)."""
from support import *  # noqa: F401,F403


def test_modrinth_install_accepts_project_id(monkeypatch):
    captured = {}

    def fake_install(identifier, loader, version, target_folder, log=print):
        captured["identifier"] = identifier
        captured["loader"] = loader
        captured["version"] = version
        captured["target_folder"] = target_folder
        return "fake-mod.jar"

    monkeypatch.setattr(modrinth, "install", fake_install)
    monkeypatch.setattr(common_module, "_effective_config", lambda: {"loader": "fabric", "version": "1.21.7"})

    response = client.post(
        "/api/modrinth/install",
        auth=("admin", "s3cret-pass"),
        json={"project_id": "abc123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "fake-mod.jar"
    assert captured["identifier"] == "abc123"


def test_mod_upload_rejected_for_vanilla(monkeypatch):
    monkeypatch.setattr(common_module, "_effective_config", lambda: {"loader": "vanilla", "version": "1.21.7"})
    response = client.post(
        "/api/server/mods/upload",
        auth=("admin", "s3cret-pass"),
        files={"files": ("demo.jar", b"jar-bytes", "application/java-archive")},
    )
    assert response.status_code == 400
    assert "vanilla" in response.json()["detail"]


def test_mod_browser_lists_popular_server_mods_without_a_query(fake_modrinth):
    response = client.get("/api/modrinth/search", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert response.json()["hits"][0]["slug"] == "sodium"
    params = fake_modrinth[-1][1]
    assert params["index"] == "downloads"
    facets = json.loads(params["facets"])
    assert ["server_side:required", "server_side:optional"] in facets
    assert ["categories:fabric"] in facets and ["versions:1.21.7"] in facets
    assert client.get("/api/modrinth/categories", auth=("admin", "s3cret-pass")).json()["categories"] == ["optimization"]


def test_mod_install_brings_required_dependencies_and_can_be_removed(fake_modrinth):
    response = client.post("/api/modrinth/install", auth=("admin", "s3cret-pass"), json={"slug": "sodium"})
    payload = response.json()
    assert payload["filename"] == "sodium.jar"
    assert payload["dependencies"] == ["Fabric API"]
    mods = client.get("/api/server/mods", auth=("admin", "s3cret-pass")).json()
    assert {m["name"] for m in mods["mods"]} == {"sodium.jar", "fabric-api.jar"}
    assert set(mods["project_ids"]) == {"P1", "P2"}

    details = client.get("/api/modrinth/project/sodium", auth=("admin", "s3cret-pass")).json()
    assert details["compatible_versions"] == 1 and details["body"].startswith("Fast")
    assert "<" not in details["body"] and "**" not in details["body"]

    removed = client.delete("/api/server/mods/sodium.jar", auth=("admin", "s3cret-pass"))
    assert removed.status_code == 200
    mods = client.get("/api/server/mods", auth=("admin", "s3cret-pass")).json()
    assert [m["name"] for m in mods["mods"]] == ["fabric-api.jar"]


def test_mod_browser_explains_vanilla(monkeypatch):
    original = common_module._effective_config
    monkeypatch.setattr(common_module, "_effective_config", lambda: {**original(), "loader": "vanilla"})
    response = client.get("/api/modrinth/search", auth=("admin", "s3cret-pass"))
    assert response.status_code == 400
    assert "Vanilla" in response.json()["detail"]


def test_modpack_index_follows_the_mrpack_format(fabric_server):
    summary = client.post("/api/modpack/build", auth=AUTH).json()
    assert summary["modrinth"] == 1 and summary["bundled"] == ["homemade.jar"] and summary["excluded"] == ["Lag Fix"]
    assert summary["filename"] == "Our-Pack-1.21.1.mrpack" and summary["loader_version"] == "0.16.10"
    with zipfile.ZipFile(modpack.pack_path(fabric_server["sdir"])) as pack:
        index = json.loads(pack.read("modrinth.index.json"))
        assert pack.read("overrides/mods/homemade.jar") == b"not on modrinth"
        assert "overrides/mods/lagfix.jar" not in pack.namelist()
    assert index["formatVersion"] == 1 and index["game"] == "minecraft"
    assert index["dependencies"] == {"minecraft": "1.21.1", "fabric-loader": "0.16.10"}
    [entry] = index["files"]
    content = fabric_server["jars"]["sodium.jar"]
    assert entry["path"] == "mods/sodium.jar" and entry["fileSize"] == len(content)
    assert entry["hashes"] == {"sha1": hashlib.sha1(content).hexdigest(), "sha512": hashlib.sha512(content).hexdigest()}
    assert entry["downloads"] == ["https://cdn.modrinth.com/data/P1/sodium.jar"]
    assert entry["env"] == {"client": "required", "server": "optional"}
    download = client.get("/api/modpack/download", auth=AUTH)
    assert download.status_code == 200 and 'filename="Our-Pack-1.21.1.mrpack"' in download.headers["content-disposition"]


def test_modpack_is_cached_until_mods_change(fabric_server):
    first = client.post("/api/modpack/build", auth=AUTH).json()
    second = client.post("/api/modpack/build", auth=AUTH).json()
    assert second["built_at"] == first["built_at"] and len(fabric_server["calls"]) == 1
    with open(os.path.join(fabric_server["sdir"], "mods", "extra.jar"), "wb") as handle:
        handle.write(b"new mod")
    client.post("/api/modpack/build", auth=AUTH)
    assert len(fabric_server["calls"]) == 2


def test_modpack_bundles_everything_when_modrinth_is_down(fabric_server, monkeypatch):
    import requests as requests_lib

    def offline(hashes):
        raise requests_lib.ConnectionError("no internet")

    monkeypatch.setattr(modpack, "_lookup", offline)
    summary = client.post("/api/modpack/build", auth=AUTH).json()
    assert summary["modrinth"] == 0 and len(summary["bundled"]) == 3
    assert any("could not be reached" in warning for warning in summary["warnings"])


def test_client_jars_download_one_by_one_without_server_only_mods(fabric_server):
    data = client.get("/api/modpack/jars", auth=AUTH).json()
    assert [jar["name"] for jar in data["jars"]] == ["homemade.jar", "sodium.jar"] and data["excluded"] == ["Lag Fix"]
    assert data["jars"][1]["size"] == len(fabric_server["jars"]["sodium.jar"])
    download = client.get("/api/modpack/jars/sodium.jar", auth=AUTH)
    assert download.status_code == 200 and download.content == fabric_server["jars"]["sodium.jar"]
    assert 'filename="sodium.jar"' in download.headers["content-disposition"]
    for name in ("lagfix.jar", "missing.jar", "..%2Fserver.jar"):  # server-only, unknown, path trick
        assert client.get(f"/api/modpack/jars/{name}", auth=AUTH).status_code == 404
    assert client.get("/api/modpack/jars/sodium.jar").status_code in (401, 403)

    _, token = _enable_status_page()
    public = client.get(f"/s/{token}/mods.json").json()
    assert [jar["name"] for jar in public["jars"]] == ["homemade.jar", "sodium.jar"]
    assert client.get(f"/s/{token}/mods/homemade.jar").content == b"not on modrinth"
    assert client.get(f"/s/{token}/mods/lagfix.jar").status_code == 404
    assert client.get("/s/wrong-token/mods/sodium.jar").status_code == 404
    assert 'id="modpack-jars-button"' in client.get("/dashboard/mods").text


def test_modpack_not_offered_for_paper_or_empty_servers():
    config_store.save({"loader": "paper", "version": "1.21.4"})
    assert client.get("/api/modpack", auth=AUTH).json()["available"] is False
    response = client.post("/api/modpack/build", auth=AUTH)
    assert response.status_code == 400 and "without mods" in response.json()["detail"]
    config_store.save({"loader": "fabric", "version": "1.21.1"})
    assert client.post("/api/modpack/build", auth=AUTH).json()["detail"] == "There are no mods on this server yet."


def test_loader_versions_are_detected(tmp_path):
    with zipfile.ZipFile(tmp_path / "server.jar", "w") as jar:
        jar.writestr("install.properties", "fabric-loader-version=0.15.11\ngame-version=1.20.4\n")
    assert modpack.loader_version("fabric", str(tmp_path), "1.20.4") == "0.15.11"
    os.makedirs(tmp_path / "libraries" / "net" / "minecraftforge" / "forge" / "1.20.1-47.3.0")
    assert modpack.loader_version("forge", str(tmp_path), "1.20.1") == "47.3.0"
    os.makedirs(tmp_path / "libraries" / "net" / "neoforged" / "neoforge" / "21.1.73")
    assert modpack.loader_version("neoforge", str(tmp_path), "1.21.1") == "21.1.73"


def test_friends_get_the_pack_from_the_status_page(fabric_server, offline_network):
    info, token = _enable_status_page()
    assert "get the mod pack from the status page" in info["invite"]
    data = client.get(f"/s/{token}/status.json").json()
    assert data["mods_required"] is True and data["modpack_url"] == "modpack.mrpack"
    response = TestClient(app).get(f"/s/{token}/modpack.mrpack")  # signed out
    assert response.status_code == 200
    assert "modrinth.index.json" in zipfile.ZipFile(__import__("io").BytesIO(response.content)).namelist()
    assert client.get("/s/wrong-token/modpack.mrpack").status_code == 404


def test_paper_invite_says_no_mods_needed(offline_network):
    config_store.save({"loader": "paper", "version": "1.21.4"})
    assert "(no mods needed)" in client.get("/api/status-page", auth=AUTH).json()["invite"]


def test_check_finds_updates_with_one_request(outdated_mods):
    result = client.post("/api/mods/updates/check", auth=AUTH).json()
    assert [(u["title"], u["current"], u["latest"]) for u in result["updates"]] == [
        ("lithium-0.10", "lithium-0.10", "0.11"), ("Sodium", "0.5", "0.6")]
    assert "fixes" in result["updates"][1]["changelog"] and "url" not in result["updates"][0]
    [(url, body)] = outdated_mods["requests"]
    assert url.endswith("/version_files/update") and len(body["hashes"]) == 3
    assert body["loaders"] == ["fabric"] and body["game_versions"] == ["1.21.1"]


def test_update_replaces_jar_keeps_old_and_installs_dependency(outdated_mods):
    client.post("/api/mods/updates/check", auth=AUTH)
    result = client.post("/api/mods/updates/apply", auth=AUTH, json={"files": ["sodium-0.5.jar"]}).json()
    mods = outdated_mods["mods"]
    assert result["updated"] == ["Sodium"] and result["pack_changed"] is True
    assert open(os.path.join(mods, "sodium-0.6.jar"), "rb").read() == b"sodium new"
    assert os.path.exists(os.path.join(mods, "sodium-0.5.jar.old"))
    assert not os.path.exists(os.path.join(mods, "sodium-0.5.jar"))
    from core import modrinth
    assert modrinth.load_record(mods)["P1"]["version"] == "0.6"
    assert outdated_mods["deps"] == ["P9"]
    assert [u["file"] for u in result["updates"]] == ["lithium-0.10.jar"] and result["rollback_available"]
    assert [b["label"] for b in backups_core.list_backups(outdated_mods["sdir"])] == ["auto-pre-mod-update"]
    undo = client.post("/api/mods/updates/rollback", auth=AUTH).json()
    assert "Restored 1" in undo["message"]
    assert open(os.path.join(mods, "sodium-0.5.jar"), "rb").read() == b"sodium old"
    assert not os.path.exists(os.path.join(mods, "sodium-0.6.jar"))
    assert modrinth.load_record(mods)["P1"]["filename"] == "sodium-0.5.jar"


def test_corrupted_download_leaves_the_old_jar(outdated_mods, monkeypatch):
    client.post("/api/mods/updates/check", auth=AUTH)
    monkeypatch.setattr(mod_updates, "download",
                        lambda url, dest, log=print, progress=None: open(dest, "wb").write(b"tampered") and dest)
    result = client.post("/api/mods/updates/apply", auth=AUTH, json={}).json()
    assert result["updated"] == [] and len(result["failed"]) == 2 and "corrupted" in result["message"]
    assert sorted(os.listdir(outdated_mods["mods"])) == [".ez-modrinth.json", "homemade.jar", "lithium-0.10.jar",
                                                         "sodium-0.5.jar"]


def test_updates_need_a_stopped_server(outdated_mods, fake_server):
    client.post("/api/mods/updates/check", auth=AUTH)
    _start()
    assert client.post("/api/mods/updates/apply", auth=AUTH, json={}).status_code == 409
    assert client.post("/api/mods/updates/rollback", auth=AUTH).status_code == 409


def test_old_jars_are_removed_after_the_server_ran_fine(outdated_mods, fake_server):
    client.post("/api/mods/updates/check", auth=AUTH)
    client.post("/api/mods/updates/apply", auth=AUTH, json={})
    _start()
    tick = app.state.services.scheduler_tick
    started = app.state.runtime.last_start_at
    tick(datetime.datetime.fromtimestamp(started + 60))
    assert mod_updates.old_jars(outdated_mods["mods"]) != []
    tick(datetime.datetime.fromtimestamp(started + common_module.OLD_MODS_KEEP_SEC + 1))
    assert mod_updates.old_jars(outdated_mods["mods"]) == []


def test_vanilla_has_no_mod_updates():
    assert client.post("/api/mods/updates/check", auth=AUTH).status_code == 400
