"""PlayIt, the Cloudflare dashboard link and the public status page (FEAT-03)."""
from support import *  # noqa: F401,F403


def test_playit_status_endpoint_reports_configuration():
    response = client.get("/api/playit/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "configured" in payload
    assert "state" in payload
    assert "address" in payload


def test_playit_start_route_starts_the_tunnel(fake_playit):
    playit.save_config("demo-secret", "")
    response = client.post("/api/playit/start", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "running"
    assert payload["address"] == "demo.example:25565"


def test_playit_configure_returns_approval_link_and_finishes_in_background(fake_playit, monkeypatch):
    approved = threading.Event()

    def fake_claim(log, should_cancel, timeout_sec=300, on_url=None):
        on_url("https://playit.gg/claim/abc123")
        approved.wait(5)
        playit.save_config("new-secret", "")
        return "new-secret"

    monkeypatch.setattr(playit, "claim_interactive", fake_claim)
    response = client.post("/api/playit/configure", auth=("admin", "s3cret-pass"))
    payload = response.json()
    assert payload["setup"] == "awaiting-approval"
    assert payload["claim_url"] == "https://playit.gg/claim/abc123"

    approved.set()
    deadline = time.monotonic() + 5
    while app.state.runtime.playit_setup != "idle" and time.monotonic() < deadline:
        time.sleep(0.05)
    status = client.get("/api/playit/status", auth=("admin", "s3cret-pass")).json()
    assert status["setup"] == "idle"
    assert status["state"] == "running"
    assert status["address"] == "demo.example:25565"
    assert status["claim_url"] == ""


def test_playit_configure_reset_forgets_saved_account(fake_playit, monkeypatch):
    playit.save_config("old-secret", "old.example:1")
    monkeypatch.setattr(playit, "claim_interactive",
                        lambda log, should_cancel, timeout_sec=300, on_url=None: on_url("https://playit.gg/claim/new") or threading.Event().wait())
    response = client.post("/api/playit/configure", auth=("admin", "s3cret-pass"), json={"reset": True})
    payload = response.json()
    assert payload["configured"] is False
    assert payload["claim_url"] == "https://playit.gg/claim/new"
    assert "secret" not in playit.load_config()
    app.state.runtime.playit_cancel = True


def test_remote_dashboard_toggle(fake_cloudflared):
    response = client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert response.json()["enabled"] is True
    assert _wait_until(lambda: client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()["state"] == "running")
    status = client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()
    assert status["url"] == "https://demo-1.trycloudflare.com"
    assert fake_cloudflared[0].port == common_module.WEB_PORT

    response = client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": False})
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["state"] == "off"
    assert payload["url"] == ""
    assert not fake_cloudflared[0].is_running()


def test_remote_dashboard_restarts_after_unexpected_exit(fake_cloudflared):
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert _wait_until(lambda: len(fake_cloudflared) == 1 and fake_cloudflared[0].is_running())
    fake_cloudflared[0].crash()
    assert _wait_until(lambda: len(fake_cloudflared) == 2 and app.state.runtime.web_tunnel_state == "running")
    assert app.state.runtime.web_tunnel_url == "https://demo-2.trycloudflare.com"


def test_remote_dashboard_reports_missing_binary(monkeypatch):
    def missing(log=print):
        raise RuntimeError("cloudflared is not installed. Install it in Termux with: pkg install cloudflared")

    monkeypatch.setattr(cloudflared, "ensure_binary", missing)
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": True})
    assert _wait_until(lambda: app.state.runtime.web_tunnel_state == "error")
    status = client.get("/api/remote-dashboard", auth=("admin", "s3cret-pass")).json()
    assert "pkg install cloudflared" in status["error"]
    client.post("/api/remote-dashboard", auth=("admin", "s3cret-pass"), json={"enabled": False})


def test_quick_tunnel_announces_url_only_after_registration():
    from core import cloudflared

    lines = [
        "INF Requesting new quick Tunnel on trycloudflare.com...\n",
        "INF |  https://quiet-river-boat-lamp.trycloudflare.com  |\n",
        "INF Starting metrics server\n",
        "INF Registered tunnel connection connIndex=0 location=lis01\n",
    ]
    events = []

    class FakeProc:
        stdout = iter(lines)

        def wait(self):
            return 0

    tunnel = cloudflared.QuickTunnel(
        on_url=lambda url: events.append(("url", url)),
        on_output=lambda line: events.append(("line", line[:12])),
        on_exit=lambda code: events.append(("exit", code)),
    )
    tunnel.proc = FakeProc()
    tunnel._reader()
    url_index = events.index(("url", "https://quiet-river-boat-lamp.trycloudflare.com"))
    registered_index = events.index(("line", "INF Register"))
    assert url_index < registered_index  # announced while handling the registration line
    assert ("line", "INF Starting") in events[:url_index]  # not before registration
    assert events[-1] == ("exit", 0)


def test_cloudflared_url_parsing_and_assets(monkeypatch):
    from core import cloudflared

    line = "2026-10-22T01:00:00Z INF |  https://quiet-river-boat-lamp.trycloudflare.com  |"
    assert cloudflared.find_url(line) == "https://quiet-river-boat-lamp.trycloudflare.com"
    assert cloudflared.find_url("INF Starting tunnel") is None
    monkeypatch.setattr(cloudflared.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cloudflared.platform, "machine", lambda: "AMD64")
    assert cloudflared._asset_name() == "cloudflared-windows-amd64.exe"
    monkeypatch.setattr(cloudflared.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cloudflared.platform, "machine", lambda: "aarch64")
    assert cloudflared._asset_name() == "cloudflared-linux-arm64"


def test_cloudflared_is_installed_automatically_on_termux(monkeypatch):
    from core import cloudflared, hostenv

    installed = []
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(hostenv, "termux_install", lambda package, log=print: installed.append(package))
    monkeypatch.setattr(cloudflared.shutil, "which", lambda name: "/usr/bin/cloudflared" if installed else None)
    assert cloudflared.ensure_binary(log=lambda message: None) == "/usr/bin/cloudflared"
    assert installed == ["cloudflared"]


def test_playit_agent_uses_termux_chroot_on_termux(monkeypatch):
    monkeypatch.setattr(playit.hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(playit.shutil, "which", lambda name: "/usr/bin/termux-chroot")
    monkeypatch.setattr(playit, "_agent_path", lambda: "/data/playit-linux-aarch64")
    assert playit.agent_command("s")[:2] == ["/usr/bin/termux-chroot", "/data/playit-linux-aarch64"]
    monkeypatch.setenv("EZ_PLAYIT_CHROOT", "0")
    assert playit.agent_command("s")[0] == "/data/playit-linux-aarch64"


def test_playit_agent_name_is_platform_aware(monkeypatch):
    monkeypatch.setattr(playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(playit.platform, "machine", lambda: "aarch64")
    assert playit._agent_binary_name() == "playit-linux-aarch64"

    monkeypatch.setattr(playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(playit.platform, "machine", lambda: "x86_64")
    assert playit._agent_binary_name() == "playit-linux-amd64"

    monkeypatch.setattr(playit.platform, "system", lambda: "Linux")
    monkeypatch.setattr(playit.platform, "machine", lambda: "armv7l")
    assert playit._agent_binary_name() == "playit-linux-armv7"


def test_playit_config_does_not_expose_secret(monkeypatch):
    monkeypatch.setattr(playit, "load_config", lambda: {"secret": "top-secret", "address": "demo.example"})
    response = client.get("/api/playit/config", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "top-secret" not in response.text
    assert response.json() == {"address": "demo.example", "secret_present": True}


def test_playit_config_put_saves_and_keeps_secret_when_blank(fake_playit):
    auth = ("admin", "s3cret-pass")
    assert client.put("/api/playit/config", auth=auth, json={"secret": "", "address": ""}).status_code == 400
    response = client.put("/api/playit/config", auth=auth, json={"secret": "  ab12cd34ef  ", "address": "a.example"})
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert "ab12cd34ef" not in response.text
    response = client.put("/api/playit/config", auth=auth, json={"secret": "", "address": "b.example"})
    assert response.status_code == 200
    assert playit.load_config() == {"secret": "ab12cd34ef", "address": "b.example"}
    assert client.put("/api/playit/config", auth=auth, json={"secret": "x", "extra": 1}).status_code == 400
    response = client.put("/api/playit/config", auth=auth, json={"secret": "not-a-playit-key"})
    assert response.status_code == 400
    assert "hexadecimal" in response.json()["detail"]
    assert playit.load_config()["secret"] == "ab12cd34ef"


def test_playit_config_put_cancels_pending_approval(fake_playit, monkeypatch):
    def fake_claim(log, should_cancel, timeout_sec=300, on_url=None):
        on_url("https://playit.gg/claim/abc123")
        while not should_cancel():
            time.sleep(0.01)
        raise RuntimeError("Setup cancelled.")

    monkeypatch.setattr(playit, "claim_interactive", fake_claim)
    auth = ("admin", "s3cret-pass")
    assert client.post("/api/playit/configure", auth=auth).json()["setup"] == "awaiting-approval"
    response = client.put("/api/playit/config", auth=auth, json={"secret": "ab12cd34ef"})
    assert response.status_code == 200
    assert response.json()["setup"] == "idle"
    time.sleep(0.1)
    status = client.get("/api/playit/status", auth=auth).json()
    assert status["setup"] == "idle" and status["error"] == ""
    assert client.post("/api/playit/start", auth=auth).json()["state"] == "running"


def test_playit_status_is_not_running_without_agent(monkeypatch):
    monkeypatch.setattr(playit, "load_config", lambda: {"secret": "demo-secret", "address": "demo.example"})
    app.state.runtime.playit_agent = None
    response = client.get("/api/playit/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert response.json()["state"] == "configured"


def test_status_page_is_off_by_default(offline_network):
    assert client.get("/api/status-page", auth=AUTH).json()["enabled"] is False
    assert client.get("/s/anything").status_code == 404
    assert client.get("/s/anything/status.json").status_code == 404
    assert client.get("/api/status-page").status_code == 401


def test_status_page_shows_only_public_fields(offline_network, monkeypatch):
    playit.save_config("very-secret-playit-key", "friends.joinmc.link")
    notifier.save_config({"smtp_password": "smtp-secret-pass", "email_to": "owner@example.com"})
    config_store.save({"motd": "Our World", "max_players": 8, "server_port": 25570})
    info, token = _enable_status_page()
    assert info["enabled"] and info["url_is_lan_only"] and info["url"].startswith("http://192.168.1.50:")
    page = client.get(f"/s/{token}")
    assert page.status_code == 200 and "noindex" in page.headers["x-robots-tag"]
    data = client.get(f"/s/{token}/status.json").json()
    assert data["name"] == "Our World" and data["online"] is False and data["players_max"] == 8
    assert data["address"] == "friends.joinmc.link" and "lan_address" not in data
    everything = page.text + json.dumps(data)
    for secret in ("very-secret-playit-key", "smtp-secret-pass", "owner@example.com", token, "192.168.1.50", "s3cret-pass"):
        assert secret not in everything, secret
    client.post("/api/status-page", auth=AUTH, json={"action": "settings", "show_lan": True, "show_names": False})
    data = client.get(f"/s/{token}/status.json").json()
    assert data["lan_address"] == "192.168.1.50:25570" and "player_names" not in data


def test_status_page_falls_back_to_public_ip(offline_network):
    config_store.save({"server_port": 25565})
    _, token = _enable_status_page()
    assert client.get(f"/s/{token}/status.json").json()["address"] == "203.0.113.9"


def test_regenerating_or_disabling_kills_the_old_link(offline_network):
    _, token = _enable_status_page()
    new_token = client.post("/api/status-page", auth=AUTH, json={"action": "regenerate"}).json()["url"].rsplit("/s/", 1)[1]
    assert new_token != token
    assert client.get(f"/s/{token}").status_code == 404
    assert client.get(f"/s/{new_token}").status_code == 200
    client.post("/api/status-page", auth=AUTH, json={"action": "disable"})
    assert client.get(f"/s/{new_token}").status_code == 404
    assert client.get(f"/s/{new_token}/status.json").status_code == 404


def test_status_page_token_cannot_be_set_through_config():
    response = client.put("/api/config", auth=AUTH, json={"status_page_token": "chosen-by-attacker"})
    assert response.status_code == 422


def test_invite_text_has_address_version_and_mods(offline_network):
    playit.save_config("k", "friends.joinmc.link")
    config_store.save({"loader": "fabric", "version": "1.21.1"})
    mods_dir = os.path.join(common_module._server_dir_for_config(common_module._effective_config()), "mods")
    os.makedirs(mods_dir, exist_ok=True)
    open(os.path.join(mods_dir, "sodium.jar"), "wb").close()
    info, token = _enable_status_page()
    invite = info["invite"]
    assert "Address: friends.joinmc.link" in invite
    assert "Version: Fabric 1.21.1 (mods required" in invite
    assert f"Status page: {info['url']}" in invite
    assert client.get(f"/s/{token}/status.json").json()["mods_required"] is True


def test_invite_card_is_on_overview_and_remote():
    for section in ("overview", "remote"):
        text = client.get(f"/dashboard/{section}").text
        for element_id in ("invite-copy-button", "invite-text", "status-page-enabled", "status-page-regenerate"):
            assert f'id="{element_id}"' in text, (section, element_id)


def test_new_link_email_includes_status_page_link(fake_cloudflared, sent_emails, offline_network):
    _, token = _enable_status_page()
    client.post("/api/remote-dashboard", auth=AUTH, json={"enabled": True})
    assert _wait_until(lambda: any("New dashboard link" in s for s, _ in sent_emails))
    link_mail = next(body for subject, body in sent_emails if "New dashboard link" in subject)
    assert f"https://demo-1.trycloudflare.com/s/{token}" in link_mail
    assert client.get("/api/status-page", auth=AUTH).json()["url"] == f"https://demo-1.trycloudflare.com/s/{token}"
