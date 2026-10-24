"""Carrying player progress (inventories, stats, advancements, mod data) from one world to another."""
from support import *  # noqa: F401,F403

import pathlib

from core import nbt, player_transfer

STEVE = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
ALEX = "853c80ef-3c37-49fd-aa49-938b674adae6"


def _player_tree(x=1200.5, dimension="minecraft:the_nether"):
    """A player file as the server writes it, with a modded backpack attached and a bed spawn."""
    return {
        "DataVersion": (nbt.INT, 3955),
        "Pos": (nbt.LIST, (nbt.DOUBLE, [x, 70.0, -300.25])),
        "Motion": (nbt.LIST, (nbt.DOUBLE, [0.0, -0.08, 0.0])),
        "Dimension": (nbt.STRING, dimension),
        "SpawnX": (nbt.INT, 1180), "SpawnY": (nbt.INT, 64), "SpawnZ": (nbt.INT, -310),
        "SpawnDimension": (nbt.STRING, "minecraft:overworld"),
        "WorldUUIDMost": (nbt.LONG, -123456789), "WorldUUIDLeast": (nbt.LONG, 987654321),
        "XpLevel": (nbt.INT, 30),
        "Inventory": (nbt.LIST, (nbt.COMPOUND, [
            {"Slot": (nbt.BYTE, 0), "id": (nbt.STRING, "minecraft:diamond_sword"), "count": (nbt.INT, 1)},
        ])),
        "ForgeCaps": (nbt.COMPOUND, {"travelersbackpack:travelers_backpack": (nbt.COMPOUND, {
            "Items": (nbt.LIST, (nbt.COMPOUND, [{"id": (nbt.STRING, "minecraft:emerald"), "count": (nbt.INT, 64)}])),
        })}),
        "UUID": (nbt.INT_ARRAY, [1, -2, 3, -4]),
    }


def _level_dat(path, spawn=(10, 72, -20), new_format=False):
    if new_format:
        data = {"spawn": (nbt.COMPOUND, {"pos": (nbt.INT_ARRAY, list(spawn)),
                                         "dimension": (nbt.STRING, "minecraft:overworld")})}
    else:
        data = {"SpawnX": (nbt.INT, spawn[0]), "SpawnY": (nbt.INT, spawn[1]), "SpawnZ": (nbt.INT, spawn[2])}
    path.parent.mkdir(parents=True, exist_ok=True)
    nbt.save(str(path), "", {"Data": (nbt.COMPOUND, data)})


def _worlds(sdir, new_format=False):
    """Fill a server dir with an old world full of progress and a freshly generated one."""
    sdir = pathlib.Path(sdir)
    old, new = sdir / "old", sdir / "new"
    _level_dat(old / "level.dat", spawn=(0, 64, 0))
    _level_dat(new / "level.dat", new_format=new_format)
    (old / "playerdata").mkdir()
    nbt.save(str(old / "playerdata" / f"{STEVE}.dat"), "", _player_tree())
    (old / "playerdata" / f"{STEVE}.dat_old").write_bytes(b"older copy")
    (old / "playerdata" / f"{STEVE}.baub").write_bytes(b"baubles")  # a Forge mod's own player file
    nbt.save(str(old / "playerdata" / f"{ALEX}.dat"), "", _player_tree(x=5.0))
    for part in ("stats", "advancements"):
        (old / part).mkdir()
        (old / part / f"{STEVE}.json").write_text('{"' + part + '": 1}', encoding="utf-8")
    (old / "data").mkdir()
    (old / "data" / "sophisticatedbackpacks.dat").write_bytes(b"backpack contents")
    (old / "data" / "raids.dat").write_bytes(b"raids")
    (old / "data" / "map_3.dat").write_bytes(b"map")
    (old / "ftbquests").mkdir()
    (old / "ftbquests" / "team.snbt").write_text("progress", encoding="utf-8")
    (old / "region").mkdir()
    (sdir / "usercache.json").write_text(json.dumps([{"name": "Steve", "uuid": STEVE}]), encoding="utf-8")
    return sdir


def _tree(path):
    return nbt.load(str(path))[1]


