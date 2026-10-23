"""Input validation for everything that reaches the server's stdin or the saved config (FIX-05)."""
from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from typing import Any, Literal

from fastapi import HTTPException

from webapp import properties_meta
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

# Java Edition names. Floodgate (Bedrock crossplay, FEAT-18) prefixes Bedrock names with ".".
PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")
BEDROCK_PREFIX = "."
# The server's own limit for one console line.
MAX_COMMAND_LENGTH = 256
MIN_RAM_MB = 512
_ONE_LINE = r"^[^\r\n]*$"
# "HH:MM" (24 h) or "" for off.
_TIME_OR_EMPTY = r"^(?:(?:[01]\d|2[0-3]):[0-5]\d)?$"


def require_player(value: Any) -> str:
    """A valid player name, or 400. Never lets a newline reach a console command."""
    player = str(value or "").strip()
    if not player:
        raise HTTPException(status_code=400, detail="A player name is required for this action.")
    bare = player[len(BEDROCK_PREFIX):] if player.startswith(BEDROCK_PREFIX) else player
    if not PLAYER_NAME.match(bare):
        raise HTTPException(status_code=400,
                            detail="Invalid player name: use 3 to 16 letters, digits or underscores.")
    return player


def require_command(value: Any) -> str:
    """One console line, or 400: no line breaks or control characters, at most MAX_COMMAND_LENGTH."""
    command = str(value or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="A command is required.")
    if any(ord(char) < 32 or ord(char) == 127 for char in command):
        raise HTTPException(status_code=400, detail="Commands must be a single line of text.")
    if len(command) > MAX_COMMAND_LENGTH:
        raise HTTPException(status_code=400, detail=f"Commands are limited to {MAX_COMMAND_LENGTH} characters.")
    return command


MAX_CHAT_LENGTH = 200


