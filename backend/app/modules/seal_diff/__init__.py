"""对照投影：看板现网格位 vs 钉住的封存差分包，列出不一致格。

只读投影，不落库、不改包；解封后 force 重生成产生的偏差由这里检出。
"""
from app.modules.seal_snapshot import pack_summary, pinned_pack


def project_diff(pack_cells: list[dict], live_assignments: list[dict]) -> list[dict]:
    """按 (day, task_id) 对齐两侧格位，产出有序的不一致格列表。

    kind: changed（同人不同位）/ missing_live（现网缺格）/ extra_live（现网多格）。
    """
    def key(c):
        return (c["day"], c["task_id"])

    sealed = {key(c): c for c in pack_cells}
    live = {key(a): a for a in live_assignments}
    out = []
    for k in sorted(set(sealed) | set(live)):
        s, l = sealed.get(k), live.get(k)
        cell = {"day": k[0], "task_id": k[1]}
        if s is not None and l is None:
            out.append({**cell, "kind": "missing_live",
                        "sealed_member_id": s["member_id"], "live_member_id": None})
        elif l is not None and s is None:
            out.append({**cell, "kind": "extra_live",
                        "sealed_member_id": None, "live_member_id": l["member_id"]})
        elif s["member_id"] != l["member_id"]:
            out.append({**cell, "kind": "changed",
                        "sealed_member_id": s["member_id"], "live_member_id": l["member_id"]})
    return out


def week_diff(conn, week_id: int) -> dict | None:
    """周的对照投影：钉住包 vs 现网 assignments。无钉住包返回 None。"""
    pack = pinned_pack(conn, week_id)
    if pack is None:
        return None
    live = [
        dict(r)
        for r in conn.execute(
            "SELECT day, task_id, member_id FROM assignments WHERE week_id=?", (week_id,)
        )
    ]
    mismatches = project_diff(pack["cells"], live)
    return {
        "pack": pack_summary(pack),
        "live_count": len(live),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
