"""Dashboard pages, assets and the JS↔API contract."""
from support import *  # noqa: F401,F403


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_serves_html():
    response = client.get("/", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    assert "EZ Minecraft Server" in response.text
    assert "Dashboard" in response.text


def test_remote_page_mentions_playit_support():
    response = client.get("/dashboard/remote")
    assert response.status_code == 200
    assert "PlayIt" in response.text


def test_server_controls_are_on_every_page():
    for section in ("overview", "setup", "advanced", "mods", "remote", "backups", "console"):
        text = client.get(f"/dashboard/{section}").text
        for control in ('id="start-button"', 'id="stop-button"', 'id="restart-button"', 'id="server-state"'):
            assert control in text, (section, control)


def test_management_features_live_on_their_pages():
    expectations = {
        "overview": ('id="backup-button"', 'id="metrics"', 'id="console-output"'),
        "mods": ('id="mod-search-form"', 'id="mod-upload-input"', 'id="mod-installed-list"'),
        "backups": ('id="backup-button"', 'id="backup-list"'),
        "console": ('id="console-output"', 'id="command-form"', 'id="admin-op"', 'id="admin-save"'),
        "remote": ('id="playit-config-button"', 'id="playit-start-button"', 'id="playit-stop-button"'),
    }
    for section, ids in expectations.items():
        text = client.get(f"/dashboard/{section}").text
        for element_id in ids:
            assert element_id in text, (section, element_id)


def test_dashboard_sections_have_their_own_pages():
    for section in ("overview", "setup", "advanced", "mods", "remote", "backups", "console"):
        response = client.get(f"/dashboard/{section}", auth=("admin", "s3cret-pass"))
        assert response.status_code == 200
        assert "EZ Minecraft" in response.text


def test_dashboard_does_not_embed_credentials():
    response = client.get("/")
    assert response.status_code == 200
    assert 'type="password"' not in response.text
    assert 'value="admin"' not in response.text


def test_every_dashboard_page_has_action_status_bar():
    for section in ("overview", "setup", "advanced", "mods", "remote", "backups", "console"):
        response = client.get(f"/dashboard/{section}")
        assert 'id="action-status"' in response.text


def test_advanced_page_has_save_control():
    response = client.get("/dashboard/advanced")
    assert 'id="advanced-save-button"' in response.text


def test_dashboard_js_only_calls_existing_api_routes():
    source = _dashboard_js()
    route_paths = {route.path for route in app.routes}
    called = set(re.findall(r"['`](/api/[^'`?]*)", source))
    assert called, "expected dashboard.js to call the API"
    for url in called:
        if "${" in url:
            prefix = url.split("${", 1)[0]
            assert any(path.startswith(prefix) and "{" in path for path in route_paths), url
        else:
            assert url in route_paths, url


def test_always_on_settings_render_on_setup_page():
    text = client.get("/dashboard/setup").text
    for element_id in ("autostart-server", "auto-restart", "autostart-playit", "always-on-save-button"):
        assert f'id="{element_id}"' in text


def test_installable_app_manifest_and_icons():
    response = client.get("/manifest.webmanifest")  # public, like the other shells
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/dashboard/overview"
    sizes = {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in manifest["icons"]:
        png = client.get(icon["src"])
        assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
        width = int.from_bytes(png.content[16:20], "big")
        assert f"{width}x{width}" == icon["sizes"]
    assert client.get("/icons/../app.py").status_code == 404
    for path in ("/login", "/dashboard/overview", "/dashboard/remote"):
        text = client.get(path).text
        assert '<link rel="manifest" href="/manifest.webmanifest" />' in text and 'apple-touch-icon' in text, path
