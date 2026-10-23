"""Self-update from git."""
from support import *  # noqa: F401,F403


def test_updater_fast_forwards_to_new_commits(git_repos):
    from core import updater

    assert updater.status()["behind"] == 0
    _publish(git_repos, "feature.txt", "new", "Add feature")
    state = updater.status()
    assert state["behind"] == 1
    assert state["commits"][0]["subject"] == "Add feature"
    result = updater.apply(lambda line: None)
    assert result["updated"] is True
    assert os.path.exists(os.path.join(git_repos["app"], "feature.txt"))
    assert updater.status()["behind"] == 0
    assert updater.apply(lambda line: None) == {"updated": False}


def test_updater_refuses_when_there_are_local_changes(git_repos):
    from core import updater

    _publish(git_repos, "feature.txt", "new", "Add feature")
    with open(os.path.join(git_repos["app"], "requirements.txt"), "a", encoding="utf-8") as handle:
        handle.write("my-local-edit\n")
    assert updater.status()["local_changes"] == ["requirements.txt"]  # full name, nothing clipped
    with pytest.raises(RuntimeError, match="Local changes"):
        updater.apply(lambda line: None)
    assert not os.path.exists(os.path.join(git_repos["app"], "feature.txt"))


def test_updater_reinstalls_packages_and_rolls_back_on_failure(git_repos, monkeypatch):
    from core import updater

    installs = []
    monkeypatch.setattr(updater, "install_requirements", lambda log: installs.append("ok"))
    _publish(git_repos, "requirements.txt", "requests\nnewdep\n", "Add dependency")
    assert updater.apply(lambda line: None)["updated"] is True
    assert installs == ["ok"]

    def failing_install(log):
        raise RuntimeError("pip install failed")

    monkeypatch.setattr(updater, "install_requirements", failing_install)
    before = updater.status(fetch=False)["current"]["hash"]
    _publish(git_repos, "requirements.txt", "requests\nnewdep\nbroken\n", "Break install")
    with pytest.raises(RuntimeError, match="pip install failed"):
        updater.apply(lambda line: None)
    assert updater.status(fetch=False)["current"]["hash"] == before  # rolled back


def test_updater_reports_when_not_a_git_clone(tmp_path, monkeypatch):
    from core import updater

    monkeypatch.setattr(updater, "REPO_DIR", str(tmp_path))
    state = updater.status()
    assert state["available"] is False
    assert "not a git clone" in state["reason"]


def test_update_endpoint_restarts_and_resumes(fake_server, fake_playit, fake_cloudflared, monkeypatch):
    from core import updater

    restarts = []
    monkeypatch.setattr(updater, "apply", lambda log: {"updated": True, "from": "aaaaaaa", "to": "bbbbbbb", "changed_files": []})
    monkeypatch.setattr(updater, "restart_process", lambda: restarts.append("restart"))
    _start()

    response = client.post("/api/update/apply", auth=("admin", "s3cret-pass"))
    assert response.json()["state"] == "updating"
    assert _wait_until(lambda: restarts == ["restart"], timeout=5)
    assert not fake_server[0].is_running()  # stopped cleanly before the restart
    assert app.state.runtime.update_state == "restarting"

    # The new process reads the marker and brings the server back.
    app.state.runtime.shutting_down = False
    app.state.runtime.update_state = "idle"
    monkeypatch.delenv("EZ_NO_AUTOSTART", raising=False)
    app.state.autostart()
    assert _wait_until(lambda: len(fake_server) == 2 and fake_server[1].is_running())
    assert not os.path.exists(os.path.join(paths.DATA_DIR, "resume_after_update.json"))


def test_update_failure_is_reported(monkeypatch):
    from core import updater

    def failing_apply(log):
        raise RuntimeError("Local changes would be overwritten (core/app.py).")

    monkeypatch.setattr(updater, "apply", failing_apply)
    app.state.runtime.update_state = "idle"
    client.post("/api/update/apply", auth=("admin", "s3cret-pass"))
    assert _wait_until(lambda: app.state.runtime.update_state == "error")
    payload = client.get("/api/update", auth=("admin", "s3cret-pass")).json()
    assert "Local changes" in payload["error"]
    app.state.runtime.update_state = "idle"
    app.state.runtime.update_error = ""


def test_dashboard_assets_are_cache_busted():
    text = client.get("/dashboard/setup").text
    for name in layout_module.SCRIPTS:
        assert f"/js/{name}?v={layout_module.ASSET_VERSION}" in text
    assert 'id="update-apply-button"' in text
