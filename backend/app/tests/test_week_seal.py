"""Week-seal feature: sealed writes rejected, package immutable, diff detects drift."""
import json

import pytest
from fastapi.testclient import TestClient

from app import seed
from app.main import app
from app.modules.week_seal import diff_cells

WEEK = 1
T1, T2, T3 = 1, 2, 3  # seed clean tasks 洗碗/倒垃圾/扫地


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """One fresh seeded sqlite database per test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    yield


def _generate(client, force=None, days=None):
    body = {}
    if force is not None: body["force"] = force
    if days is not None: body["days"] = days
    return client.post(f"/api/weeks/{WEEK}/generate", json=body)


def _seal_week_first():
    """Fresh client: generate then seal week 1; return (client, seal payload)."""
    client = TestClient(app)
    with client:
        assert _generate(client).status_code == 200
        # two pending swaps, different assignees: (day,t1)<->(day,t2)
        for day in (0, 1):
            r = client.post(f"/api/weeks/{WEEK}/swaps",
                            json={"a_day": day, "a_task": T1, "b_day": day, "b_task": T2})
            assert r.status_code == 200, r.text
        r = client.post(f"/api/weeks/{WEEK}/seal")
        assert r.status_code == 200, r.text
        return client, r.json()


def _package_bytes(client):
    r = client.get(f"/api/weeks/{WEEK}/seal/package")
    assert r.status_code == 200
    return r.content, r.headers["x-package-sha256"], int(r.headers["x-package-byte-count"])


# ---------- sealed weeks reject every guarded write ----------

def test_sealed_week_rejects_generates_confirms_and_revokes():
    client, seal = _seal_week_first()
    with client:
        weeks = {w["id"]: w for w in client.get("/api/weeks").json()}
        assert weeks[WEEK]["status"] == "sealed"
        assert weeks[WEEK]["sealed"] is True

        # generation blocked, force cannot bypass a seal
        assert _generate(client).status_code == 409
        r = _generate(client, force=True)
        assert r.status_code == 409 and r.json()["detail"] == "week_sealed"

        # swap confirm blocked (#1) and revoke blocked (#2)
        assert client.post("/api/swaps/1/confirm").status_code == 409
        assert client.post("/api/swaps/2/revoke").status_code == 409

        # swap rows stay exactly as they were at seal time
        swaps = {s["id"]: s["status"] for s in client.get("/api/swaps").json()}
        assert swaps[1] == "pending" and swaps[2] == "pending"


def test_seal_requires_ready_week():
    client = TestClient(app)
    with client:
        r = client.post("/api/weeks", json={"label": "草稿周"})
        wid = r.json()["id"]
        assert client.post(f"/api/weeks/{wid}/seal").status_code == 409
        # sealing the same ready week twice is also refused
        assert client.post(f"/api/weeks/{WEEK}/seal").status_code == 409


# ---------- the package bytes/content are never rewritten ----------

def test_package_bytes_survive_household_rename_and_unseal():
    client, seal = _seal_week_first()
    with client:
        before, sha_before, n_before = _package_bytes(client)
        doc_before = json.loads(before)
        assert seal["sha256"] == sha_before and seal["byte_count"] == n_before
        assert doc_before["household"] == "绿纸之家"
        assert len(doc_before["cells"]) == 21  # 3 tasks * 7 days

        # renaming the household neither unseals nor alters the package
        r = client.put("/api/settings", json={"household": "红纸之家"})
        assert r.status_code == 200
        weeks = {w["id"]: w for w in client.get("/api/weeks").json()}
        assert weeks[WEEK]["status"] == "sealed"  # not unsealed
        assert _generate(client, force=True).status_code == 409  # still write-locked
        detail = client.get(f"/api/weeks/{WEEK}/seal").json()
        assert detail["household"] == "绿纸之家"  # frozen name inside the package
        assert detail["package"]["household"] == "绿纸之家"

        # unseal flips only the week status
        r = client.post(f"/api/weeks/{WEEK}/unseal")
        assert r.status_code == 200 and r.json()["status"] == "ready"
        after, sha_after, n_after = _package_bytes(client)
        assert after == before and sha_after == sha_before and n_after == n_before


# ---------- after unseal, writes resume and the projection lists drift ----------

def test_force_regenerate_requires_ack_and_diff_detects_drift():
    client, _ = _seal_week_first()
    with client:
        assert client.post(f"/api/weeks/{WEEK}/unseal").status_code == 200
        pkg_before, sha_before, _ = _package_bytes(client)

        # a plain regeneration over the existing board is a drift risk -> force_required
        assert _generate(client).status_code == 409

        # a confirmed swap now goes through and the projection flags the two cells
        assert client.post("/api/swaps/1/confirm").status_code == 200
        d = client.get(f"/api/weeks/{WEEK}/diff").json()
        changed = [m for m in d["mismatches"] if m["kind"] == "member_changed"]
        assert d["mismatch_count"] == 2 and len(changed) == 2
        assert {(m["day"], m["task_id"]) for m in changed} == {(0, T1), (0, T2)}
        assert d["package_sha256"] == sha_before

        # force regeneration with a shorter grid: live loses all day-6 cells
        r = _generate(client, force=True)
        assert r.status_code == 200 and r.json()["force"] is True
        r2 = client.post(f"/api/weeks/{WEEK}/generate", json={"days": 6, "force": True})
        assert r2.status_code == 200
        d = client.get(f"/api/weeks/{WEEK}/diff").json()
        assert d["mismatch_count"] == 3
        assert {m["kind"] for m in d["mismatches"]} == {"only_in_package"}
        assert {m["day"] for m in d["mismatches"]} == {6}

        # through all of this the frozen package is still the exact seal bytes
        pkg_after, sha_after, _ = _package_bytes(client)
        assert pkg_after == pkg_before and sha_after == sha_before


# ---------- projection unit coverage for every mismatch kind ----------

def test_diff_cells_classifies_all_kinds():
    frozen = [
        {"day": 0, "task_id": T1, "member_id": 1},
        {"day": 0, "task_id": T2, "member_id": 2},
        {"day": 1, "task_id": T1, "member_id": 3},
    ]
    live = [
        {"day": 0, "task_id": T1, "member_id": 9},  # member_changed
        {"day": 0, "task_id": T2, "member_id": 2},  # match
        {"day": 1, "task_id": T2, "member_id": 4},  # only_in_live
    ]
    out = diff_cells(live, frozen)
    kinds = {(m["day"], m["task_id"], m["kind"]) for m in out["mismatches"]}
    assert kinds == {(0, T1, "member_changed"), (1, T1, "only_in_package"),
                     (1, T2, "only_in_live")}
    assert out["match_count"] == 1 and out["mismatch_count"] == 3
    ch = next(m for m in out["mismatches"] if m["kind"] == "member_changed")
    assert ch["package_member_id"] == 1 and ch["live_member_id"] == 9


def test_identical_boards_have_no_mismatches():
    cells = [{"day": 0, "task_id": T1, "member_id": 1}]
    out = diff_cells(cells, [dict(s) for s in cells])
    assert out["mismatch_count"] == 0 and out["match_count"] == 1
