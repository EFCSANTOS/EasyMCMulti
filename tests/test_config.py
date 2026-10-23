"""Status, metrics, config validation, atomic writes, performance and server properties."""
from support import *  # noqa: F401,F403


def test_status_endpoint_returns_state_when_authenticated():
    response = client.get("/api/status", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "state" in payload
    assert "loader" in payload
    assert "version" in payload
    assert "lan_address" in payload
    assert "public_address" in payload
    assert "playit" in payload


def test_system_metrics_endpoint_returns_cpu_and_ram():
    response = client.get("/api/system/metrics", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    payload = response.json()
    assert "cpu" in payload
    assert "ram" in payload
    assert "ramTotal" in payload
    assert 0 <= payload["cpu"] <= 100
    assert payload["ram"] >= 0
    assert payload["ramTotal"] > 0


def test_metrics_survive_blocked_cpu_counters(monkeypatch):
    class BlockedPsutil:
        @staticmethod
        def cpu_percent(interval=None):
            raise PermissionError("/proc/stat")

        @staticmethod
        def virtual_memory():
            class Mem:
                used = 1024
                total = 4096
            return Mem()

    monkeypatch.setattr(common_module, "psutil", BlockedPsutil)
    monkeypatch.setattr(common_module, "PROC_DIR", "/nonexistent-proc")
    snapshot = common_module._system_metrics_snapshot()
    assert snapshot["cpu"] == 0.0 and snapshot["cpuScope"] == "unavailable"
    assert snapshot["ram"] == 1024
    assert snapshot["ramTotal"] == 4096


def test_cpu_without_psutil_reads_proc_stat(monkeypatch, tmp_path):
    monkeypatch.setattr(common_module, "psutil", None)
    monkeypatch.setattr(common_module, "PROC_DIR", str(tmp_path))
    monkeypatch.setattr(common_module, "_cpu_last", {})
    stat = tmp_path / "stat"
    stat.write_text("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n", encoding="utf-8")
    assert common_module._cpu_usage() == (0.0, "system")  # first reading
    stat.write_text("cpu  150 0 150 900 0 0 0 0 0 0\n", encoding="utf-8")  # 100 busy of 200
    assert common_module._cpu_usage() == (50.0, "system")


def test_cpu_on_android_counts_our_own_processes(monkeypatch, tmp_path):
    # Android: no psutil, /proc/stat blocked, but /proc/<pid>/stat of Termux processes readable.
    monkeypatch.setattr(common_module, "psutil", None)
    monkeypatch.setattr(common_module, "PROC_DIR", str(tmp_path))
    monkeypatch.setattr(common_module, "_cpu_last", {})
    monkeypatch.setattr(common_module, "_clock_ticks", lambda: 100)
    monkeypatch.setattr(common_module.os, "cpu_count", lambda: 4)
    clock = [1000.0]
    monkeypatch.setattr(common_module.time, "monotonic", lambda: clock[0])
    for pid in ("1", "2"):
        (tmp_path / pid).mkdir()
    (tmp_path / "self").mkdir()

    def write(java_ticks, python_ticks):
        rest = "S 1 1 1 0 -1 0 0 0 0 0"
        (tmp_path / "1" / "stat").write_text(f"1 (java (server)) {rest} {java_ticks} 0 0 0", encoding="utf-8")
        (tmp_path / "2" / "stat").write_text(f"2 (python) {rest} {python_ticks} 10 0 0", encoding="utf-8")

    write(1000, 90)
    assert common_module._cpu_usage() == (0.0, "processes")
    clock[0] += 2  # 2 s on 4 cores = 800 ticks; 200 + 0 used
    write(1200, 90)
    assert common_module._cpu_usage() == (25.0, "processes")


def test_config_can_persist_values():
    response = client.put(
        "/api/config",
        auth=("admin", "s3cret-pass"),
        json={"loader": "vanilla", "version": "1.21.7", "ram_mb": 2048},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["loader"] == "vanilla"
    assert payload["version"] == "1.21.7"
    assert payload["ram_mb"] == 2048


def test_versions_endpoint_accepts_known_loader():
    response = client.get("/api/versions/vanilla", auth=("admin", "s3cret-pass"))
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "loader" in data
    assert "versions" in data
    assert data["loader"] == "vanilla"


def test_config_save_is_atomic_and_keeps_a_backup():
    config_store.save({"loader": "paper", "version": "1.21.4"})
    config_store.save({"loader": "fabric", "version": "1.21.4"})
    path = config_store._FILE
    assert config_store.load()["loader"] == "fabric"
    assert json.load(open(path + ".bak", encoding="utf-8"))["loader"] == "paper"
    assert not os.path.exists(path + ".tmp")


def test_damaged_config_is_restored_from_backup_and_logged():
    config_store.save({"loader": "paper", "version": "1.21.4"})
    config_store.save({"loader": "paper", "version": "1.21.4", "motd": "x"})
    with open(config_store._FILE, "w", encoding="utf-8") as handle:
        handle.write('{"loader": "pap')  # power cut mid-write (pre-FIX-03 style)
    app.state.console_manager.history.clear()
    assert config_store.load()["loader"] == "paper"
    assert json.load(open(config_store._FILE, encoding="utf-8"))["loader"] == "paper"  # repaired on disk
    lines = client.get("/api/console", auth=("admin", "s3cret-pass")).json()["lines"]
    assert any("launcher_config.json was damaged; restored the previous copy" in line for line in lines)


def test_failed_write_leaves_the_original_intact(monkeypatch):
    config_store.save({"loader": "paper"})

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch, pytest.raises(OSError):
        patch.setattr(fsutil.os, "replace", broken_replace)
        config_store.save({"loader": "fabric"})
    assert config_store.load()["loader"] == "paper"
    assert not os.path.exists(config_store._FILE + ".tmp")


def test_put_config_reports_disk_errors(monkeypatch):
    def full_disk(data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config_store, "save", full_disk)
    response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={"motd": "hi"})
    assert response.status_code == 500
    assert "disk full" in response.json()["detail"]


def test_missing_file_without_backup_is_quiet(tmp_path):
    app.state.console_manager.history.clear()
    assert fsutil.read_json(str(tmp_path / "nothing.json"), {"a": 1}) == {"a": 1}
    assert not any("damaged" in line for line in app.state.console_manager.history)


def test_atomic_writes_cover_every_settings_file(tmp_path):
    notifier.save_config({"email_to": "me@example.com"})
    notifier.save_config({"email_to": "you@example.com"})
    playit.save_config("ab12cd34ef", "a.example")
    playit.save_config("ab12cd34ef", "b.example")
    from core import modrinth, server_process, worlds
    worlds._save_meta(str(tmp_path), {"world": {"name": "One"}})
    worlds._save_meta(str(tmp_path), {"world": {"name": "Two"}})
    modrinth._save_record(str(tmp_path), {"p1": {"filename": "a.jar"}})
    modrinth._save_record(str(tmp_path), {"p1": {"filename": "b.jar"}})
    server_process.update_properties(str(tmp_path), motd="one")
    server_process.update_properties(str(tmp_path), motd="two")
    for path in (notifier._CONFIG_FILE, playit._CONFIG_FILE, str(tmp_path / worlds.META_FILE),
                 str(tmp_path / modrinth.RECORD_FILE), str(tmp_path / "server.properties")):
        assert os.path.exists(path + ".bak"), path
        assert not os.path.exists(path + ".tmp"), path
    assert server_process.read_properties(str(tmp_path))["motd"] == "two"
    assert worlds.load_meta(str(tmp_path))["world"]["name"] == "Two"


def test_player_names_with_newlines_are_rejected(fake_server):
    _start()
    for bad in ("x\nstop", "a b", "ab", "x" * 17, "Steve;op", ""):
        response = client.post("/api/admin/kick", auth=("admin", "s3cret-pass"), json={"player": bad})
        assert response.status_code == 400, bad
    assert fake_server[0].sent == []
    for good in ("Steve", "Alex_99", ".BedrockGuy"):
        assert client.post("/api/admin/op", auth=("admin", "s3cret-pass"), json={"player": good}).status_code == 200
    assert fake_server[0].sent == ["op Steve", "op Alex_99", "op .BedrockGuy"]


def test_console_commands_must_be_one_line(fake_server):
    _start()
    for bad in ("say hi\nstop", "say hi\rstop", "say \x00", "say " + "x" * 300):
        response = client.post("/api/server/command", auth=("admin", "s3cret-pass"), json={"command": bad})
        assert response.status_code == 400, repr(bad)
    assert fake_server[0].sent == []
    assert client.post("/api/server/command", auth=("admin", "s3cret-pass"),
                       json={"command": "say hello"}).status_code == 200


def test_config_rejects_bad_values_naming_the_field():
    cases = {"ram_mb": "lots", "server_port": 80, "max_players": -1, "view_distance": 99,
             "gamemode": "god", "motd": "line one\nline two", "version": "../../etc", "level_name": "../x",
             "loader": "bukkit"}
    for key, value in cases.items():
        response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={key: value})
        assert response.status_code == 422, key
        detail = response.json()["detail"]
        assert detail[0]["loc"] == ["body", key] and key in detail[0]["msg"]
    response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={"surprise": 1})
    assert response.status_code == 422 and response.json()["detail"][0]["loc"] == ["body", "surprise"]
    assert config_store.load() == {}  # nothing was saved


