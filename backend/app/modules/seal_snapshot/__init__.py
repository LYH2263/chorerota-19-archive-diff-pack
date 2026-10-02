"""封存快照写入：ready 周封存瞬间落一份不可变格位差分包。

差分包 = 封存瞬间全部格位 {day, task_id, member_id} 的规范化字节，
落库后只增不改：解封、重生成、再封存都不会改写已存在的包行，
再封存只会追加新版本并把 weeks.pack_id 钉向新包。
"""
import hashlib
import json

CELL_KEYS = ("day", "task_id", "member_id")


def canonical_cells(assignments: list[dict]) -> list[dict]:
    """格位规范化：只留定位三键，按 (day, task_id) 排序，与查询顺序无关。"""
    cells = [
        {"day": int(a["day"]), "task_id": int(a["task_id"]), "member_id": int(a["member_id"])}
        for a in assignments
    ]
    return sorted(cells, key=lambda c: (c["day"], c["task_id"]))


def encode_cells(cells: list[dict]) -> bytes:
    """规范化字节：同一份格位永远编码出同一份字节，checksum 才稳定。"""
    return json.dumps(cells, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def checksum_of(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def write_pack(conn, week_id: int, assignments: list[dict], sealed_at: str) -> dict:
    """写入新包并把周钉到它上面（status 翻为 sealed）。调用方负责 commit。

    只 INSERT 不 UPDATE：已存在的包行字节与内容永不被改写。
    """
    cells = canonical_cells(assignments)
    body = encode_cells(cells)
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM week_packs WHERE week_id=?", (week_id,)
    ).fetchone()["v"]
    cur = conn.execute(
        "INSERT INTO week_packs(week_id,version,sealed_at,cell_count,checksum,body) VALUES (?,?,?,?,?,?)",
        (week_id, version, sealed_at, len(cells), checksum_of(body), body.decode("utf-8")),
    )
    conn.execute("UPDATE weeks SET status='sealed', pack_id=? WHERE id=?", (cur.lastrowid, week_id))
    return load_pack(conn, cur.lastrowid)


def _to_pack(row) -> dict | None:
    if row is None:
        return None
    pack = dict(row)
    pack["cells"] = json.loads(pack["body"])
    return pack


def load_pack(conn, pack_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM week_packs WHERE id=?", (pack_id,)).fetchone()
    return _to_pack(row)


def pinned_pack(conn, week_id: int) -> dict | None:
    """周当前钉住的包（解封后仍钉着，供对照投影用）。"""
    row = conn.execute(
        "SELECT p.* FROM weeks w JOIN week_packs p ON p.id = w.pack_id WHERE w.id=?", (week_id,)
    ).fetchone()
    return _to_pack(row)


def pack_summary(pack: dict) -> dict:
    """三路同钉的对外摘要：周列表 / 包详情 / 看板对照入口共用同一份封存瞬间数据。"""
    return {
        "pack_id": pack["id"],
        "version": pack["version"],
        "sealed_at": pack["sealed_at"],
        "cell_count": pack["cell_count"],
        "checksum": pack["checksum"],
    }


def list_weeks_with_pin(conn) -> list[dict]:
    """周列表状态：每周带上钉住包的摘要（无包则为空）。"""
    rows = conn.execute(
        """
        SELECT w.id, w.label, w.status, w.pack_id,
               p.version AS pack_version, p.sealed_at AS pack_sealed_at,
               p.cell_count AS pack_cell_count, p.checksum AS pack_checksum
        FROM weeks w LEFT JOIN week_packs p ON p.id = w.pack_id
        ORDER BY w.id
        """
    ).fetchall()
    return [dict(r) for r in rows]
