"""Java selection, Termux installs and platform-specific launch details."""
from support import *  # noqa: F401,F403


def test_system_java_prefers_exact_then_oldest_newer(monkeypatch):
    from core import java_manager

    versions = {"/jvm/17/bin/java": 17, "/jvm/21/bin/java": 21, "/jvm/25/bin/java": 25}
    monkeypatch.setattr(java_manager, "_system_java_candidates", lambda: list(versions))
    monkeypatch.setattr(java_manager, "java_major", lambda exe: versions[exe])
    assert java_manager.find_system_java(21) == ("/jvm/21/bin/java", 21)
    assert java_manager.find_system_java(8) == ("/jvm/17/bin/java", 17)
    assert java_manager.find_system_java(26) == (None, None)


def test_missing_java_on_termux_is_installed_automatically(monkeypatch):
    from core import hostenv, java_manager

    installed = {}
    monkeypatch.setattr(hostenv, "is_windows", lambda: False)
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(java_manager, "_system_java_candidates", lambda: list(installed))
    monkeypatch.setattr(java_manager, "java_major", lambda exe: installed[exe])

    def fake_install(package, log=print, timeout_sec=1800):
        installed[f"/jvm/{package}/bin/java"] = int(package.split("-")[1])

    monkeypatch.setattr(hostenv, "termux_install", fake_install)
    assert java_manager.ensure_java(25, log=lambda message: None) == "/jvm/openjdk-25/bin/java"
    assert java_manager.termux_java_package(18) == "openjdk-21"
    installed.clear()  # nothing installed: Java 8 has no Termux package
    with pytest.raises(RuntimeError, match="Choose Minecraft 1.17 or newer"):
        java_manager.ensure_java(8, log=lambda message: None)


def test_failed_java_install_on_termux_explains_how_to_install(monkeypatch):
    from core import hostenv, java_manager

    monkeypatch.setattr(hostenv, "is_windows", lambda: False)
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(java_manager, "_system_java_candidates", lambda: [])
    monkeypatch.setattr(hostenv, "_apt", lambda args, log, timeout_sec: (100, ["E: Unable to locate package openjdk-21"]))
    with pytest.raises(RuntimeError, match="pkg install openjdk-21"):
        java_manager.ensure_java(21, log=lambda message: None)


def test_termux_install_refreshes_package_lists_and_retries(monkeypatch):
    from core import hostenv

    calls = []

    def fake_apt(args, log, timeout_sec):
        calls.append(args)
        return (0 if calls.count(["install", "cloudflared"]) == 2 else 100), []

    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(hostenv, "_apt", fake_apt)
    hostenv.termux_install("cloudflared", log=lambda message: None)
    assert calls == [["install", "cloudflared"], ["update"], ["install", "cloudflared"]]


def test_forge_args_file_matches_platform(tmp_path, monkeypatch):
    from core import downloaders

    args_dir = tmp_path / "libraries" / "net" / "forge"
    args_dir.mkdir(parents=True)
    (args_dir / "unix_args.txt").write_text("")
    monkeypatch.setattr(downloaders.sys, "platform", "linux")
    assert downloaders._find_args_file(str(tmp_path)).endswith("unix_args.txt")
    monkeypatch.setattr(downloaders.sys, "platform", "win32")
    assert downloaders._find_args_file(str(tmp_path)) is None
