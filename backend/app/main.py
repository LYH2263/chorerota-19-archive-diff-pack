from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.rota import build_week_slots, swap_legal, apply_swap
from app.modules.seal_snapshot import write_pack, pinned_pack, pack_summary, list_weeks_with_pin
from app.modules.seal_guard import SealedWriteError, ensure_week_writable
from app.modules.seal_diff import week_diff

app = FastAPI(title="Chorerota", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.exception_handler(SealedWriteError)
def _sealed_write(_, exc: SealedWriteError):
    return JSONResponse(status_code=409, content={"detail": "week_sealed"})

def _week_or_404(c, week_id: int):
    week = c.execute("SELECT * FROM weeks WHERE id=?", (week_id,)).fetchone()
    if not week:
        c.close(); raise HTTPException(404, "week not found")
    return week

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

@app.get("/api/weeks")
def list_weeks():
    c = connect(); rows = list_weeks_with_pin(c); c.close(); return rows

@app.get("/api/weeks/{week_id}/board")
def week_board(week_id: int):
    c = connect()
    week = _week_or_404(c, week_id)
    assigns = [dict(r) for r in c.execute("SELECT * FROM assignments WHERE week_id=?", (week_id,))]
    members = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM members")}
    tasks = {r["id"]: r["title"] for r in c.execute("SELECT id,title FROM tasks")}
    pack = pinned_pack(c, week_id)
    c.close()
    for a in assigns:
        a["member_name"] = members.get(a["member_id"], "?")
        a["task_title"] = tasks.get(a["task_id"], "?")
    return {"week": dict(week), "assignments": assigns,
            "pack": pack_summary(pack) if pack else None}

class GenBody(BaseModel):
    days: int = 7
    force: bool = False

@app.post("/api/weeks/{week_id}/generate")
def generate(week_id: int, body: GenBody = GenBody()):
    c = connect()
    week = _week_or_404(c, week_id)
    ensure_week_writable(week)
    existing = c.execute("SELECT COUNT(*) n FROM assignments WHERE week_id=?", (week_id,)).fetchone()["n"]
    if existing and not body.force:
        c.close(); raise HTTPException(409, "already_generated")
    mids = [r["id"] for r in c.execute("SELECT id FROM members WHERE active=1 AND data_quality='clean' ORDER BY id")]
    tids = [r["id"] for r in c.execute("SELECT id FROM tasks WHERE data_quality='clean' AND weight>0 ORDER BY id")]
    slots = build_week_slots(mids, tids, days=body.days)
    c.execute("DELETE FROM assignments WHERE week_id=?", (week_id,))
    for s in slots:
        c.execute("INSERT INTO assignments(week_id,day,task_id,member_id) VALUES (?,?,?,?)",
                  (week_id, s["day"], s["task_id"], s["member_id"]))
    c.execute("UPDATE weeks SET status='ready' WHERE id=?", (week_id,))
    c.commit(); c.close()
    return {"count": len(slots), "slots": slots}

@app.post("/api/weeks/{week_id}/seal")
def seal_week(week_id: int):
    c = connect()
    week = _week_or_404(c, week_id)
    if week["status"] != "ready":
        c.close(); raise HTTPException(409, "not_ready")
    assigns = [dict(r) for r in c.execute(
        "SELECT day,task_id,member_id FROM assignments WHERE week_id=?", (week_id,))]
    pack = write_pack(c, week_id, assigns, datetime.now(timezone.utc).isoformat())
    c.commit(); summary = pack_summary(pack); c.close()
    return {"ok": True, "pack": summary}

@app.post("/api/weeks/{week_id}/unseal")
def unseal_week(week_id: int):
    c = connect()
    week = _week_or_404(c, week_id)
    if week["status"] != "sealed":
        c.close(); raise HTTPException(409, "not_sealed")
    # 解封只回状态，钉住的包不动，供随后 force 重生成做对照
    c.execute("UPDATE weeks SET status='ready' WHERE id=?", (week_id,))
    c.commit(); c.close()
    return {"ok": True}

@app.get("/api/weeks/{week_id}/pack")
def week_pack(week_id: int):
    c = connect()
    _week_or_404(c, week_id)
    pack = pinned_pack(c, week_id)
    c.close()
    if not pack: raise HTTPException(404, "no_pinned_pack")
    return pack

@app.get("/api/weeks/{week_id}/diff")
def week_diff_view(week_id: int):
    c = connect()
    _week_or_404(c, week_id)
    diff = week_diff(c, week_id)
    c.close()
    if diff is None: raise HTTPException(404, "no_pinned_pack")
    return diff

class SwapBody(BaseModel):
    a_day: int; a_task: int; b_day: int; b_task: int; note: str = ""

@app.post("/api/weeks/{week_id}/swaps")
def request_swap(week_id: int, body: SwapBody):
    c = connect()
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

def _pending_swap_or_400(c, swap_id: int):
    sw = c.execute("SELECT * FROM swap_requests WHERE id=?", (swap_id,)).fetchone()
    if not sw: c.close(); raise HTTPException(404, "swap not found")
    if sw["status"] != "pending":
        c.close(); raise HTTPException(400, "not_pending")
    return sw

@app.post("/api/swaps/{swap_id}/confirm")
def confirm_swap(swap_id: int):
    c = connect()
    sw = _pending_swap_or_400(c, swap_id)
    ensure_week_writable(_week_or_404(c, sw["week_id"]))
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

@app.post("/api/swaps/{swap_id}/cancel")
def cancel_swap(swap_id: int):
    c = connect()
    sw = _pending_swap_or_400(c, swap_id)
    ensure_week_writable(_week_or_404(c, sw["week_id"]))
    c.execute("UPDATE swap_requests SET status='cancelled' WHERE id=?", (swap_id,))
    c.commit(); c.close()
    return {"ok": True, "swap_id": swap_id}

@app.get("/api/settings")
def get_settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.put("/api/settings")
def put_settings(body: dict):
    # 只写 settings 表：改家庭名等设置不得解封周、不得改差分包
    c = connect()
    for k, v in body.items():
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    c.commit(); c.close(); return {"ok": True}
