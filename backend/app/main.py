from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.rota import build_week_slots, swap_legal, apply_swap
from app.modules.week_seal import (
    SealedPackageError,
    SealedWeekError,
    assert_writable,
    create_seal,
    diff_cells,
    get_seal,
    read_package,
)
from app.modules.week_seal.guard import (
    OP_FORCE_REGENERATE,
    OP_GENERATE,
    OP_SWAP_CONFIRM,
    OP_SWAP_REVOKE,
)

app = FastAPI(title="Chorerota", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "chorerota"}

@app.get("/api/members")
def list_members():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM members")]; c.close(); return rows

@app.post("/api/members")
def add_member(body: dict):
    c = connect()
    cur = c.execute("INSERT INTO members(name,active,data_quality) VALUES (?,?,?)",
                    (body.get("name","未命名"), int(body.get("active",1)), body.get("data_quality","clean")))
    c.commit(); mid = cur.lastrowid; c.close(); return {"id": mid}

@app.get("/api/tasks")
def list_tasks():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM tasks")]; c.close(); return rows

@app.post("/api/tasks")
def add_task(body: dict):
    c = connect()
    cur = c.execute("INSERT INTO tasks(title,weight,data_quality) VALUES (?,?,?)",
                    (body.get("title","任务"), int(body.get("weight",1)), body.get("data_quality","clean")))
    c.commit(); tid = cur.lastrowid; c.close(); return {"id": tid}

@app.post("/api/weeks")
def add_week(body: dict):
    c = connect()
    cur = c.execute("INSERT INTO weeks(label,status) VALUES (?,?)",
                    (body.get("label", "新周"), "draft"))
    c.commit(); wid = cur.lastrowid; c.close(); return {"id": wid, "status": "draft"}

def _seal_brief(c, week_id):
    row = get_seal(c, week_id)
    if row is None:
        return None
    return {
        "sealed_at": row["sealed_at"],
        "week_label": row["week_label"],
        "household": row["household"],
        "byte_count": row["byte_count"],
        "sha256": row["sha256"],
    }

@app.get("/api/weeks")
def list_weeks():
    c = connect()
    rows = []
    for w in c.execute("SELECT * FROM weeks ORDER BY id"):
        d = dict(w)
        brief = _seal_brief(c, w["id"])
        d["has_package"] = brief is not None
        d["sealed"] = d["status"] == "sealed" and brief is not None
        d["package"] = brief
        rows.append(d)
    c.close(); return rows

@app.get("/api/weeks/{week_id}/board")
def week_board(week_id: int):
    c = connect()
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    assigns = [dict(r) for r in c.execute("SELECT * FROM assignments WHERE week_id=?", (week_id,))]
    members = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM members")}
    tasks = {r["id"]: r["title"] for r in c.execute("SELECT id,title FROM tasks")}
    brief = _seal_brief(c, week_id)
    c.close()
    for a in assigns:
        a["member_name"] = members.get(a["member_id"], "?")
        a["task_title"] = tasks.get(a["task_id"], "?")
    return {
        "week": dict(week),
        "assignments": assigns,
        "sealed": week["status"] == "sealed" and brief is not None,
        "package": brief,
    }

class GenBody(BaseModel):
    days: int = 7
    force: bool = False

