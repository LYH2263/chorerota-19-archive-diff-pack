"""Snapshot writing: freeze the sealed week into an immutable byte package.

The package is canonical JSON (sorted keys, no whitespace, UTF-8) plus a
sha256 over those exact bytes. It is written with a single INSERT into
week_seals and is never UPDATE-d afterwards: unsealing only flips
weeks.status, so the bytes and their decoded content stay byte-identical
forever.
"""
import hashlib
import json
from datetime import datetime, timezone

SNAPSHOT_FORMAT = "chorerota.week-cells"
SNAPSHOT_VERSION = 1


class SealedPackageError(Exception):
    """Invalid state for creating/reading a sealed package."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def canonical_bytes(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_package_bytes(*, week_id: int, week_label: str, household: str, cells: list[dict],
                        sealed_at: str | None = None) -> bytes:
    """Render the immutable package bytes from the seal-moment grid."""
    frozen = sorted(
        ({"day": int(s["day"]), "task_id": int(s["task_id"]), "member_id": int(s["member_id"])}
         for s in cells),
        key=lambda s: (s["day"], s["task_id"]),
    )
    doc = {
        "format": SNAPSHOT_FORMAT,
        "format_version": SNAPSHOT_VERSION,
        "week_id": int(week_id),
        "week_label": week_label,
        "household": household,
        "sealed_at": sealed_at or datetime.now(timezone.utc).isoformat(),
        "cells": frozen,
    }
    return canonical_bytes(doc)


def get_seal(c, week_id: int):
    return c.execute("SELECT * FROM week_seals WHERE week_id=?", (week_id,)).fetchone()


def create_seal(c, week_id: int, sealed_at: str | None = None):
    """INSERT one immutable package row for a ready week. Caller commits.

    Raises SealedPackageError if the week is missing, not ready, or already
    sealed.
    """
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week:
        raise SealedPackageError("week_not_found")
    if week["status"] != "ready":
        raise SealedPackageError("week_not_ready")
    if get_seal(c, week_id) is not None:
        raise SealedPackageError("already_sealed")

    cells = [dict(r) for r in c.execute(
        "SELECT day,task_id,member_id FROM assignments WHERE week_id=?", (week_id,))]
    row = c.execute("SELECT value FROM settings WHERE key='household'").fetchone()
    household = row["value"] if row else ""
    payload = build_package_bytes(
        week_id=week_id, week_label=week["label"] or "", household=household,
        cells=cells, sealed_at=sealed_at)

    c.execute(
        "INSERT INTO week_seals(week_id,sealed_at,week_label,household,byte_count,sha256,payload)"
        " VALUES (?,?,?,?,?,?,?)",
        (week_id, sealed_at or datetime.now(timezone.utc).isoformat(),
         week["label"] or "", household, len(payload), hashlib.sha256(payload).hexdigest(),
         payload))
    c.execute("UPDATE weeks SET status='sealed' WHERE id=?", (week_id,))
    return get_seal(c, week_id)


def read_package(seal_row) -> dict:
    """Decode a stored seal row, verifying the bytes match the stored hash.

    Raises SealedPackageError on any tampering/corruption.
    """
    if seal_row is None:
        raise SealedPackageError("not_sealed")
    payload = bytes(seal_row["payload"])
    digest = hashlib.sha256(payload).hexdigest()
    if digest != seal_row["sha256"] or len(payload) != seal_row["byte_count"]:
        raise SealedPackageError("package_hash_mismatch")
    try:
        doc = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SealedPackageError("package_decode_error") from e
    if doc.get("format") != SNAPSHOT_FORMAT:
        raise SealedPackageError("package_format_unknown")
    return doc
