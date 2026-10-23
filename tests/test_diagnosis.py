"""Crash diagnosis and the log viewer (FEAT-08)."""
from support import *  # noqa: F401,F403


def test_diagnosis_recognises_real_failures():
    fabric = _diagnose_fixture("fabric_missing_dependency.log")
    assert [(d["title"], d["action"]) for d in fabric if d["code"] == "missing_dependency"] == [
        ("Sodium Extra needs Sodium", {"kind": "install_mod", "slug": "sodium"}),
        ("Mod Menu needs fabric-api", {"kind": "install_mod", "slug": "fabric-api"})]
    assert fabric[-1]["code"] == "incompatible_mods"
    forge = _diagnose_fixture("forge_missing_dependency.log")
    assert forge[0]["title"] == "roughlyenoughitems needs architectury" and "not installed" in forge[0]["message"]
    assert "47.0.19 is installed" in forge[1]["message"]
    java = _diagnose_fixture("java_version.log")[0]
    assert java["code"] == "wrong_java" and "needs Java 21, but Java 17" in java["message"]
    assert _diagnose_fixture("out_of_memory.log")[0]["action"] == {"kind": "open_setting", "page": "setup",
                                                                   "field": "ram-input"}
    port = _diagnose_fixture("port_in_use.log")[0]
    assert port["title"] == "Port 25565 is already in use" and port["action"] == {"kind": "kill_process", "port": 25565}
    client_only = _diagnose_fixture("client_only_mod.log")[0]
    assert client_only["action"] == {"kind": "remove_mod", "file": "oculus-mc1.20.1-1.6.9.jar"}
    assert _diagnose_fixture("eula.log")[0]["action"] == {"kind": "accept_eula"}
    assert _diagnose_fixture("world_locked.log")[0]["code"] == "world_locked"
    assert diagnose.diagnose(["[12:00:00] [Server thread/INFO]: Done (3.2s)!"]) == []


def test_crash_is_diagnosed_and_explained_in_the_alert(fake_server, sent_emails):
    config_store.save({"auto_restart": False})  # keep the crashed state to inspect it
    _start()
    lines = open(os.path.join(LOG_FIXTURES, "fabric_missing_dependency.log"), encoding="utf-8").read().splitlines()
    for line in lines:
        app.state.services.append_console_line(line)
    fake_server[0].exit(1)
    diagnosis_items = client.get("/api/server/diagnosis", auth=AUTH).json()["items"]
    assert diagnosis_items[0]["title"] == "Sodium Extra needs Sodium"
    assert client.get("/api/status", auth=AUTH).json()["diagnosis_at"] > 0
    assert _wait_until(lambda: any("Likely cause: Sodium Extra needs Sodium" in body for _, body in sent_emails))
    _start()  # a new start clears the old diagnosis
    assert client.get("/api/server/diagnosis", auth=AUTH).json()["items"] == []


def test_diagnosis_fixes_eula_and_only_kills_java(monkeypatch):
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    assert client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "accept_eula"}).status_code == 200
    assert "eula=true" in open(os.path.join(sdir, "eula.txt"), encoding="utf-8").read()
    terminated = []

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return {111: "java.exe", 222: "chrome.exe"}[self.pid]

        def terminate(self):
            terminated.append(self.pid)

    class Conn:
        def __init__(self, port, pid):
            self.laddr, self.status, self.pid = type("A", (), {"port": port})(), "LISTEN", pid

    fake_psutil = type("P", (), {"Error": Exception, "Process": FakeProcess,
                                 "net_connections": staticmethod(lambda kind: [Conn(25565, 111), Conn(8080, 222)])})
    monkeypatch.setattr(common_module, "psutil", fake_psutil)
    assert client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "kill_process", "port": 25565}).status_code == 200
    refused = client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "kill_process", "port": 8080})
    assert refused.status_code == 409 and "chrome.exe" in refused.json()["detail"]
    assert terminated == [111]
    assert client.post("/api/server/diagnosis/fix", auth=AUTH, json={"kind": "kill_process", "port": 1}).status_code == 404


def test_log_viewer_lists_reads_gz_and_refuses_traversal():
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    os.makedirs(os.path.join(sdir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(sdir, "crash-reports"), exist_ok=True)
    with open(os.path.join(sdir, "logs", "latest.log"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(f"line {i}" for i in range(2500)))
    with gzip.open(os.path.join(sdir, "logs", "2026-10-21-1.log.gz"), "wt", encoding="utf-8") as handle:
        handle.write("old line\n")
    with open(os.path.join(sdir, "crash-reports", "crash-2026-10-22_12.00.00-server.txt"), "w", encoding="utf-8") as handle:
        handle.write("---- Minecraft Crash Report ----\n")
    files = client.get("/api/server/logs", auth=AUTH).json()["files"]
    assert {(f["folder"], f["name"]) for f in files} == {("logs", "latest.log"), ("logs", "2026-10-21-1.log.gz"),
                                                         ("crash-reports", "crash-2026-10-22_12.00.00-server.txt")}
    latest = client.get("/api/server/logs/logs/latest.log", auth=AUTH).json()
    assert latest["truncated"] and len(latest["lines"]) == 2000 and latest["lines"][-1] == "line 2499"
    assert client.get("/api/server/logs/logs/2026-10-21-1.log.gz", auth=AUTH).json()["lines"] == ["old line"]
    download = client.get("/api/server/logs/logs/latest.log?download=true", auth=AUTH)
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    for bad in ("/api/server/logs/logs/..%2F..%2Flauncher_config.json", "/api/server/logs/world/level.dat",
                "/api/server/logs/logs/eula.txt.bak"):
        assert client.get(bad, auth=AUTH).status_code in (400, 404), bad
    assert client.get("/api/server/logs/logs/missing.log", auth=AUTH).status_code == 404


def test_diagnosis_and_logs_cards_render():
    console = client.get("/dashboard/console").text
    for element_id in ("diagnosis", "diagnosis-list", "log-list", "log-view"):
        assert f'id="{element_id}"' in console
    assert 'id="diagnosis"' in client.get("/dashboard/overview").text
