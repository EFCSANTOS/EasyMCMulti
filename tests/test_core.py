"""Unit tests for pure-ish helpers in core/ (no web app needed)."""
import io
import zipfile

import pytest

from core import downloaders, java_manager, netinfo, server_process, worlds


@pytest.mark.parametrize("version, java", [
    ("1.16.5", 8), ("1.17.1", 17), ("1.18.2", 17), ("1.20.4", 17), ("1.20.5", 21), ("1.21.7", 21),
    ("26.2", 25), ("24w14a", 25), ("1.21-pre1", 21),
])
def test_required_java_table(version, java):
    assert java_manager.required_java(version) == java


def test_version_sorting_and_neoforge_mapping():
    assert sorted(["1.21.9", "1.21.10", "1.20.4"], key=downloaders._version_key) == ["1.20.4", "1.21.9", "1.21.10"]
    assert downloaders._neoforge_to_mc("21.1.73") == "1.21.1"
    assert downloaders._neoforge_to_mc("21.0.167") == "1.21"
    assert downloaders._neoforge_to_mc("26.2.0.7") == "26.2"


def test_update_properties_round_trip(tmp_path):
    (tmp_path / "server.properties").write_text("#comment\nspawn-protection=16\nmotd=old\n", encoding="utf-8")
    server_process.update_properties(str(tmp_path), motd="new", pvp=False, max_players=5)
    props = server_process.read_properties(str(tmp_path))
    assert props == {"spawn-protection": "16", "motd": "new", "pvp": "false", "max-players": "5"}


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_import_zip_finds_the_world_root_and_dimensions(tmp_path):
    archive = tmp_path / "upload.zip"
    archive.write_bytes(_zip({"saves/My World/level.dat": b"x", "saves/My World/region/r.0.0.mca": b"r",
                              "saves/My World_nether/DIM-1/r.mca": b"n"}))
    sdir = tmp_path / "server"
    sdir.mkdir()
    folder = worlds.import_zip(str(sdir), str(archive), "My World")
    assert (sdir / folder / "level.dat").exists() and (sdir / folder / "region" / "r.0.0.mca").exists()
    assert (sdir / f"{folder}_nether" / "DIM-1" / "r.mca").exists()


def test_import_zip_rejects_escaping_members_and_cleans_up(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(_zip({"world/level.dat": b"x", "world/../../escape.txt": b"!"}))
    sdir = tmp_path / "server"
    sdir.mkdir()
    with pytest.raises(ValueError):
        worlds.import_zip(str(sdir), str(archive), "Evil")
    assert not (tmp_path / "escape.txt").exists()
    assert list(sdir.iterdir()) in ([], [sdir / worlds.META_FILE])


def test_netinfo_falls_back_without_network(monkeypatch):
    # The conftest blocks the network: the LAN lookup falls back, the public IP is "unavailable".
    monkeypatch.setattr(netinfo, "_public_ip_cache", (0.0, ""))
    assert netinfo.lan_ip() == "127.0.0.1"
    assert netinfo.public_ip() == "unavailable"
