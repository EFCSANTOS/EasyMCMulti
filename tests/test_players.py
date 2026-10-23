"""Players page: whitelist/ops/bans (FEAT-02) and activity (FEAT-12)."""
from support import *  # noqa: F401,F403


def test_stopped_server_whitelist_edits_json_with_offline_uuid():
    config_store.save({"online_mode": False})
    response = client.post("/api/players/whitelist", auth=AUTH, json={"action": "add", "name": "Notch"})
    assert response.status_code == 200 and response.json()["whitelist"] == [{"name": "Notch"}]
    entries = json.load(open(_server_file("whitelist.json"), encoding="utf-8"))
    assert entries == [{"uuid": "b50ad385-829d-3141-a216-7e7d7539ba7f", "name": "Notch"}]
    assert client.post("/api/players/whitelist", auth=AUTH, json={"action": "remove", "name": "notch"}).json()["whitelist"] == []
    assert client.post("/api/players/whitelist", auth=AUTH, json={"action": "remove", "name": "Notch"}).status_code == 404


def test_stopped_server_online_mode_resolves_uuid_from_mojang(monkeypatch):
    from core import players

    class FakeResponse:
        def __init__(self, status, data=None):
            self.status_code, self.ok, self._data = status, status == 200, data

        def json(self):
            return self._data

    def fake_get(url, timeout):
        if url.endswith("/steve"):
            return FakeResponse(200, {"id": "8667ba71b85a4004af54457a9734eed7", "name": "Steve"})
        return FakeResponse(404)

    monkeypatch.setattr(players.requests, "get", fake_get)
    result = client.post("/api/players/ops", auth=AUTH, json={"action": "add", "name": "steve", "level": 2}).json()
    assert result["ops"] == [{"name": "Steve", "level": 2}]
    entry = json.load(open(_server_file("ops.json"), encoding="utf-8"))[0]
    assert entry["uuid"] == "8667ba71-b85a-4004-af54-457a9734eed7" and entry["level"] == 2
    response = client.post("/api/players/whitelist", auth=AUTH, json={"action": "add", "name": "NoSuchGuy"})
    assert response.status_code == 400 and "no Minecraft account" in response.json()["detail"]


def test_running_server_changes_lists_with_commands(fake_server, monkeypatch):
    monkeypatch.setattr(players_route, "SYNC_WAIT_SEC", 0.05)
    _start()
    for kind, body in (("whitelist", {"action": "add", "name": "Steve"}), ("ops", {"action": "add", "name": "Alex"}),
                       ("bans", {"action": "add", "name": "Griefer", "reason": "broke the base"}),
                       ("bans", {"action": "remove", "name": "Griefer"}), ("ips", {"action": "remove", "ip": "10.0.0.7"}),
                       ("whitelist", {"action": "enable"})):
        assert client.post(f"/api/players/{kind}", auth=AUTH, json=body).status_code == 200, (kind, body)
    assert fake_server[0].sent == ["whitelist add Steve", "op Alex", "ban Griefer broke the base", "pardon Griefer",
                                   "pardon-ip 10.0.0.7", "whitelist on"]
    assert common_module._effective_config()["white_list"] is True


def test_player_list_input_is_validated():
    cases = [("whitelist", {"action": "add", "name": "x\nop me"}), ("whitelist", {"action": "add", "name": "ab"}),
             ("ops", {"action": "add", "name": "Steve", "level": 7}), ("ips", {"action": "remove", "ip": "999.1.1.1"}),
             ("bans", {"action": "add", "name": "Steve", "reason": "line\nbreak"}), ("whitelist", {"action": "nuke"})]
    for kind, body in cases:
        assert client.post(f"/api/players/{kind}", auth=AUTH, json=body).status_code == 400, body
    assert client.post("/api/players/admins", auth=AUTH, json={"action": "add", "name": "Steve"}).status_code == 404


def test_player_lists_wait_for_running_lifecycle_job():
    config_store.save({"online_mode": False})
    lock = app.state.runtime.lifecycle
    lock.acquire()
    try:
        response = client.post("/api/players/whitelist", auth=AUTH, json={"action": "add", "name": "Steve"})
        assert response.status_code == 409
    finally:
        lock.release()