def test_config_ram_is_capped_by_host_memory(monkeypatch):
    monkeypatch.setattr(common_module, "_host_ram_mb", lambda: 4096)
    response = client.put("/api/config", auth=("admin", "s3cret-pass"), json={"ram_mb": 8192})
    assert response.status_code == 422 and "4096" in response.json()["detail"][0]["msg"]
    assert client.put("/api/config", auth=("admin", "s3cret-pass"), json={"ram_mb": 3072}).status_code == 200


def test_start_payload_is_validated_too(fake_server):
    response = client.post("/api/server/start", auth=("admin", "s3cret-pass"), json={"server_port": "nope"})
    assert response.status_code == 422
    assert fake_server == []


def test_stored_garbage_falls_back_to_defaults(fake_server):
    config_store.save({"ram_mb": "lots", "server_port": 99999, "motd": "kept", "custom": "x"})
    app.state.console_manager.history.clear()
    config = client.get("/api/config", auth=("admin", "s3cret-pass")).json()
    assert config["ram_mb"] == 2048 and config["server_port"] == 25565
    assert config["motd"] == "kept" and config["custom"] == "x"
    assert any("Ignoring invalid saved value ram_mb='lots'" in line for line in app.state.console_manager.history)
    assert _start().status_code == 202
    assert app.state.runtime.state == "running"