def require_chat(value: Any) -> str:
    """One line of chat text (no control characters), at most MAX_CHAT_LENGTH, or 400."""
    message = str(value or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Type a message first.")
    if any(ord(char) < 32 or ord(char) == 127 for char in message):
        raise HTTPException(status_code=400, detail="Messages must be a single line of text.")
    if len(message) > MAX_CHAT_LENGTH:
        raise HTTPException(status_code=400, detail=f"Messages are limited to {MAX_CHAT_LENGTH} characters.")
    return message


class ConfigUpdate(BaseModel):
    """Every key of webapp.app._default_config, all optional. Unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")

    loader: Literal["vanilla", "paper", "fabric", "forge", "neoforge"] | None = None
    # Part of the server folder name, so no path separators.
    version: str | None = Field(None, pattern=r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$")
    ram_mb: int | None = Field(None, ge=MIN_RAM_MB)
    server_port: int | None = Field(None, ge=1024, le=65535)
    # § colour codes and "\n" (second line) count towards the length but aren't shown.
    motd: str | None = Field(None, max_length=150, pattern=_ONE_LINE)
    max_players: int | None = Field(None, ge=1, le=100)
    gamemode: Literal["survival", "creative", "adventure", "spectator"] | None = None
    difficulty: Literal["peaceful", "easy", "normal", "hard"] | None = None
    level_type: Literal["normal", "flat", "largebiomes", "amplified"] | None = None
    view_distance: int | None = Field(None, ge=2, le=32)
    simulation_distance: int | None = Field(None, ge=2, le=32)
    level_seed: str | None = Field(None, max_length=64, pattern=_ONE_LINE)
    # Same rule as core.worlds.FOLDER_PATTERN: a folder inside the server dir.
    level_name: str | None = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    online_mode: bool | None = None
    pvp: bool | None = None
    spawn_monsters: bool | None = None
    allow_flight: bool | None = None
    allow_nether: bool | None = None
    white_list: bool | None = None
    enable_command_block: bool | None = None
    autostart_server: bool | None = None
    autostart_playit: bool | None = None
    auto_restart: bool | None = None
    auto_update_check: bool | None = None
    auto_update_install: bool | None = None
    remote_dashboard: bool | None = None
    remote_mode: Literal["quick", "named"] | None = None
    remote_hostname: str | None = Field(
        None, max_length=253, pattern=r"^$|^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
    status_page_enabled: bool | None = None
    status_page_show_names: bool | None = None
    status_page_show_lan: bool | None = None
    # status_page_token is deliberately absent: only POST /api/status-page creates tokens.
    backup_interval_hours: int | None = Field(None, ge=0, le=168)
    backup_keep_recent: int | None = Field(None, ge=1, le=100)
    backup_keep_daily: int | None = Field(None, ge=0, le=60)
    backup_prune_manual: bool | None = None
    extra_properties: dict[str, str] | None = None
    friends_can_stop: bool | None = None
    jvm_preset_flags: bool | None = None
    jvm_extra_args: str | None = Field(None, max_length=500, pattern=_ONE_LINE)
    restart_daily_time: str | None = Field(None, pattern=_TIME_OR_EMPTY)
    idle_stop_minutes: int | None = Field(None, ge=0, le=1440)
    quiet_hours_start: str | None = Field(None, pattern=_TIME_OR_EMPTY)
    quiet_hours_end: str | None = Field(None, pattern=_TIME_OR_EMPTY)

    @field_validator("extra_properties")
    @classmethod
    def _extra_properties(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        for key, item in value.items():
            if not properties_meta.KEY_PATTERN.match(key):
                raise ValueError(f"'{key}' is not a server.properties key")
            if key in properties_meta.MANAGED or key in properties_meta.LOCKED:
                raise ValueError(f"{key} is set on the Settings and Server pages, not here")
            if len(item) > 500 or any(ord(char) < 32 for char in item):
                raise ValueError(f"{key}: the value must be one line of at most 500 characters")
        return value

    @field_validator("jvm_extra_args")
    @classmethod
    def _only_jvm_options(cls, value: str | None) -> str | None:
        """Options only: no -jar / @argfile / extra program, so the launch command can't be replaced."""
        if not value:
            return value
        try:
            tokens = shlex.split(value)
        except ValueError as exc:
            raise ValueError(f"can't be parsed ({exc})") from None
        for token in tokens:
            if not token.startswith(("-X", "-D")) or token.lower() in ("-jar",) or token.startswith("@"):
                raise ValueError(f"only -X… and -D… JVM options are allowed, not '{token}'")
        return value

    @field_validator("ram_mb")
    @classmethod
    def _fits_in_host_memory(cls, value: int | None, info: ValidationInfo) -> int | None:
        cap = (info.context or {}).get("ram_cap_mb")
        if value is not None and cap and value > cap:
            raise ValueError(f"must be at most {cap} MB (this machine's memory)")
        return value


CONFIG_KEYS = frozenset(ConfigUpdate.model_fields)


def _field(error: dict[str, Any]) -> str:
    return str(error["loc"][0]) if error.get("loc") else "body"


def validate_config_update(payload: Any, ram_cap_mb: int | None) -> dict[str, Any]:
    """The payload's keys, checked and normalized; 422 naming each bad field otherwise.

    The 422 body matches FastAPI's own format: detail = [{loc: ["body", field], msg, type}].
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail=[{"loc": ["body"], "msg": "Expected a JSON object.",
                                                      "type": "dict_type"}])
    try:
        update = ConfigUpdate.model_validate(payload, context={"ram_cap_mb": ram_cap_mb})
    except ValidationError as exc:
        detail = [{"loc": ["body", _field(error)], "msg": f"{_field(error)}: {error['msg'].removeprefix('Value error, ')}",
                   "type": error["type"]}
                  for error in exc.errors(include_url=False, include_context=False, include_input=False)]
        raise HTTPException(status_code=422, detail=detail) from None
    return update.model_dump(exclude_unset=True, exclude_none=True)


def clean_stored_config(stored: dict[str, Any], defaults: dict[str, Any], ram_cap_mb: int | None,
                        warn: Callable[[str, Any], None]) -> dict[str, Any]:
    """Saved config with invalid known values replaced by defaults (old or hand-edited files).

    Unknown keys pass through untouched; `warn(key, value)` is called for each replaced value.
    """
    known = {key: value for key, value in stored.items() if key in CONFIG_KEYS}
    try:
        ConfigUpdate.model_validate(known, context={"ram_cap_mb": ram_cap_mb})
        return stored
    except ValidationError as exc:
        bad = {_field(error) for error in exc.errors()}
    cleaned = dict(stored)
    for key in bad:
        if key in cleaned:
            warn(key, cleaned[key])
            if key in defaults:
                cleaned[key] = defaults[key]
            else:
                del cleaned[key]
    return cleaned