def test_refused_players_show_up_as_recently_seen():
    runtime = app.state.runtime
    runtime.recent_players.clear()
    runtime.note_player("Stranger", rejected=True)
    runtime.track_player_event("[12:00:00] [Server thread/INFO]: Steve joined the game")
    recent = client.get("/api/players/lists", auth=AUTH).json()["recent"]
    assert [(p["name"], p["rejected"], p["whitelisted"]) for p in recent] == [("Steve", False, False),
                                                                              ("Stranger", True, False)]


def test_server_output_records_whitelist_refusals(fake_server, monkeypatch):
    captured = {}
    original = RecordingProcess.__init__

    def capture(self, args, cwd, on_output, on_exit):
        captured["on_output"] = on_output
        original(self, args, cwd, on_output, on_exit)

    monkeypatch.setattr(RecordingProcess, "__init__", capture)
    app.state.runtime.recent_players.clear()
    _start()
    captured["on_output"]("[12:00:00] [Server thread/INFO]: Disconnecting Bob_1 (/10.0.0.9:5000): "
                          "You are not white-listed on this server!")
    assert app.state.runtime.recent()[0]["name"] == "Bob_1" and app.state.runtime.recent()[0]["rejected"] is True


def test_players_page_renders():
    text = client.get("/dashboard/players").text
    for element_id in ("whitelist-add-form", "whitelist-list", "ops-add-form", "bans-list", "recent-players-list",
                       "players-whitelist-enabled"):
        assert f'id="{element_id}"' in text
    assert 'href="/dashboard/players"' in client.get("/dashboard/overview").text


def test_playtime_from_joins_leaves_and_stops():
    for event, name, offset in (("join", "Alex", 0), ("join", "Sam", 600), ("join", "Alex", 900),
                                ("leave", "Alex", 3600), ("server_stop", "", 7200), ("join", "Alex", 8000)):
        activity.record(event, name, t=T0 + offset)
    with open(os.path.join(paths.DATA_DIR, "activity.jsonl"), "a", encoding="utf-8") as handle:
        handle.write('{"t": 17600')  # cut short by a power loss
    data = activity.summary(now=T0 + 9000)
    rows = {row["name"]: row for row in data["players"]}
    assert rows["Alex"]["playtime_seconds"] == 3600 + 1000 and rows["Alex"]["sessions"] == 2
    assert rows["Alex"]["online"] is True and data["players"][0]["name"] == "Alex"
    assert rows["Sam"]["playtime_seconds"] == 6600 and rows["Sam"]["online"] is False  # the stop closed Sam
    assert abs(sum(data["hours"]) - (4600 + 6600) / (30 * 3600)) < 0.005  # hours are rounded to 3 decimals


def test_compaction_keeps_totals():
    year = activity.COMPACT_AFTER_SEC
    activity.record("join", "Old", t=T0 - year - 5000)
    activity.record("server_stop", t=T0 - year - 1000)
    activity.record("join", "Old", t=T0 - 100)
    activity.record("leave", "Old", t=T0)
    before = activity.summary(now=T0 + 10)["players"][0]
    assert activity.compact(now=T0 + 10) > 0
    after = activity.summary(now=T0 + 10)["players"][0]
    assert (after["playtime_seconds"], after["sessions"]) == (before["playtime_seconds"], before["sessions"]) == (4100, 2)
    assert "totals" in open(os.path.join(paths.DATA_DIR, "activity.jsonl"), encoding="utf-8").read()


def test_server_output_feeds_the_activity_log(fake_server, monkeypatch):
    captured = {}
    original = RecordingProcess.__init__

    def capture(self, args, cwd, on_output, on_exit):
        captured["on_output"] = on_output
        original(self, args, cwd, on_output, on_exit)

    monkeypatch.setattr(RecordingProcess, "__init__", capture)
    _start()
    captured["on_output"]("[12:00:00] [Server thread/INFO]: Alex joined the game")
    data = client.get("/api/players/activity", auth=AUTH).json()
    assert data["players"][0]["name"] == "Alex" and data["players"][0]["online"] is True
    fake_server[0].exit(1)  # crash mid-session
    data = client.get("/api/players/activity", auth=AUTH).json()
    assert data["players"][0]["online"] is False
    assert 'id="activity-chart"' in client.get("/dashboard/players").text
