"""Bedrock crossplay: Geyser + Floodgate and the PlayIt Bedrock tunnel (FEAT-18)."""
from support import *  # noqa: F401,F403

from core import geyser, modrinth

OLD_CONFIG = """bedrock:
  # The IP address that will listen for connections.
  address: 0.0.0.0
  port: 19132
remote:
  address: auto
  port: 25565
  auth-type: online
"""
NEW_CONFIG = """bedrock:
  port: 19132 # Bedrock players use this
java:
  auth-type: online
"""


@pytest.mark.parametrize("original, section", [(OLD_CONFIG, "remote"), (NEW_CONFIG, "java")])
def test_geyser_config_is_edited_in_place_and_idempotent(tmp_path, original, section):
    path = tmp_path / "plugins" / "Geyser-Spigot" / "config.yml"
    assert geyser.configure(str(tmp_path), "paper", 19200) is False  # not generated yet
    path.parent.mkdir(parents=True)
    path.write_text(original, encoding="utf-8")
    assert geyser.configure(str(tmp_path), "paper", 19200) is True
    first = path.read_text(encoding="utf-8")
    assert "  port: 19200" in first and "  auth-type: floodgate" in first
    assert "  port: 25565" in first or section == "java"  # the Java port is left alone
    assert geyser.configure(str(tmp_path), "paper", 19200) and path.read_text(encoding="utf-8") == first


def test_bedrock_tunnel_request_is_udp_and_typed(monkeypatch):
    calls = []
    tunnels = []

    def fake_api(path, body, secret=None):
        calls.append((path, body))
        if path == "/tunnels/create":
            tunnels.append({"id": "t1", "tunnel_type": body["tunnel_type"],
                            "origin": {"data": {"local_port": body["origin"]["data"]["local_port"]}},
                            "alloc": {"status": "allocated", "data": {"assigned_domain": "bed.joinmc.link",
                                                                       "port_start": 40123}}})
        return {"data": {"tunnels": tunnels}}

    monkeypatch.setattr(playit, "_api", fake_api)
    monkeypatch.setattr(playit, "_agent_id", lambda secret: "agent-1")
    address = playit.ensure_bedrock_tunnel("secret", 19132, log=lambda line: None, timeout_sec=5)
    assert address == "bed.joinmc.link:40123"
    create = next(body for path, body in calls if path == "/tunnels/create")
    assert create["tunnel_type"] == "minecraft-bedrock" and create["port_type"] == "udp"
    assert create["origin"]["data"]["local_port"] == 19132


def test_playit_config_keeps_the_bedrock_address():
    playit.save_config("key1", "java.example", bedrock_address="bed.example:40000")
    playit.save_config("key1", "java2.example")
    assert playit.load_config()["bedrock_address"] == "bed.example:40000"
    playit.save_config("another-key", "x")
    assert "bedrock_address" not in playit.load_config()


def test_bedrock_switch_installs_and_removes_the_plugins(monkeypatch):
    config_store.save({"loader": "vanilla"})
    assert client.post("/api/bedrock", auth=AUTH, json={"enabled": True}).status_code == 400
    config_store.save({"loader": "paper", "version": "1.21.4"})
    plugins = os.path.join(common_module._server_dir_for_config(common_module._effective_config()), "plugins")

    def fake_install(slug, loader, version, dest, log=print):
        filename = f"{slug}.jar"
        open(os.path.join(dest, filename), "wb").close()
        record = modrinth.load_record(dest)
        record[slug] = {"slug": slug, "title": slug, "filename": filename}
        modrinth._save_record(dest, record)
        return filename

    def fake_download(url, dest, log=print, progress=None):
        assert url == geyser.FLOODGATE_SPIGOT_URL  # Floodgate for Paper isn't on Modrinth
        open(dest, "wb").close()
        return dest

    monkeypatch.setattr(modrinth, "install", fake_install)
    monkeypatch.setattr(downloaders, "download", fake_download)
    response = client.post("/api/bedrock", auth=AUTH, json={"enabled": True})
    assert response.status_code == 200 and response.json()["busy"] is True
    assert _wait_until(lambda: not client.get("/api/bedrock", auth=AUTH).json()["busy"])
    info = client.get("/api/bedrock", auth=AUTH).json()
    assert info["enabled"] and info["error"] == "" and not info["configured"]
    assert sorted(n for n in os.listdir(plugins) if n.endswith(".jar")) == ["floodgate-spigot.jar", "geyser.jar"]
    off = client.post("/api/bedrock", auth=AUTH, json={"enabled": False}).json()
    assert not off["enabled"] and [n for n in os.listdir(plugins) if n.endswith(".jar")] == []


def test_bedrock_address_reaches_status_page_and_invite(fake_playit, offline_network, monkeypatch):
    config_store.save({"loader": "paper", "version": "1.21.4", "bedrock_enabled": True})
    monkeypatch.setattr(playit, "ensure_bedrock_tunnel",
                        lambda secret, port, log, timeout_sec=120, should_abort=None: "bed.joinmc.link:40123")
    playit.save_config("ab12cd34ef", "")
    assert client.post("/api/playit/start", auth=AUTH).status_code == 200
    assert playit.load_config()["bedrock_address"] == "bed.joinmc.link:40123"
    info, token = _enable_status_page()
    assert "Bedrock (phone/console): bed.joinmc.link:40123" in info["invite"]
    assert client.get(f"/s/{token}/status.json").json()["bedrock_address"] == "bed.joinmc.link:40123"
    assert client.get("/api/bedrock", auth=AUTH).json()["address"] == "bed.joinmc.link:40123"


def test_bedrock_tunnel_failure_does_not_break_the_java_tunnel(fake_playit, monkeypatch):
    config_store.save({"loader": "paper", "version": "1.21.4", "bedrock_enabled": True})

    def refused(secret, port, log, timeout_sec=120, should_abort=None):
        raise RuntimeError("TunnelTypeNotAllowed")

    monkeypatch.setattr(playit, "ensure_bedrock_tunnel", refused)
    playit.save_config("ab12cd34ef", "")
    assert client.post("/api/playit/start", auth=AUTH).json()["state"] == "running"
    assert "TunnelTypeNotAllowed" in client.get("/api/bedrock", auth=AUTH).json()["error"]


def test_bedrock_players_can_be_whitelisted_with_the_floodgate_prefix():
    config_store.save({"online_mode": False})
    response = client.post("/api/players/whitelist", auth=AUTH, json={"action": "add", "name": ".Gamer_Tag"})
    assert response.status_code == 200 and response.json()["whitelist"] == [{"name": ".Gamer_Tag"}]
    assert 'id="bedrock-enabled"' in client.get("/dashboard/remote").text
    assert 'id="whitelist-bedrock"' in client.get("/dashboard/players").text
