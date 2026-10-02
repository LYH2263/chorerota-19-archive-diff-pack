"""Board-vs-package projection: list the cells where live and frozen diverge.

After an unsealed week has been force-regenerated (or otherwise edited), the
sealed package stays byte-identical. This module projects both grids onto
(day, task_id) cells and classifies each difference:

- member_changed:   same cell exists both places, different member_id
- only_in_package:  cell frozen in the package but absent from the live board
- only_in_live:     cell on the live board but absent from the package

Cells that agree are reported only as a match count, not listed.
"""


def _index(cells):
    return {(int(s["day"]), int(s["task_id"])): int(s["member_id"]) for s in cells}


def diff_cells(live_cells: list[dict], package_cells: list[dict]) -> dict:
    live = _index(live_cells)
    frozen = _index(package_cells)
    mismatches = []
    for key in sorted(frozen.keys() | live.keys()):
        day, task_id = key
        if key in frozen and key not in live:
            mismatches.append({
                "day": day, "task_id": task_id, "kind": "only_in_package",
                "package_member_id": frozen[key], "live_member_id": None,
            })
        elif key in live and key not in frozen:
            mismatches.append({
                "day": day, "task_id": task_id, "kind": "only_in_live",
                "package_member_id": None, "live_member_id": live[key],
            })
        elif live[key] != frozen[key]:
            mismatches.append({
                "day": day, "task_id": task_id, "kind": "member_changed",
                "package_member_id": frozen[key], "live_member_id": live[key],
            })
    return {
        "match_count": len(frozen.keys() & live.keys()) - sum(
            1 for k in frozen.keys() & live.keys() if live[k] != frozen[k]),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
