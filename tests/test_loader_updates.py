"""Updating the Fabric loader in place, keeping the Minecraft version, with a rollback."""
from support import *  # noqa: F401,F403

from core import loader_updates, modpack, storage


def _fabric_jar(path, loader, game="26.2"):
    _write_zip(path, {"install.properties": f"fabric-loader-version={loader}\ngame-version={game}\n"})


@pytest.fixture
def fabric_26_2(monkeypatch):
    """A Fabric 26.2 server on loader 0.19.5 with a world; Fabric meta offers 0.20.1 (and a newer beta)."""
    config_store.save({"loader": "fabric", "version": "26.2", "online_mode": False})
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    os.makedirs(os.path.join(sdir, "world"), exist_ok=True)
    with open(os.path.join(sdir, "world", "level.dat"), "wb") as handle:
        handle.write(b"level")
    _fabric_jar(os.path.join(sdir, "server.jar"), "0.19.5")
    meta = {"loaders": [{"loader": {"version": "0.21.0-beta.1", "stable": False}},
                        {"loader": {"version": "0.20.1", "stable": True}},
                        {"loader": {"version": "0.19.5", "stable": True}}]}
    urls = []

    class Reply:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    def fake_get(url, timeout):
        if url.endswith("/versions/loader/26.2"):
            return Reply(meta["loaders"])
        if url.endswith("/versions/installer"):
            return Reply([{"version": "1.1.0", "stable": True}])
        raise AssertionError(url)

    def fake_download(url, dest, log=print, progress=None):
        urls.append(url)
        _fabric_jar(dest, url.split("/")[-4])  # .../loader/<game>/<loader>/<installer>/server/jar
        return dest

    monkeypatch.setattr(downloaders.requests, "get", fake_get)
    monkeypatch.setattr(downloaders, "download", fake_download)
    return {"sdir": sdir, "meta": meta, "urls": urls}


def test_latest_loader_is_the_newest_stable_one_for_the_game(fabric_26_2):
    assert downloaders.fabric_latest_loader("26.2") == "0.20.1"
    fabric_26_2["meta"]["loaders"] = [{"loader": {"version": "0.21.0-beta.1", "stable": False}}]
    assert downloaders.fabric_latest_loader("26.2") == "0.21.0-beta.1"  # nothing stable yet: newest anyway
    fabric_26_2["meta"]["loaders"] = []
    with pytest.raises(RuntimeError):
        downloaders.fabric_latest_loader("26.2")


def test_status_update_and_rollback(fabric_26_2):
    sdir = fabric_26_2["sdir"]
    status = client.get("/api/server/loader", auth=AUTH).json()
    assert status["supported"] and status["current"] == "0.19.5" and status["latest"] == "0.20.1"
    assert status["update_available"] and status["previous"] == ""

    assert client.post("/api/server/loader/update", auth=AUTH).status_code == 202
    _wait_idle()
    assert app.state.runtime.operation_error == "", app.state.runtime.operation_error
    assert "0.19.5 to 0.20.1" in app.state.runtime.operation_message
    assert fabric_26_2["urls"] == ["https://meta.fabricmc.net/v2/versions/loader/26.2/0.20.1/1.1.0/server/jar"]
    assert loader_updates.jar_loader_version(os.path.join(sdir, "server.jar")) == "0.20.1"
    assert any("auto-pre-loader-update" in name for name in os.listdir(sdir))
    assert modpack.loader_version("fabric", sdir, "26.2") == "0.20.1"
    status = client.get("/api/server/loader", auth=AUTH).json()
    assert not status["update_available"] and status["previous"] == "0.19.5"
    assert client.post("/api/server/loader/update", auth=AUTH).status_code == 202
    _wait_idle()
    assert "already the newest" in app.state.runtime.operation_error

    assert client.post("/api/server/loader/rollback", auth=AUTH).status_code == 202
    _wait_idle()
    assert loader_updates.jar_loader_version(os.path.join(sdir, "server.jar")) == "0.19.5"
    assert loader_updates.jar_loader_version(os.path.join(sdir, "server.jar.previous")) == "0.20.1"
    assert "rolled back from 0.20.1 to 0.19.5" in app.state.runtime.operation_message


def test_a_wrong_download_leaves_the_server_as_it_was(fabric_26_2, monkeypatch):
    def broken(url, dest, log=print, progress=None):
        with open(dest, "wb") as handle:
            handle.write(b"<html>error page</html>")
        return dest

    monkeypatch.setattr(downloaders, "download", broken)
    with pytest.raises(RuntimeError, match="not a Fabric 0.20.1 server"):
        loader_updates.update(fabric_26_2["sdir"], "26.2")
    assert loader_updates.jar_loader_version(os.path.join(fabric_26_2["sdir"], "server.jar")) == "0.19.5"
    assert sorted(n for n in os.listdir(fabric_26_2["sdir"]) if n.startswith("server.jar")) == ["server.jar"]


def test_refusals(fabric_26_2, fake_server, monkeypatch):
    assert client.post("/api/server/loader/rollback", auth=AUTH).status_code == 409  # nothing to go back to
    _start()
    response = client.post("/api/server/loader/update", auth=AUTH)
    assert response.status_code == 409 and "Stop the server" in response.json()["detail"]
    config_store.save({"loader": "paper", "version": "1.21.4"})
    assert client.get("/api/server/loader", auth=AUTH).json() == {"supported": False, "loader": "paper",
                                                                   "version": "1.21.4"}
    assert client.post("/api/server/loader/update", auth=AUTH).status_code == 400


def test_offline_check_still_shows_the_installed_loader(fabric_26_2, monkeypatch):
    def offline(url, timeout):
        raise downloaders.requests.ConnectionError("no network")

    monkeypatch.setattr(downloaders.requests, "get", offline)
    status = client.get("/api/server/loader", auth=AUTH).json()
    assert status["current"] == "0.19.5" and not status["update_available"] and "not reachable" in status["error"]


def test_previous_jar_is_listed_in_storage(fabric_26_2):
    loader_updates.update(fabric_26_2["sdir"], "26.2")
    name = os.path.basename(fabric_26_2["sdir"])
    item = next(i for i in storage.usage(fabric_26_2["sdir"], fresh=True)["items"] if i["id"] == f"previousjar:{name}")
    assert item["deletable"]
    storage.delete(item["id"], fabric_26_2["sdir"])
    assert not os.path.exists(os.path.join(fabric_26_2["sdir"], "server.jar.previous"))


def test_dashboard_has_the_loader_block():
    assert 'id="loader-update"' in client.get("/dashboard/setup", auth=AUTH).text
