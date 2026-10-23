"""Temperatures and device health alerts: high CPU / RAM use, too hot (email, Discord or both)."""
from support import *  # noqa: F401,F403

import subprocess

from core import temperatures


@pytest.fixture
def readings(monkeypatch):
    """Fake device state: set cpu / ram (percent) / temps, then call tick()."""
    now = {"cpu": 10.0, "ram": 20.0, "temps": []}
    monkeypatch.setattr(common_module, "_cpu_usage", lambda last=None: (now["cpu"], "system"))
    monkeypatch.setattr(common_module, "_memory_usage", lambda: (int(now["ram"] * 10), 1000))
    monkeypatch.setattr(temperatures, "read", lambda: now["temps"])
    return now


def tick():
    app.state.services.health_tick()


def _subjects(posts):
    return [content.split("\n", 1)[0].strip("*") for _, content in posts]


def test_temperatures_from_sys_thermal_zones_and_battery(monkeypatch, tmp_path):
    monkeypatch.setattr(temperatures, "psutil", None)
    monkeypatch.setattr(temperatures, "SYS_DIR", str(tmp_path))
    for name, kind, value in (("thermal_zone0", "cpu-0", "52000"), ("thermal_zone1", "unused", "0"),
                              ("thermal_zone2", "battery", "38500")):
        zone = tmp_path / "class" / "thermal" / name
        zone.mkdir(parents=True)
        (zone / "type").write_text(kind, encoding="utf-8")
        (zone / "temp").write_text(value, encoding="utf-8")
    supply = tmp_path / "class" / "power_supply" / "battery"
    supply.mkdir(parents=True)
    (supply / "temp").write_text("391", encoding="utf-8")  # tenths of a degree
    found = temperatures.read()
    assert [(r["label"], r["celsius"], r["kind"]) for r in found] == [
        ("cpu-0", 52.0, "device"), ("Battery", 39.1, "battery"), ("battery", 38.5, "battery")]
    assert temperatures.hottest(found, "battery")["celsius"] == 39.1


def test_temperatures_fall_back_to_termux_battery_status(monkeypatch, tmp_path):
    monkeypatch.setattr(temperatures, "psutil", None)
    monkeypatch.setattr(temperatures, "SYS_DIR", str(tmp_path))  # Android: /sys hidden
    monkeypatch.setattr(temperatures, "_termux_cache", {"at": -1e9, "readings": []})
    monkeypatch.setattr(temperatures.shutil, "which", lambda name: "/data/termux-battery-status")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"percentage": 80, "temperature": 41.3}', stderr="")

    monkeypatch.setattr(temperatures.subprocess, "run", fake_run)
    assert temperatures.read() == [{"label": "Battery", "celsius": 41.3, "kind": "battery"}]
    temperatures.read()
    assert len(calls) == 1  # cached: the command is slow


def test_metrics_include_temperatures(readings):
    readings["temps"] = [{"label": "cpu-0", "celsius": 55.0, "kind": "device"}]
    payload = client.get("/api/system/metrics", auth=AUTH).json()
    assert payload["temperatures"] == readings["temps"]


def test_high_cpu_alerts_once_after_the_set_minutes_then_recovers(readings, discord_posts):
    _hooks(alert_usage_minutes=3, alert_cpu_percent=85)
    readings["cpu"] = 95.0
    tick()
    tick()
    time.sleep(0.1)
    assert discord_posts == []  # only 2 of 3 minutes
    tick()
    assert _wait_until(lambda: len(discord_posts) == 1)
    assert "CPU use is high: 95%" in discord_posts[0][1] and discord_posts[0][0] == OWNER_HOOK
    tick()
    readings["cpu"] = 83.0  # below the limit but within the margin: not "back to normal" yet
    tick()
    time.sleep(0.1)
    assert len(discord_posts) == 1
    readings["cpu"] = 40.0
    tick()
    assert _wait_until(lambda: len(discord_posts) == 2)
    assert "CPU use is back to normal" in discord_posts[1][1]


def test_high_memory_and_heat_alert(readings, discord_posts):
    _hooks(alert_usage_minutes=1, alert_temp_c=70, alert_battery_c=42)
    readings["ram"] = 97.0
    readings["temps"] = [{"label": "cpu-0", "celsius": 75.0, "kind": "device"},
                         {"label": "Battery", "celsius": 44.0, "kind": "battery"}]
    tick()
    assert _wait_until(lambda: len(discord_posts) == 1)
    assert "Memory use is high: 97%" in discord_posts[0][1]
    tick()  # temperatures need two hot checks in a row
    assert _wait_until(lambda: len(discord_posts) == 3)
    subjects = " | ".join(_subjects(discord_posts))
    assert "Device is too hot: 75 °C" in subjects and "Battery is too hot: 44 °C" in subjects


def test_alert_channels_and_switches(readings, discord_posts, sent_emails):
    _hooks(alert_usage_minutes=1, channels_usage="email", channels_temperature="discord")
    readings["cpu"] = 99.0
    readings["temps"] = [{"label": "cpu-0", "celsius": 95.0, "kind": "device"}]
    tick()
    tick()
    assert _wait_until(lambda: len(sent_emails) == 1 and len(discord_posts) == 1)
    assert "CPU use is high" in sent_emails[0][0]  # usage: email only
    assert "Device is too hot" in discord_posts[0][1]  # temperature: Discord only
    time.sleep(0.1)
    assert len(sent_emails) == 1 and len(discord_posts) == 1

    app.state.services.reset_health()
    client.put("/api/notifications", auth=AUTH, json={"notify_usage": False, "notify_temperature": False})
    tick()
    tick()
    time.sleep(0.1)
    assert len(sent_emails) == 1 and len(discord_posts) == 1  # both switched off


def test_alert_settings_are_validated():
    bad = (("channels_usage", "sms"), ("alert_cpu_percent", 150), ("alert_temp_c", "hot"), ("alert_usage_minutes", 0))
    for key, value in bad:
        assert client.put("/api/notifications", auth=AUTH, json={key: value}).status_code == 400, key
    saved = client.put("/api/notifications", auth=AUTH, json={"channels_temperature": "email", "alert_battery_c": "40"})
    assert saved.json()["channels_temperature"] == "email" and saved.json()["alert_battery_c"] == 40
    overview = client.get("/dashboard").text
    assert 'id="temp-chart"' in overview
    alerts = client.get("/dashboard/setup").text
    assert 'id="alerts-channels-usage"' in alerts and 'id="alerts-temp-limit"' in alerts
