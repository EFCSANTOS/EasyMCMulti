"""Minimal NBT reader/writer for player data and level.dat (no third-party dependency).

A tag is a (type, value) pair. Compounds are dicts of name -> tag (insertion order is kept), lists
are (element type, [values]), arrays are lists of ints (byte arrays are bytes). Strings use Java's
modified UTF-8 (read into ordinary Python strings); decoding and encoding round-trip it exactly, so
a file that is read and written back unchanged is byte-identical apart from compression.
"""
import gzip
import io
import os
import re
import struct

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(13)
_SCALARS = {BYTE: ">b", SHORT: ">h", INT: ">i", LONG: ">q", FLOAT: ">f", DOUBLE: ">d"}
_ARRAYS = {INT_ARRAY: ">i", LONG_ARRAY: ">q"}
_GZIP_MAGIC = b"\x1f\x8b"
_ASTRAL = re.compile("[\U00010000-\U0010FFFF]")


def _read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Truncated NBT data.")
    return data


def _split_astral(match):
    code = ord(match.group()) - 0x10000
    return chr(0xD800 + (code >> 10)) + chr(0xDC00 + (code & 0x3FF))


def _read_string(stream):
    """Java modified UTF-8: null is C0 80 and characters above U+FFFF are two encoded surrogates."""
    (length,) = struct.unpack(">H", _read(stream, 2))
    text = _read(stream, length).replace(b"\xc0\x80", b"\x00").decode("utf-8", "surrogatepass")
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")  # join surrogate pairs


def _write_string(out, text):
    text = _ASTRAL.sub(_split_astral, text)
    data = text.encode("utf-8", "surrogatepass").replace(b"\x00", b"\xc0\x80")
    out.write(struct.pack(">H", len(data)) + data)


def _read_payload(stream, tag_type):
    if tag_type in _SCALARS:
        fmt = _SCALARS[tag_type]
        return struct.unpack(fmt, _read(stream, struct.calcsize(fmt)))[0]
    if tag_type == STRING:
        return _read_string(stream)
    if tag_type == BYTE_ARRAY:
        (length,) = struct.unpack(">i", _read(stream, 4))
        return _read(stream, length)
    if tag_type in _ARRAYS:
        (length,) = struct.unpack(">i", _read(stream, 4))
        fmt = _ARRAYS[tag_type]
        return list(struct.unpack(f">{length}{fmt[1]}", _read(stream, length * struct.calcsize(fmt))))
    if tag_type == LIST:
        element = _read(stream, 1)[0]
        (length,) = struct.unpack(">i", _read(stream, 4))
        return element, [_read_payload(stream, element) for _ in range(max(length, 0))]
    if tag_type == COMPOUND:
        entries = {}
        while (child := _read(stream, 1)[0]) != END:
            name = _read_string(stream)
            entries[name] = (child, _read_payload(stream, child))
        return entries
    raise ValueError(f"Unknown NBT tag type {tag_type}.")


def _write_payload(out, tag_type, value):
    if tag_type in _SCALARS:
        out.write(struct.pack(_SCALARS[tag_type], value))
    elif tag_type == STRING:
        _write_string(out, value)
    elif tag_type == BYTE_ARRAY:
        out.write(struct.pack(">i", len(value)) + bytes(value))
    elif tag_type in _ARRAYS:
        out.write(struct.pack(f">i{len(value)}{_ARRAYS[tag_type][1]}", len(value), *value))
    elif tag_type == LIST:
        element, items = value
        out.write(struct.pack(">bi", element, len(items)))
        for item in items:
            _write_payload(out, element, item)
    elif tag_type == COMPOUND:
        for name, (child, child_value) in value.items():
            out.write(bytes([child]))
            _write_string(out, name)
            _write_payload(out, child, child_value)
        out.write(bytes([END]))
    else:
        raise ValueError(f"Unknown NBT tag type {tag_type}.")


def loads(data):
    """(root name, root compound) from raw NBT bytes, gzipped or not."""
    if data[:2] == _GZIP_MAGIC:
        data = gzip.decompress(data)
    stream = io.BytesIO(data)
    if _read(stream, 1)[0] != COMPOUND:
        raise ValueError("The NBT root is not a compound.")
    name = _read_string(stream)
    return name, _read_payload(stream, COMPOUND)


def dumps(name, root, compress=True):
    out = io.BytesIO()
    out.write(bytes([COMPOUND]))
    _write_string(out, name)
    _write_payload(out, COMPOUND, root)
    data = out.getvalue()
    return gzip.compress(data, mtime=0) if compress else data


def load(path):
    with open(path, "rb") as handle:
        return loads(handle.read())


def save(path, name, root, compress=True):
    """Write atomically (temp file + replace) so a crash never leaves half a player file."""
    temp = f"{path}.tmp"
    with open(temp, "wb") as handle:
        handle.write(dumps(name, root, compress))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