@app.post("/api/weeks/{week_id}/generate")
def generate(week_id: int, body: GenBody = GenBody()):
    c = connect()
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    # A sealed week is never writable; force cannot bypass the seal.
    try:
        assert_writable(c, week_id, OP_FORCE_REGENERATE if body.force else OP_GENERATE)
    except SealedWeekError as e:
        c.close(); raise HTTPException(409, e.reason)
    existing = c.execute(
        "SELECT COUNT(*) n FROM assignments WHERE week_id=?", (week_id,)).fetchone()["n"]
    # Regeneration overwrites a live board; once a package exists this would
    # create drift, so demand explicit force.
    if existing and not body.force:
        c.close(); raise HTTPException(409, "force_required")
    mids = [r["id"] for r in c.execute("SELECT id FROM members WHERE active=1 AND data_quality='clean' ORDER BY id")]
    tids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE data_quality='clean' AND weight>0 ORDER BY id")]
    slots = build_week_slots(mids, tids, days=body.days)
    c.execute("DELETE FROM assignments WHERE week_id=?", (week_id,))
    for s in slots:
        c.execute("INSERT INTO assignments(week_id,day,task_id,member_id) VALUES (?,?,?,?)",
                  (week_id, s["day"], s["task_id"], s["member_id"]))
    c.execute("UPDATE weeks SET status='ready' WHERE id=?", (week_id,))
    c.commit(); c.close()
    return {"count": len(slots), "slots": slots, "force": bool(body.force and existing)}

class SwapBody(BaseModel):
    a_day: int; a_task: int; b_day: int; b_task: int; note: str = ""

@app.post("/api/weeks/{week_id}/swaps")
def request_swap(week_id: int, body: SwapBody):
    c = connect()
    week = c.execute("SELECT id FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    assigns = [dict(r) for r in c.execute("SELECT day,task_id,member_id FROM assignments WHERE week_id=?", (week_id,))]
    check = swap_legal(assigns, body.a_day, body.a_task, body.b_day, body.b_task)
    if not check["ok"]:
        c.close(); raise HTTPException(400, check["reason"])
    cur = c.execute(
        "INSERT INTO swap_requests(week_id,a_day,a_task,b_day,b_task,status,note) VALUES (?,?,?,?,?,?,?)",
        (week_id, body.a_day, body.a_task, body.b_day, body.b_task, "pending", body.note))
    c.commit(); sid = cur.lastrowid; c.close()
    return {"id": sid, "status": "pending", **check}

@app.get("/api/swaps")
def list_swaps():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM swap_requests ORDER BY id DESC")]; c.close(); return rows

@app.post("/api/swaps/{swap_id}/confirm")
def confirm_swap(swap_id: int):
    c = connect()
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if not sw: c.close(); raise HTTPException(404, "swap not found")
    try:
        assert_writable(c, sw["week_id"], OP_SWAP_CONFIRM)
    except SealedWeekError as e:
        c.close(); raise HTTPException(409, e.reason)
    if sw["status"] != "pending":
        c.close(); raise HTTPException(400, "not_pending")
    assigns = [dict(r) for r in c.execute(
        "SELECT id,day,task_id,member_id FROM assignments WHERE week_id=?", (sw["week_id"],))]
    slots = [{"day": a["day"], "task_id": a["task_id"], "member_id": a["member_id"]} for a in assigns]
    try:
        new_slots = apply_swap(slots, sw["a_day"], sw["a_task"], sw["b_day"], sw["b_task"])
    except ValueError as e:
        c.close(); raise HTTPException(400, str(e))
    for a, s in zip(assigns, new_slots):
        c.execute("UPDATE assignments SET member_id=? WHERE id=?", (s["member_id"], a["id"]))
    c.execute("UPDATE swap_requests SET status='confirmed' WHERE id=?", (swap_id,))
    c.commit(); c.close()
    return {"ok": True, "swap_id": swap_id}

@app.post("/api/swaps/{swap_id}/revoke")
def revoke_swap(swap_id: int):
    c = connect()
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if not sw: c.close(); raise HTTPException(404, "swap not found")
    try:
        assert_writable(c, sw["week_id"], OP_SWAP_REVOKE)
    except SealedWeekError as e:
        c.close(); raise HTTPException(409, e.reason)
    if sw["status"] != "pending":
        c.close(); raise HTTPException(400, "not_pending")
    c.execute("UPDATE swap_requests SET status='revoked' WHERE id=?", (swap_id,))
    c.commit(); c.close()
    return {"ok": True, "swap_id": swap_id, "status": "revoked"}

@app.post("/api/weeks/{week_id}/seal")
def seal_week(week_id: int):
    c = connect()
    try:
        row = create_seal(c, week_id)
        c.commit()
        result = {"ok": True, "sealed_at": row["sealed_at"],
                  "byte_count": row["byte_count"], "sha256": row["sha256"]}
    except SealedPackageError as e:
        c.close()
        code = {"week_not_found": 404, "already_sealed": 409}.get(e.reason, 409)
        raise HTTPException(code, e.reason)
    c.close()
    return result

@app.post("/api/weeks/{week_id}/unseal")
def unseal_week(week_id: int):
    c = connect()
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    seal = get_seal(c, week_id)
    if seal is None: c.close(); raise HTTPException(404, "not_sealed")
    if week["status"] != "sealed": c.close(); raise HTTPException(409, "not_sealed_status")
    # Only the week status flips; the package row/bytes are untouched.
    c.execute("UPDATE weeks SET status='ready' WHERE id=?", (week_id,))
    c.commit(); c.close()
    return {"ok": True, "week_id": week_id, "status": "ready",
            "package_sha256": seal["sha256"], "package_byte_count": seal["byte_count"]}

@app.get("/api/weeks/{week_id}/seal")
def seal_detail(week_id: int):
    c = connect()
    row = get_seal(c, week_id)
    if row is None: c.close(); raise HTTPException(404, "not_sealed")
    week = c.execute("SELECT status FROM weeks WHERE id=?", (week_id,)).fetchone()
    try:
        doc = read_package(row)
    except SealedPackageError as e:
        c.close(); raise HTTPException(500, e.reason)
    c.close()
    return {
        "week_id": week_id,
        "status": week["status"],
        "sealed": week["status"] == "sealed",
        "sealed_at": row["sealed_at"],
        "week_label": row["week_label"],
        "household": row["household"],
        "byte_count": row["byte_count"],
        "sha256": row["sha256"],
        "package": doc,
    }

@app.get("/api/weeks/{week_id}/seal/package")
def seal_package(week_id: int):
    c = connect()
    row = get_seal(c, week_id)
    if row is None: c.close(); raise HTTPException(404, "not_sealed")
    payload = bytes(row["payload"])
    sha = row["sha256"]; n = row["byte_count"]
    c.close()
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"x-package-sha256": sha, "x-package-byte-count": str(n),
                 "content-disposition": f'attachment; filename="week-{week_id}-seal.json"'})

