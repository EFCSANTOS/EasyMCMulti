"""Deep sleep on phones: wake lock kept, sleeps noticed and alerted, battery setting opened."""
from support import *  # noqa: F401,F403

from core import hostenv, power


@pytest.fixture
def phone(monkeypatch):
    """A fake Termux phone: set clock["asleep"] (seconds suspended since boot), count wake locks."""
    clock = {"asleep": 100.0}
    wake_locks = []
    monkeypatch.setattr(power, "can_detect_sleep", lambda: True)
    monkeypatch.setattr(power, "asleep_total", lambda: clock["asleep"])
    monkeypatch.setattr(power, "acquire_wake_lock", lambda: wake_locks.append(1) or True)
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(common_module, "SLEEP_ALERT_DELAY_SEC", 0)
    return clock, wake_locks


def tick():
    app.state.services.power_tick()


def test_sleep_watch_measures_time_asleep_between_checks():
    readings = iter([5.0, 5.0, 65.5, 60.0])
    watch = power.SleepWatch(read=lambda: next(readings))
    assert [watch.check() for _ in range(4)] == [0.0, 0.0, 60.5, 0.0]


def test_deep_sleep_is_shown_alerted_and_the_wake_lock_taken_again(phone, discord_posts):
    clock, wake_locks = phone
    _hooks()
    tick()
    assert len(wake_locks) == 1  # taken right away, then every few minutes
    tick()
    assert len(wake_locks) == 1
    clock["asleep"] += 600  # ten minutes of deep sleep before this check
    tick()
    assert len(wake_locks) == 2  # taken again right after waking
    assert _wait_until(lambda: len(discord_posts) == 1)
    assert "The phone fell asleep" in discord_posts[0][1] and "10 minute(s)" in discord_posts[0][1]
    assert "Unrestricted" in discord_posts[0][1]

    sleep = client.get("/api/status", auth=AUTH).json()["device_sleep"]
    assert sleep["count"] == 1 and sleep["last_seconds"] == 600 and sleep["phone"] is True
    assert 'id="sleep-warning"' in client.get("/dashboard").text
    assert client.post("/api/power/dismiss", auth=AUTH).json()["count"] == 0


def test_short_naps_are_ignored(phone, discord_posts):
    clock, _ = phone
    _hooks()
    tick()
    clock["asleep"] += 20
    tick()
    time.sleep(0.1)
    assert discord_posts == [] and client.get("/api/power", auth=AUTH).json()["count"] == 0


def test_battery_setting_opens_only_on_a_phone(monkeypatch):
    monkeypatch.setattr(hostenv, "is_termux", lambda: False)
    assert client.post("/api/power/battery-settings", auth=AUTH).status_code == 400
    monkeypatch.setattr(hostenv, "is_termux", lambda: True)
    monkeypatch.setattr(power, "open_battery_settings", lambda: False)
    assert "ezmc battery" in client.post("/api/power/battery-settings", auth=AUTH).json()["detail"]
    monkeypatch.setattr(power, "open_battery_settings", lambda: True)
    response = client.post("/api/power/battery-settings", auth=AUTH)
    assert response.status_code == 200 and "Unrestricted" in response.json()["message"]


@pytest.mark.skipif(not power.can_detect_sleep(), reason="needs CLOCK_BOOTTIME (Linux / Android)")
def test_real_clocks_give_a_sleep_total():
    assert power.asleep_total() >= 0
