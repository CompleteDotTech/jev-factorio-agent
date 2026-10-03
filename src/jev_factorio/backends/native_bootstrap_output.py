"""Same-command read sidecar over the explicitly installed output capability."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import stat

from ..bootstrap_output import MODULE, ROLE, _integer, _point

MARKER = "JEV_BOOTSTRAP_OUTPUT|"
WITNESS_SCHEMA = "jev.bootstrap-output-ownership.v1"
WITNESS_FIELDS = {"schema", "session_id", "actor_unit", "surface_index", "force_index",
    "drill_unit", "chest_unit", "drill_position", "drop_position", "chest_position",
    "origin", "authorization_sha256", "bound_at_tick", "asset_sha256"}


def read_witness(attachment):
    """Read the immutable witness pinned by the separately signed launcher.

    This is an ownership pin, not a replacement for the installer's signature,
    original attachment or full prepared/result verification.
    """
    path = os.environ.get("JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS")
    expected = os.environ.get("JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256")
    if (not path or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or expected == "0" * 64):
        raise ValueError("Bootstrap ownership requires its pinned installation witness")
    before = Path(path).lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Bootstrap ownership witness is not a regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_size > 16384
                or (os.name == "posix" and (opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o400))):
            raise ValueError("Bootstrap ownership witness identity/privacy changed")
        raw = os.read(fd, 16385)
        after = os.fstat(fd)
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Bootstrap ownership witness changed during read")
        if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Bootstrap ownership witness hash changed")
    finally:
        os.close(fd)
    value = json.loads(raw)
    assets = attachment.get("native_installation", {}).get("assets", {})
    if (not isinstance(value, dict) or set(value) != WITNESS_FIELDS
            or value.get("schema") != WITNESS_SCHEMA
            or value.get("asset_sha256") != assets.get(MODULE)
            or value.get("session_id") != attachment.get("session_id")
            or any(not _integer(value.get(k), 1) for k in (
                "actor_unit", "surface_index", "force_index", "drill_unit", "chest_unit"))
            or not _integer(value.get("bound_at_tick"))
            or value.get("drill_unit") == value.get("chest_unit")
            or not all(_point(value.get(k)) for k in (
                "drill_position", "drop_position", "chest_position"))
            or not isinstance(value.get("authorization_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["authorization_sha256"])
            or value["authorization_sha256"] == "0" * 64
            or not isinstance(value.get("asset_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["asset_sha256"])
            or value["asset_sha256"] == "0" * 64
            or type(attachment.get("actor_unit")) is not int
            or value.get("actor_unit") != attachment.get("actor_unit")
            or value.get("origin") != "legacy_authorized_current_asset"):
        raise ValueError("Bootstrap ownership witness differs from installed capability")
    return value


def observation_command(command):
    return command + '''
do
local rt=assert(jev_fle_runtime)
if rt.bootstrap_output_v1 then
    local row=rt.bootstrap_output_v1.observe()
    rcon.print("JEV_BOOTSTRAP_OUTPUT|"..helpers.table_to_json({schema=1,tick=game.tick,
        session_id=rt.jev_session_id,actor_unit=rt.fair.actor().character.unit_number,output=row,
        native_pending=rt.bootstrap_output_v1.placement_pending~=false
            or rt.bootstrap_output_v1.transfer_pending~=false}))
end
end'''


def decode(raw, result, attachment):
    rows = [line[len(MARKER):] for line in raw.splitlines() if line.startswith(MARKER)]
    enabled = bool(attachment and attachment.get("modules", {}).get(MODULE) is True)
    if not enabled:
        if rows:
            raise ValueError("Unqualified bootstrap output capability")
        return None
    if len(rows) != 1 or len(rows[0].encode()) > 32768:
        raise ValueError("Missing or ambiguous bootstrap output sidecar")
    value = json.loads(rows[0])
    if (not isinstance(value, dict) or set(value) != {"schema", "tick", "session_id", "actor_unit", "output", "native_pending"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or type(value["tick"]) is not int or type(value["actor_unit"]) is not int
            or type(value["native_pending"]) is not bool
            or any(value[k] != result[k] for k in ("tick", "session_id", "actor_unit"))):
        raise ValueError("Bootstrap output crossed observation identity")
    result['factory']['bootstrap_output_pending'] = value['native_pending']
    row = value["output"]
    if row is False:
        if ROLE in result["factory"]["entities"]:
            raise ValueError("Unbound bootstrap output registration")
        return None
    if not isinstance(row, dict):
        raise ValueError("Invalid bootstrap output sidecar")
    if type(row.get('native_pending')) is not bool or row['native_pending'] != value['native_pending']:
        raise ValueError("Bootstrap output pending journal changed")
    if row.get("output") == []:
        row["output"] = {}
    return row