def test_nbt_round_trips_every_tag_and_java_strings():
    root = {
        "b": (nbt.BYTE, -5), "s": (nbt.SHORT, 300), "i": (nbt.INT, -70000), "l": (nbt.LONG, 2**40),
        "f": (nbt.FLOAT, 0.5), "d": (nbt.DOUBLE, -1.25), "ba": (nbt.BYTE_ARRAY, b"\x00\xff"),
        "text": (nbt.STRING, "Steve's é \x00 \U0001F600"), "ia": (nbt.INT_ARRAY, [1, -1]),
        "la": (nbt.LONG_ARRAY, [2**50]), "empty": (nbt.LIST, (nbt.END, [])),
        "nested": (nbt.LIST, (nbt.COMPOUND, [{"x": (nbt.INT, 1)}, {}])),
    }
    raw = nbt.dumps("root", root, compress=False)
    assert nbt.loads(raw) == ("root", root)
    assert nbt.dumps(*nbt.loads(raw), compress=False) == raw
    assert b"\xc0\x80" in raw  # Java's modified UTF-8: null as two bytes...
    assert b"\xed\xa0\xbd\xed\xb8\x80" in raw and b"\xf0" not in raw  # ...and U+1F600 as a surrogate pair
    assert nbt.loads(nbt.dumps("root", root)) == ("root", root)  # gzipped, as the server writes it
    with pytest.raises(ValueError):
        nbt.loads(raw[:-3])


def test_player_transfer_lists_players_and_mod_data(tmp_path):
    sdir = str(_worlds(tmp_path / "server"))
    listed = player_transfer.list_players(sdir, "old")
    assert [(p["uuid"], p["name"]) for p in listed] == [(STEVE, "Steve"), (ALEX, "")]  # named players first
    assert listed[0]["parts"] == ["playerdata", "stats", "advancements"] and listed[0]["last_seen"]
    assert [m["path"] for m in player_transfer.mod_data(sdir, "old")] == ["ftbquests", "data/sophisticatedbackpacks.dat"]
    assert player_transfer.list_players(sdir, "new") == []
    with pytest.raises(ValueError, match="not been generated"):
        player_transfer.list_players(sdir, "missing")


@pytest.mark.parametrize("new_format", [False, True])
def test_player_transfer_moves_players_to_the_new_spawn_and_keeps_their_items(tmp_path, new_format):
    sdir = _worlds(tmp_path / "server", new_format)
    before = (sdir / "old" / "playerdata" / f"{STEVE}.dat").read_bytes()
    result = player_transfer.transfer(str(sdir), "old", "new", log=lambda line: None)
    assert result == {"players": 2, "parts": ["playerdata", "stats", "advancements"], "extras": [], "to_spawn": True}

    moved = _tree(sdir / "new" / "playerdata" / f"{STEVE}.dat")
    assert moved["Pos"] == (nbt.LIST, (nbt.DOUBLE, [10.5, 72.0, -19.5]))
    assert moved["Dimension"] == (nbt.STRING, "minecraft:overworld")
    for gone in ("Motion", "SpawnX", "SpawnDimension", "WorldUUIDMost", "WorldUUIDLeast"):
        assert gone not in moved
    original = _player_tree()
    for kept in ("Inventory", "ForgeCaps", "XpLevel", "UUID", "DataVersion"):
        assert moved[kept] == original[kept]
    assert (sdir / "new" / "playerdata" / f"{STEVE}.baub").read_bytes() == b"baubles"
    assert not (sdir / "new" / "playerdata" / f"{STEVE}.dat_old").exists()
    assert (sdir / "new" / "stats" / f"{STEVE}.json").read_text(encoding="utf-8") == '{"stats": 1}'
    assert (sdir / "new" / "advancements" / f"{STEVE}.json").exists()
    assert not (sdir / "new" / "data").exists()  # mod data only when asked for
    assert (sdir / "old" / "playerdata" / f"{STEVE}.dat").read_bytes() == before  # the old world is only read