def test_dashboard_highlights_rejected_fields():
    source = _dashboard_js()
    assert "markInvalidFields" in source and "aria-invalid" in source


def test_status_reflects_config_without_writing_runtime(offline_network):
    runtime = app.state.runtime
    runtime.loader, runtime.version, runtime.lan_address, runtime.public_address = "marker", "0", "unset", None
    config_store.save({"loader": "paper", "version": "1.21.4"})
    first = client.get("/api/status", auth=AUTH).json()
    config_store.save({"loader": "fabric", "version": "1.21.1"})
    second = client.get("/api/status", auth=AUTH).json()
    assert (first["loader"], first["version"]) == ("paper", "1.21.4")
    assert (second["loader"], second["version"]) == ("fabric", "1.21.1")
    assert second["lan_address"] == "192.168.1.50" and second["public_address"] == "203.0.113.9"
    assert second["playit"]["state"] in {"not-configured", "configured", "stopped", "running"}
    assert (runtime.loader, runtime.version, runtime.lan_address, runtime.public_address) == ("marker", "0", "unset", None)


def test_status_handler_has_no_runtime_assignments():
    source = open(os.path.join(os.path.dirname(webapp_module.__file__), "routes", "system.py"), encoding="utf-8").read()
    handler = source[source.index("def status_payload"):source.index('@app.get("/api/console")')]
    assert not re.search(r"runtime\.\w+\s*=[^=]", handler)


def test_jvm_flags_variants():
    normal = downloaders.jvm_flags(4096)
    assert "-XX:+UseG1GC" in normal and "-XX:+AlwaysPreTouch" in normal and "-XX:G1HeapRegionSize=8M" in normal
    assert "-XX:+AlwaysPreTouch" not in downloaders.jvm_flags(2048, is_phone=True)
    assert "-XX:G1HeapRegionSize=16M" in downloaders.jvm_flags(16384)
    args = downloaders.launch_args("vanilla", "java", "/srv", 2048, ["-XX:+UseG1GC"])
    assert args[:5] == ["java", "-Xms2048M", "-Xmx2048M", "-XX:+UseG1GC", "-jar"]


