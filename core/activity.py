"""Player activity log and playtime (FEAT-12).

Every join/leave is appended to EZ_Server_Data/activity.jsonl as {t, event, name, server_dir};
"server_stop" closes every open session (server stopped or crashed, or the dashboard restarted)
so time while the server was down is never counted. summary() replays the file sequentially:
at friend scale (a few thousand lines a year) no index is needed. compact() folds entries older
than a year into per-player "totals" lines.
"""
import json
import os
import threading
import time

from . import fsutil, paths

COMPACT_AFTER_SEC = 365 * 24 * 3600
_lock = threading.Lock()


def _path():
    return os.path.join(paths.DATA_DIR, "activity.jsonl")


def record(event, name="", server_dir="", t=None):
    """Append one event: "join", "leave" or "server_stop"."""
    entry = {"t": round(t if t is not None else time.time(), 1), "event": event}
    if name:
        entry["name"] = name
    if server_dir:
        entry["server_dir"] = os.path.basename(server_dir)
    with _lock:
        paths.ensure_dirs()
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def _events():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a line cut short by a power loss
        if isinstance(entry, dict) and "t" in entry and "event" in entry:
            events.append(entry)
    events.sort(key=lambda entry: entry["t"])  # appended in order already; this guards hand edits / clock jumps
    return events


def _replay(events, until, on_session=None):
    """Per-player stats; on_session(name, start, end) is called for each closed or still-open session."""
    players = {}
    open_sessions = {}

    def close(name, end):
        start = open_sessions.pop(name)
        stats = players[name]
        stats["seconds"] += max(0.0, end - start)
        stats["last_seen"] = max(stats["last_seen"], end)
        if on_session:
            on_session(name, start, end)

    for entry in events:
        t, event, name = entry["t"], entry["event"], entry.get("name", "")
        if event == "totals":
            stats = players.setdefault(name, {"seconds": 0.0, "sessions": 0, "first_seen": entry.get("first", t),
                                              "last_seen": t})
            stats["seconds"] += entry.get("seconds", 0)
            stats["sessions"] += entry.get("sessions", 0)
            stats["first_seen"] = min(stats["first_seen"], entry.get("first", t))
        elif event == "join" and name:
            stats = players.setdefault(name, {"seconds": 0.0, "sessions": 0, "first_seen": t, "last_seen": t})
            if name not in open_sessions:  # a second join without a leave continues the session
                open_sessions[name] = t
                stats["sessions"] += 1
            stats["last_seen"] = max(stats["last_seen"], t)
        elif event == "leave" and name in open_sessions:
            close(name, t)
        elif event == "server_stop":
            for open_name in list(open_sessions):
                close(open_name, t)
    online = set(open_sessions)
    for name in list(open_sessions):
        close(name, until)
    return players, online


def summary(now=None, days=30):
    """{players: [...], hours: [24 x average players online], days} — newest activity first."""
    now = now or time.time()
    since = now - days * 86400
    hour_seconds = [0.0] * 24

    def by_hour(_name, start, end):
        start = max(start, since)
        while start < end:
            local = time.localtime(start)
            hour_end = start - (local.tm_min * 60 + local.tm_sec) + 3600
            chunk_end = min(end, hour_end)
            hour_seconds[local.tm_hour] += chunk_end - start
            start = chunk_end

    players, online = _replay(_events(), now, by_hour)
    rows = [{"name": name, "online": name in online, "playtime_seconds": int(stats["seconds"]),
             "sessions": stats["sessions"], "first_seen": stats["first_seen"], "last_seen": stats["last_seen"]}
            for name, stats in players.items()]
    rows.sort(key=lambda row: (not row["online"], -row["last_seen"]))
    return {"players": rows, "hours": [round(seconds / (days * 3600), 3) for seconds in hour_seconds],
            "days": days}


def compact(now=None):
    """Fold sessions that ended over a year ago into per-player totals. Returns lines removed."""
    now = now or time.time()
    cutoff = now - COMPACT_AFTER_SEC
    with _lock:
        events = _events()
        old = [e for e in events if e["t"] < cutoff]
        if not old or all(e["event"] == "totals" for e in old):
            return 0
        # Only cut at a server_stop, so no session spans the cut.
        stops = [i for i, e in enumerate(events) if e["event"] == "server_stop" and e["t"] < cutoff]
        if not stops:
            return 0
        head, tail = events[:stops[-1] + 1], events[stops[-1] + 1:]
        players, _ = _replay(head, head[-1]["t"])
        totals = [{"t": head[-1]["t"], "event": "totals", "name": name, "seconds": round(stats["seconds"]),
                   "sessions": stats["sessions"], "first": stats["first_seen"]} for name, stats in players.items()]
        fsutil.write_text_atomic(_path(), "".join(json.dumps(e) + "\n" for e in totals + tail))
        return len(head) - len(totals)