def test_player_transfer_copies_chosen_players_parts_and_mod_data(tmp_path):
    sdir = _worlds(tmp_path / "server")
    (sdir / "new" / "data").mkdir()
    (sdir / "new" / "data" / "sophisticatedbackpacks.dat").write_bytes(b"empty")
    player_transfer.transfer(str(sdir), "old", "new", [STEVE], ["stats"], True,
                             ["data/sophisticatedbackpacks.dat", "ftbquests"], log=lambda line: None)
    assert (sdir / "new" / "stats" / f"{STEVE}.json").exists()
    assert not (sdir / "new" / "playerdata").exists() and not (sdir / "new" / "advancements").exists()
    assert (sdir / "new" / "data" / "sophisticatedbackpacks.dat").read_bytes() == b"backpack contents"
    assert (sdir / "new" / "ftbquests" / "team.snbt").read_text(encoding="utf-8") == "progress"

    player_transfer.transfer(str(sdir), "old", "new", [ALEX], ["playerdata"], False, log=lambda line: None)
    kept = _tree(sdir / "new" / "playerdata" / f"{ALEX}.dat")
    assert kept["Pos"] == (nbt.LIST, (nbt.DOUBLE, [5.0, 70.0, -300.25])) and "SpawnX" in kept  # copied as is


def test_player_transfer_refuses_bad_choices_before_writing_anything(tmp_path):
    sdir = _worlds(tmp_path / "server")
    (sdir / "unborn").mkdir()  # created in the dashboard, never started
    cases = [
        (("old", "unborn"), {}, "not been generated"),
        (("old", "old"), {}, "different worlds"),
        (("old", "new"), {"uuids": ["00000000-0000-0000-0000-000000000000"]}, "No data for player"),
        (("old", "new"), {"extras": ["../../evil"]}, "Unknown mod data"),
        (("old", "new"), {"extras": ["data/raids.dat"]}, "Unknown mod data"),
        (("old", "new"), {"parts": ["level"]}, "Unknown kind"),
        (("old", "new"), {"parts": []}, "Nothing to copy"),
        (("../server/old", "new"), {}, "Invalid world"),
    ]
    for (source, target), kwargs, message in cases:
        with pytest.raises(ValueError, match=message):
            player_transfer.prepare(str(sdir), source, target, **kwargs)
    (sdir / "old" / "playerdata" / f"{ALEX}.dat").write_bytes(b"\x1f\x8b broken")
    with pytest.raises(ValueError, match="damaged"):
        player_transfer.transfer(str(sdir), "old", "new", log=lambda line: None)
    assert sorted(os.listdir(sdir / "new")) == ["level.dat"]  # nothing half-copied


def test_player_transfer_endpoints_back_up_the_target_first(monkeypatch):
    app.state.runtime.process = None
    sdir = common_module._server_dir_for_config(common_module._effective_config())
    server = _worlds(sdir)
    (server / "unborn").mkdir()
    auth_ = ("admin", "s3cret-pass")
    listed = client.get("/api/worlds/old/players", auth=auth_).json()
    assert [p["name"] for p in listed["players"]] == ["Steve", ""]
    assert client.get("/api/worlds/unborn/players", auth=auth_).status_code == 400

    response = client.post("/api/worlds/new/transfer", auth=auth_,
                           json={"source": "old", "players": [STEVE], "mod_data": ["data/sophisticatedbackpacks.dat"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transfer"]["players"] == 1 and "server_running" in body
    assert (server / "new" / "playerdata" / f"{STEVE}.dat").exists()
    assert (server / "new" / "data" / "sophisticatedbackpacks.dat").exists()
    assert any(name.endswith("-auto-pre-transfer.zip") for name in os.listdir(server))

    bad = client.post("/api/worlds/new/transfer", auth=auth_, json={"source": "old", "players": "everyone"})
    assert bad.status_code == 400
    backups_before = len([n for n in os.listdir(server) if n.endswith(".zip")])
    assert client.post("/api/worlds/new/transfer", auth=auth_, json={"source": "old", "players": ["nobody"]}).status_code == 400
    assert len([n for n in os.listdir(server) if n.endswith(".zip")]) == backups_before  # no backup for a bad request

    running = RecordingProcess([], "", None, None)
    running.running = True
    app.state.runtime.process = running
    try:
        assert client.post("/api/worlds/new/transfer", auth=auth_, json={"source": "old"}).status_code == 409
    finally:
        app.state.runtime.process = None