def test_launch_uses_flags_switch_and_extra_args(fake_server, monkeypatch):
    calls = []
    monkeypatch.setattr(downloaders, "launch_args", lambda loader, java, sdir, ram, flags=(): calls.append(list(flags)) or ["java"])
    config_store.save({"jvm_extra_args": "-XX:+UseStringDeduplication -Dmy.flag=yes"})
    _start()
    assert "-XX:+UseG1GC" in calls[-1] and calls[-1][-2:] == ["-XX:+UseStringDeduplication", "-Dmy.flag=yes"]
    client.post("/api/server/stop", auth=AUTH)
    fake_server[0].exit(0)
    config_store.save({"jvm_preset_flags": False, "jvm_extra_args": ""})
    _start()
    assert calls[-1] == []


def test_extra_jvm_args_are_validated():
    for bad in ("-jar evil.jar", "@argfile.txt", "-Xmx1G\nstop", "nogui", '-Dunterminated="x'):
        assert client.put("/api/config", auth=AUTH, json={"jvm_extra_args": bad}).status_code == 422, bad
    assert client.put("/api/config", auth=AUTH, json={"jvm_extra_args": "-XX:+UseStringDeduplication"}).status_code == 200


def test_host_info_and_performance_card():
    info = client.get("/api/system/host", auth=AUTH).json()
    assert set(info) == {"ram_mb", "phone"}
    text = client.get("/dashboard/setup").text
    for marker in ('data-preset="phone"', 'id="jvm-preset-flags"', 'id="ram-advice"', 'id="paper-pregen-hint"'):
        assert marker in text


def test_server_icon_upload_is_validated():
    assert client.get("/api/server/icon", auth=AUTH).status_code == 404
    upload = lambda data: client.post("/api/server/icon", auth=AUTH, files={"file": ("i.png", data, "image/png")})  # noqa: E731
    assert upload(_png(32, 32)).status_code == 400
    assert upload(b"GIF89a....").status_code == 400
    assert upload(_png(64, 64, padding=70_000)).status_code == 400
    assert upload(_png(64, 64)).status_code == 200
    assert client.get("/api/server/icon", auth=AUTH).content == _png(64, 64)
    assert client.delete("/api/server/icon", auth=AUTH).status_code == 200
    assert client.get("/api/server/icon", auth=AUTH).status_code == 404


def test_extra_properties_round_trip_and_managed_keys_are_refused():
    response = client.put("/api/server/properties", auth=AUTH, json={"spawn-protection": "0", "hardcore": True})
    assert response.status_code == 200
    rows = {row["key"]: row for row in response.json()["properties"]}
    assert rows["spawn-protection"]["value"] == "0" and rows["spawn-protection"]["custom"]
    assert rows["hardcore"]["value"] == "true" and rows["hardcore"]["type"] == "bool"
    assert rows["server-port"]["managed"] and rows["motd"]["managed"]
    props = common_module._server_properties_from_config(common_module._effective_config())
    assert props["spawn-protection"] == "0" and props["hardcore"] == "true"
    for bad in ({"server-port": "1"}, {"motd": "x"}, {"level-name": "../x"}, {"bad=key": "1"}, {"pvp": "false"},
                {"resource-pack": "line\nbreak"}):
        assert client.put("/api/server/properties", auth=AUTH, json=bad).status_code == 422, bad
    removed = client.put("/api/server/properties", auth=AUTH, json={"spawn-protection": None}).json()
    assert not {row["key"]: row for row in removed["properties"]}["spawn-protection"]["custom"]


def test_motd_colours_are_written_as_unicode_escapes():
    # The owner types "\n" (backslash + n) for the second line; it must reach the file unchanged.
    config_store.save({"motd": r"§aOur §lServer\n§7Come play"})
    props = common_module._server_properties_from_config(common_module._effective_config())
    assert props["motd"] == r"\u00a7aOur \u00a7lServer\n\u00a77Come play"
    assert client.put("/api/config", auth=AUTH, json={"motd": "§" * 120}).status_code == 200
    assert client.put("/api/config", auth=AUTH, json={"motd": "x" * 151}).status_code == 422


def test_managed_property_list_matches_the_settings_form():
    config = common_module._effective_config()
    config["extra_properties"] = {}
    assert set(common_module._server_properties_from_config(config)) == properties_meta.MANAGED
    text = client.get("/dashboard/advanced").text
    for element_id in ("server-icon-input", "motd-preview", "properties-list", "properties-save-button"):
        assert f'id="{element_id}"' in text
