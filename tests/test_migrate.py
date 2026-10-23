"""Moving to another Minecraft version with worlds, lists and mods (FEAT-09)."""
from support import *  # noqa: F401,F403

from core import migrate, modrinth


@pytest.fixture
def fabric_1_21_1(monkeypatch):
    """Fabric 1.21.1 with a world, a whitelist and three mods; Modrinth knows 1.21.4 builds for two."""
    config_store.save({"loader": "fabric", "version": "1.21.1", "online_mode": False})
    old = common_module._server_dir_for_config(common_module._effective_config())
    os.makedirs(os.path.join(old, "world", "region"), exist_ok=True)
    with open(os.path.join(old, "world", "level.dat"), "wb") as handle:
        handle.write(b"level")
    with open(os.path.join(old, "whitelist.json"), "w", encoding="utf-8") as handle:
        json.dump([{"uuid": "u1", "name": "Alex"}], handle)
    mods = os.path.join(old, "mods")
    os.makedirs(mods)
    jars = {"sodium-1.21.1.jar": b"s", "lithium-1.21.1.jar": b"l", "abandoned.jar": b"a"}
    for name, data in jars.items():
        with open(os.path.join(mods, name), "wb") as handle:
            handle.write(data)
    new_files = {"sodium-1.21.4.jar": b"S4", "lithium-1.21.4.jar": b"L4"}
    sha = {hashlib.sha1(data).hexdigest(): name for name, data in jars.items()}

    class Reply:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def fake_post(url, headers, timeout, json):
        assert json["loaders"] == ["fabric"]
        answer = {}
        if json["game_versions"] != ["1.21.4"]:
            return Reply(answer)  # nothing built for other versions
        for digest in json["hashes"]:
            name = sha[digest]
            if name != "abandoned.jar":
                new = name.replace("1.21.1", "1.21.4")
                answer[digest] = {"project_id": name.split("-")[0], "version_number": "for-1.21.4", "files": [
                    {"primary": True, "filename": new, "url": f"https://cdn.modrinth.com/{new}",
                     "hashes": {"sha1": hashlib.sha1(new_files[new]).hexdigest()}}]}
        return Reply(answer)

    def fake_download(url, dest, log=print, progress=None):
        with open(dest, "wb") as handle:
            handle.write(new_files[url.rsplit("/", 1)[1]])
        return dest

    monkeypatch.setattr(migrate.requests, "post", fake_post)
    monkeypatch.setattr(downloaders, "download", fake_download)
    monkeypatch.setattr(common_module, "_server_is_installed", lambda target_dir, loader: True)
    return {"old": old, "new": os.path.join(paths.SERVERS_DIR, "fabric-1.21.4")}


def test_plan_lists_worlds_mods_and_warnings(fabric_1_21_1):
    plan = client.post("/api/server/migrate/plan", auth=AUTH, json={"loader": "fabric", "version": "1.21.4"}).json()
    assert plan["worlds"] == ["world"] and "whitelist.json" in plan["files"]
    assert [m["file"] for m in plan["mods"]["compatible"]] == ["lithium-1.21.1.jar", "sodium-1.21.1.jar"]
    assert [m["file"] for m in plan["mods"]["missing"]] == ["abandoned.jar"]
    assert not plan["downgrade"] and not plan["loader_change"] and plan["enough_space"]
    older = client.post("/api/server/migrate/plan", auth=AUTH, json={"loader": "paper", "version": "1.20.4"}).json()
    assert older["downgrade"] and older["loader_change"] and older["mods"]["compatible"] == []
    same = client.post("/api/server/migrate/plan", auth=AUTH, json={"loader": "fabric", "version": "1.21.1"})
    assert same.status_code == 400


def test_migration_copies_everything_and_leaves_the_old_version(fabric_1_21_1):
    body = {"loader": "fabric", "version": "1.21.4"}
    blocked = client.post("/api/server/migrate", auth=AUTH, json=body)
    assert blocked.status_code == 409 and "abandoned" in blocked.json()["detail"]
    response = client.post("/api/server/migrate", auth=AUTH, json={**body, "skip_missing": True})
    assert response.status_code == 202
    _wait_idle()
    old, new = fabric_1_21_1["old"], fabric_1_21_1["new"]
    assert app.state.runtime.operation_error == "", app.state.runtime.operation_error
    assert open(os.path.join(new, "world", "level.dat"), "rb").read() == b"level"
    assert json.load(open(os.path.join(new, "whitelist.json"), encoding="utf-8"))[0]["name"] == "Alex"
    assert sorted(n for n in os.listdir(os.path.join(new, "mods")) if n.endswith(".jar")) == [
        "lithium-1.21.4.jar", "sodium-1.21.4.jar"]
    assert modrinth.load_record(os.path.join(new, "mods"))["sodium"]["version"] == "for-1.21.4"
    assert sorted(os.listdir(os.path.join(old, "mods"))) == ["abandoned.jar", "lithium-1.21.1.jar", "sodium-1.21.1.jar"]
    assert os.path.exists(os.path.join(old, "world", "level.dat"))  # untouched
    assert any("auto-pre-migrate" in name for name in os.listdir(old))
    config = common_module._effective_config()
    assert (config["loader"], config["version"]) == ("fabric", "1.21.4")
    assert "first start upgrades the world" in app.state.runtime.operation_message


def test_downgrade_needs_confirmation_and_running_server_is_refused(fabric_1_21_1, fake_server):
    response = client.post("/api/server/migrate", auth=AUTH, json={"loader": "fabric", "version": "1.20.4",
                                                                    "skip_missing": True})
    assert response.status_code == 409 and "older version" in response.json()["detail"]
    _start()
    response = client.post("/api/server/migrate", auth=AUTH, json={"loader": "fabric", "version": "1.21.4",
                                                                    "skip_missing": True})
    assert response.status_code == 409 and "Stop the server" in response.json()["detail"]


def test_existing_target_worlds_are_kept_aside(fabric_1_21_1):
    os.makedirs(os.path.join(fabric_1_21_1["new"], "world"))
    with open(os.path.join(fabric_1_21_1["new"], "world", "level.dat"), "wb") as handle:
        handle.write(b"older copy")
    client.post("/api/server/migrate", auth=AUTH, json={"loader": "fabric", "version": "1.21.4", "skip_missing": True})
    _wait_idle()
    kept = [n for n in os.listdir(fabric_1_21_1["new"]) if n.startswith("world-pre-migrate-")]
    assert len(kept) == 1
    assert open(os.path.join(fabric_1_21_1["new"], kept[0], "level.dat"), "rb").read() == b"older copy"
    assert 'id="migrate-form"' in client.get("/dashboard/setup").text