@app.get("/api/weeks/{week_id}/diff")
def week_diff(week_id: int):
    c = connect()
    week = c.execute("SELECT id FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week: c.close(); raise HTTPException(404, "week not found")
    row = get_seal(c, week_id)
    if row is None: c.close(); raise HTTPException(404, "not_sealed")
    try:
        doc = read_package(row)
    except SealedPackageError as e:
        c.close(); raise HTTPException(500, e.reason)
    live_cells = [dict(r) for r in c.execute(
        "SELECT day,task_id,member_id FROM assignments WHERE week_id=?", (week_id,))]
    members = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM members")}
    tasks = {r["id"]: r["title"] for r in c.execute("SELECT id,title FROM tasks")}
    status = c.execute("SELECT status FROM weeks WHERE id=?", (week_id,)).fetchone()["status"]
    c.close()
    result = diff_cells(live_cells, doc["cells"])
    for m in result["mismatches"]:
        m["task_title"] = tasks.get(m["task_id"], "?")
        pm, lm = m["package_member_id"], m["live_member_id"]
        m["package_member_name"] = members.get(pm, "?") if pm is not None else None
        m["live_member_name"] = members.get(lm, "?") if lm is not None else None
    result["week_id"] = week_id
    result["status"] = status
    result["package_sha256"] = row["sha256"]
    result["package_byte_count"] = row["byte_count"]
    return result

@app.get("/api/settings")
def get_settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.put("/api/settings")
def put_settings(body: dict):
    c = connect()
    for k, v in body.items():
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    c.commit(); c.close(); return {"ok": True}
