"""PelliPlan API. Thin routes: the rules live in auth.py (who), engine.py (when), brain.py (AI, always validated)."""
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, brain, seed, service
from . import db as D
from . import engine as E
from .llm import get_llm

if not os.getenv("PELLIPLAN_NO_DOTENV"):   # tests set this so a real .env can never leak a key into them
    try:
        from dotenv import load_dotenv
        load_dotenv(D.ROOT / ".env")
    except Exception:
        pass

ROOT = D.ROOT
MAX_UPLOAD = 10 * 1024 * 1024
ALLOWED = {".txt": "text", ".md": "text", ".csv": "text", ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image"}
MAGIC = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8", ".webp": b"RIFF"}
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def today():
    t = os.getenv("PELLIPLAN_TODAY")
    return date.fromisoformat(t) if t else date.today()


def get_conn():
    conn = D.connect()
    D.init(conn)
    try:
        yield conn
    finally:
        conn.close()


def get_member(x_token: Optional[str] = Header(default=None), conn=Depends(get_conn)):
    m = auth.member_from_token(conn, x_token)
    if not m:
        raise HTTPException(401, "missing or invalid token")
    return m


def bride(m=Depends(get_member)):
    if m["role"] != auth.BRIDE:
        raise HTTPException(403, "only the bride can do this")
    return m


@asynccontextmanager
async def lifespan(app):
    c = D.connect()
    D.init(c)
    c.close()
    yield

app = FastAPI(title="PelliPlan", lifespan=lifespan)


# ------------------------------------------------------------------ demo (local only)
def demo_only():
    if os.getenv("PELLIPLAN_DEMO") != "1":
        raise HTTPException(404, "demo mode is off")


@app.post("/api/demo/reset")
def demo_reset(_=Depends(demo_only), conn=Depends(get_conn)):
    _, tokens = seed.reset_demo(conn, today())
    rows = D.many(conn, "SELECT name, role FROM member ORDER BY id")
    return [dict(r, token=tokens[r["name"]]) for r in rows]


@app.get("/api/demo/members")
def demo_members(_=Depends(demo_only), conn=Depends(get_conn)):
    """Only available when PELLIPLAN_DEMO=1. Tokens for the demo wedding are returned by /api/demo/reset."""
    return D.many(conn, "SELECT name, role FROM member ORDER BY id")


# ------------------------------------------------------------------ read
@app.get("/api/me")
def me(m=Depends(get_member)):
    return dict(id=m["id"], name=m["name"], role=m["role"], llm="ai" if get_llm() else "rules")


@app.get("/api/plan")
def plan(m=Depends(get_member), conn=Depends(get_conn)):
    return service.plan_view(conn, m, today())


@app.get("/api/activity")
def activity(limit: int = 60, m=Depends(get_member), conn=Depends(get_conn)):
    rows = D.many(conn, "SELECT a.*, mm.name AS actor FROM activity a LEFT JOIN member mm ON mm.id = a.actor_id WHERE a.wedding_id = ? ORDER BY a.id DESC LIMIT ?",
                  (m["wedding_id"], min(limit, 200) * (1 if m["role"] == auth.BRIDE else 5)))
    if m["role"] != auth.BRIDE:
        approver = auth.approver_task_ids(conn, m)
        visible = {t["id"] for t in D.many(conn, "SELECT * FROM task WHERE wedding_id = ?", (m["wedding_id"],)) if auth.can_view(m, t, approver)}
        rows = [r for r in rows if r["entity"] == "task" and r["entity_id"] in visible]
    out = []
    for r in rows[:limit]:
        det = json.loads(r["detail"] or "{}")
        if m["role"] != auth.BRIDE:
            det = {k: v for k, v in det.items() if k not in auth.MONEY_FIELDS and k not in ("cost", "total_cost", "steps")}
        out.append(dict(id=r["id"], ts=r["ts"], actor=r["actor"], action=r["action"], entity=r["entity"], entity_id=r["entity_id"], detail=det))
    return out


# ------------------------------------------------------------------ tasks
class TaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=140)
    ceremony_id: Optional[int] = None
    description: Optional[str] = Field(default=None, max_length=500)
    duration_days: int = Field(default=1, ge=0, le=365)
    due_date: Optional[date] = None
    assignee_id: Optional[int] = None
    visibility: str = Field(default="team", pattern="^(private|team|family)$")
    amount: Optional[float] = Field(default=None, ge=0, le=1e8)
    notes_private: Optional[str] = Field(default=None, max_length=1000)
    expedite_days: int = Field(default=0, ge=0, le=60)
    expedite_cost: float = Field(default=0, ge=0, le=1e8)
    is_decision: bool = False


class TaskPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=140)
    ceremony_id: Optional[int] = None
    description: Optional[str] = Field(default=None, max_length=500)
    duration_days: Optional[int] = Field(default=None, ge=0, le=365)
    due_date: Optional[date] = None
    not_before: Optional[date] = None
    assignee_id: Optional[int] = None
    visibility: Optional[str] = Field(default=None, pattern="^(private|team|family)$")
    amount: Optional[float] = Field(default=None, ge=0, le=1e8)
    notes_private: Optional[str] = Field(default=None, max_length=1000)
    expedite_days: Optional[int] = Field(default=None, ge=0, le=60)
    expedite_cost: Optional[float] = Field(default=None, ge=0, le=1e8)
    status: Optional[str] = Field(default=None, pattern="^(todo|in_progress|done|blocked)$")


def _own_task(conn, m, task_id):
    t = D.one(conn, "SELECT * FROM task WHERE id = ? AND wedding_id = ?", (task_id, m["wedding_id"]))
    if not t:
        raise HTTPException(404, "task not found")
    return t


def _check_refs(conn, m, ceremony_id, assignee_id):
    if ceremony_id is not None and not D.one(conn, "SELECT 1 FROM ceremony WHERE id = ? AND wedding_id = ?", (ceremony_id, m["wedding_id"])):
        raise HTTPException(422, "unknown ceremony")
    if assignee_id is not None and not D.one(conn, "SELECT 1 FROM member WHERE id = ? AND wedding_id = ?", (assignee_id, m["wedding_id"])):
        raise HTTPException(422, "unknown member")


@app.post("/api/tasks", status_code=201)
def create_task(body: TaskIn, m=Depends(bride), conn=Depends(get_conn)):
    _check_refs(conn, m, body.ceremony_id, body.assignee_id)
    tid = conn.execute(
        """INSERT INTO task (wedding_id, ceremony_id, title, description, duration_days, due_date, assignee_id, visibility, amount, notes_private,
           expedite_days, expedite_cost, is_decision, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (m["wedding_id"], body.ceremony_id, body.title, body.description, body.duration_days, body.due_date and body.due_date.isoformat(),
         body.assignee_id, body.visibility, body.amount, body.notes_private, body.expedite_days, body.expedite_cost, int(body.is_decision), m["id"], D.now())).lastrowid
    D.log(conn, m["wedding_id"], m["id"], "created", "task", tid, title=body.title)
    conn.commit()
    return dict(id=tid)


@app.patch("/api/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, m=Depends(get_member), conn=Depends(get_conn)):
    t = _own_task(conn, m, task_id)
    approver = auth.approver_task_ids(conn, m)
    if not auth.can_view(m, t, approver):
        raise HTTPException(404, "task not found")
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return dict(ok=True)
    if m["role"] != auth.BRIDE:
        if set(fields) != {"status"} or not auth.can_change_status(m, t):
            raise HTTPException(403, "you can only update the status of tasks assigned to you")
    _check_refs(conn, m, fields.get("ceremony_id"), fields.get("assignee_id"))
    if "status" in fields:
        fields["completed_on"] = today().isoformat() if fields["status"] == "done" else None
    for k in ("due_date", "not_before"):
        if k in fields and fields[k] is not None:
            fields[k] = fields[k].isoformat()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE task SET {sets} WHERE id = ?", (*fields.values(), task_id))
    changed = {k: {"from": t.get(k), "to": v} for k, v in fields.items() if t.get(k) != v}
    D.log(conn, m["wedding_id"], m["id"], "updated", "task", task_id, title=t["title"], **changed)
    conn.commit()
    return dict(ok=True)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, m=Depends(bride), conn=Depends(get_conn)):
    t = _own_task(conn, m, task_id)
    conn.execute("DELETE FROM task WHERE id = ?", (task_id,))
    D.log(conn, m["wedding_id"], m["id"], "deleted", "task", task_id, title=t["title"])
    conn.commit()
    return dict(ok=True)


class DepIn(BaseModel):
    depends_on_id: int


def _add_dependency(conn, wedding_id, task_id, dep_id):
    g = service.load_graph(conn, wedding_id)
    if task_id not in g["preds"] or dep_id not in g["preds"]:
        raise HTTPException(404, "task not found")
    loop = E.would_create_cycle(g["preds"], task_id, dep_id)
    if loop:
        names = {t["id"]: t["title"] for t in g["tasks"]}
        raise HTTPException(409, "that would create a circular dependency: " + " -> ".join(names[i] for i in loop))
    conn.execute("INSERT OR IGNORE INTO dependency (task_id, depends_on_id) VALUES (?,?)", (task_id, dep_id))


@app.post("/api/tasks/{task_id}/dependencies", status_code=201)
def add_dependency(task_id: int, body: DepIn, m=Depends(bride), conn=Depends(get_conn)):
    _own_task(conn, m, task_id)
    _own_task(conn, m, body.depends_on_id)
    _add_dependency(conn, m["wedding_id"], task_id, body.depends_on_id)
    D.log(conn, m["wedding_id"], m["id"], "linked", "task", task_id, depends_on=body.depends_on_id)
    conn.commit()
    return dict(ok=True)


@app.delete("/api/tasks/{task_id}/dependencies/{dep_id}")
def remove_dependency(task_id: int, dep_id: int, m=Depends(bride), conn=Depends(get_conn)):
    _own_task(conn, m, task_id)
    conn.execute("DELETE FROM dependency WHERE task_id = ? AND depends_on_id = ?", (task_id, dep_id))
    D.log(conn, m["wedding_id"], m["id"], "unlinked", "task", task_id, depends_on=dep_id)
    conn.commit()
    return dict(ok=True)


# ------------------------------------------------------------------ approvals (family)
class ApprovalIn(BaseModel):
    requested_from: int
    question: str = Field(min_length=3, max_length=300)


@app.post("/api/tasks/{task_id}/approval", status_code=201)
def request_approval(task_id: int, body: ApprovalIn, m=Depends(bride), conn=Depends(get_conn)):
    _own_task(conn, m, task_id)
    _check_refs(conn, m, None, body.requested_from)
    aid = conn.execute("INSERT INTO approval (task_id, requested_from, requested_by, question, created_at) VALUES (?,?,?,?,?)",
                       (task_id, body.requested_from, m["id"], body.question, D.now())).lastrowid
    D.log(conn, m["wedding_id"], m["id"], "requested_approval", "task", task_id, approver=body.requested_from)
    conn.commit()
    return dict(id=aid)


@app.get("/api/approvals")
def my_approvals(m=Depends(get_member), conn=Depends(get_conn)):
    rows = D.many(conn, """SELECT a.id, a.status, a.question, a.comment, t.id AS task_id, t.title, t.due_date FROM approval a
                           JOIN task t ON t.id = a.task_id WHERE a.requested_from = ? ORDER BY a.status = 'pending' DESC, a.id DESC""", (m["id"],))
    return rows   # title and deadline only: no money, notes or evidence ever leave this endpoint


class DecideIn(BaseModel):
    approve: bool
    comment: Optional[str] = Field(default=None, max_length=300)


@app.post("/api/approvals/{approval_id}/decide")
def decide_approval(approval_id: int, body: DecideIn, m=Depends(get_member), conn=Depends(get_conn)):
    a = D.one(conn, "SELECT * FROM approval WHERE id = ?", (approval_id,))
    if not a or a["requested_from"] != m["id"]:
        raise HTTPException(404, "approval not found")      # same answer for 'not yours' and 'does not exist'
    if a["status"] != "pending":
        raise HTTPException(409, "already decided")
    conn.execute("UPDATE approval SET status = ?, comment = ?, decided_at = ? WHERE id = ?",
                 ("approved" if body.approve else "rejected", body.comment, D.now(), approval_id))
    D.log(conn, m["wedding_id"], m["id"], "approved" if body.approve else "rejected", "task", a["task_id"], comment=body.comment)
    conn.commit()
    return dict(ok=True)


# ------------------------------------------------------------------ Wedding Brain: documents -> proposals
def _context(conn, m):
    g = service.load_graph(conn, m["wedding_id"])
    return dict(today=today().isoformat(), ceremonies=[dict(id=c["id"], name=c["name"], date=c["date"]) for c in g["ceremonies"]],
                members=D.many(conn, "SELECT id, name FROM member WHERE wedding_id = ?", (m["wedding_id"],))), g["tasks"]


@app.post("/api/documents", status_code=201)
def upload(file: UploadFile = File(...), m=Depends(get_member), conn=Depends(get_conn)):
    if m["role"] == auth.FAMILY:
        raise HTTPException(403, "family members cannot upload documents")
    name = Path(file.filename or "upload").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(415, "unsupported file type. Use txt, md, csv, pdf, png, jpg or webp")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than 10 MB")
    if not data:
        raise HTTPException(422, "file is empty")
    if ext in MAGIC and not data.startswith(MAGIC[ext]):
        raise HTTPException(415, "file contents do not match its extension")
    folder = ROOT / "data" / "uploads" / str(m["wedding_id"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{ext}"          # never use the user's file name on disk
    path.write_bytes(data)
    did = conn.execute("INSERT INTO document (wedding_id, filename, mime, path, size, uploaded_by, created_at) VALUES (?,?,?,?,?,?,?)",
                       (m["wedding_id"], name, ALLOWED[ext], str(path), len(data), m["id"], D.now())).lastrowid
    kind, text, media = ALLOWED[ext], None, None
    import base64
    if kind == "text":
        text = data.decode("utf-8", errors="replace")
    elif kind == "pdf":
        try:
            from io import BytesIO
            from pypdf import PdfReader
            text = "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(data)).pages).strip() or None
        except Exception:
            text = None
        if text is None:
            media = [dict(kind="pdf", data=base64.b64encode(data).decode())]
    else:
        media = [dict(kind="image", media_type=MEDIA[ext], data=base64.b64encode(data).decode())]
    ctx, tasks = _context(conn, m)
    llm = get_llm()
    if media and llm is None:
        raise HTTPException(422, "images and scanned PDFs need an AI key (ANTHROPIC_API_KEY). Text files and text PDFs work offline.")
    try:
        raw, mode = brain.extract_items(dict(text=text, media=media, filename=name), ctx, llm)
    except Exception as e:
        raise HTTPException(502, f"the AI service failed: {type(e).__name__}")
    items, dropped = brain.validate_items(raw, dict(text=text), ctx, tasks)
    payload = dict(mode=mode, filename=name, dropped_invalid=dropped, items=items)
    pid = conn.execute("INSERT INTO proposal (wedding_id, kind, payload, doc_id, created_by, created_at) VALUES (?,?,?,?,?,?)",
                       (m["wedding_id"], "extraction", json.dumps(payload), did, m["id"], D.now())).lastrowid
    D.log(conn, m["wedding_id"], m["id"], "uploaded", "document", did, filename=name, items_found=len(items), mode=mode)
    conn.commit()
    return dict(proposal_id=pid, document_id=did, **payload) if m["role"] == auth.BRIDE else dict(proposal_id=pid, document_id=did, items_found=len(items), note="The bride will review these before anything is added.")


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int, m=Depends(bride), conn=Depends(get_conn)):
    d = D.one(conn, "SELECT * FROM document WHERE id = ? AND wedding_id = ?", (doc_id, m["wedding_id"]))
    if not d:
        raise HTTPException(404, "document not found")
    return FileResponse(d["path"], filename=d["filename"])


@app.get("/api/proposals")
def proposals(m=Depends(bride), conn=Depends(get_conn)):
    rows = D.many(conn, "SELECT * FROM proposal WHERE wedding_id = ? ORDER BY id DESC LIMIT 30", (m["wedding_id"],))
    return [dict(r, payload=json.loads(r["payload"])) for r in rows]


class Overrides(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=140)
    due_date: Optional[date] = None
    ceremony_id: Optional[int] = None
    assignee_id: Optional[int] = None
    duration_days: Optional[int] = Field(default=None, ge=0, le=365)
    amount: Optional[float] = Field(default=None, ge=0, le=1e8)
    visibility: Optional[str] = Field(default=None, pattern="^(private|team|family)$")


class ConfirmIn(BaseModel):
    acknowledge: bool = False
    overrides: Overrides = Overrides()


BLOCKING = {"evidence_not_in_document", "image_source", "possible_duplicate", "amount_not_in_document"}


def _get_proposal(conn, m, pid):
    p = D.one(conn, "SELECT * FROM proposal WHERE id = ? AND wedding_id = ? AND kind = 'extraction'", (pid, m["wedding_id"]))
    if not p:
        raise HTTPException(404, "proposal not found")
    p["payload"] = json.loads(p["payload"])
    return p


def _save_proposal(conn, p):
    its = p["payload"]["items"]
    pending = any(i["status"] == "pending" for i in its)
    status = "pending" if pending else ("approved" if any(i["status"] == "confirmed" for i in its) else "rejected")
    conn.execute("UPDATE proposal SET payload = ?, status = ?, decided_at = ? WHERE id = ?",
                 (json.dumps(p["payload"]), status, None if pending else D.now(), p["id"]))


def _link_confirmed(conn, wedding_id, items):
    for it in items:
        if it["status"] != "confirmed" or not it.get("task_id"):
            continue
        for ln in it["links"]:
            tgt = ln["ref"] if ln["kind"] == "task" else (items[ln["ref"]].get("task_id") if items[ln["ref"]]["status"] == "confirmed" else None)
            if tgt:
                try:
                    _add_dependency(conn, wedding_id, it["task_id"], tgt)
                except HTTPException:
                    pass   # a link that would loop is skipped, never forced


@app.post("/api/proposals/{pid}/items/{idx}/confirm")
def confirm_item(pid: int, idx: int, body: ConfirmIn, m=Depends(bride), conn=Depends(get_conn)):
    p = _get_proposal(conn, m, pid)
    items = p["payload"]["items"]
    if not (0 <= idx < len(items)):
        raise HTTPException(404, "item not found")
    it = items[idx]
    if it["status"] != "pending":
        raise HTTPException(409, "item already decided")
    risky = sorted(set(it["flags"]) & BLOCKING)
    if risky and not body.acknowledge:
        raise HTTPException(409, f"this item has warnings ({', '.join(risky)}). Review it, then confirm with acknowledge=true")
    o = body.overrides.model_dump(exclude_unset=True)
    _check_refs(conn, m, o.get("ceremony_id"), o.get("assignee_id"))
    f = dict(title=it["title"], ceremony_id=it["ceremony_id"], due_date=it["due_date"], assignee_id=it["assignee_id"],
             duration_days=it["duration_days"], amount=it["amount"], visibility="team")
    for k, v in o.items():
        f[k] = v.isoformat() if isinstance(v, date) else v
    tid = conn.execute(
        """INSERT INTO task (wedding_id, ceremony_id, title, duration_days, due_date, assignee_id, visibility, amount, is_decision,
           source_doc_id, evidence, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (m["wedding_id"], f["ceremony_id"], f["title"], f["duration_days"], f["due_date"], f["assignee_id"], f["visibility"], f["amount"],
         int(it["kind"] == "decision"), p["doc_id"], it["evidence"], m["id"], D.now())).lastrowid
    it["status"], it["task_id"] = "confirmed", tid
    _link_confirmed(conn, m["wedding_id"], items)
    _save_proposal(conn, p)
    D.log(conn, m["wedding_id"], m["id"], "confirmed_ai_item", "task", tid, title=f["title"], flags=it["flags"], source_document=p["doc_id"])
    conn.commit()
    return dict(task_id=tid)


@app.post("/api/proposals/{pid}/items/{idx}/reject")
def reject_item(pid: int, idx: int, m=Depends(bride), conn=Depends(get_conn)):
    p = _get_proposal(conn, m, pid)
    items = p["payload"]["items"]
    if not (0 <= idx < len(items)) or items[idx]["status"] != "pending":
        raise HTTPException(404, "item not found or already decided")
    items[idx]["status"] = "rejected"
    _save_proposal(conn, p)
    D.log(conn, m["wedding_id"], m["id"], "rejected_ai_item", "document", p["doc_id"], title=items[idx]["title"])
    conn.commit()
    return dict(ok=True)


# ------------------------------------------------------------------ Wedding Brain: ask
class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)
    ceremony_id: Optional[int] = None


@app.post("/api/ask")
def ask(body: AskIn, m=Depends(get_member), conn=Depends(get_conn)):
    view = service.plan_view(conn, m, today())        # permission filtering happens BEFORE the AI sees anything
    cer = next((c for c in view["ceremonies"] if c["id"] == body.ceremony_id), None) if body.ceremony_id else brain.pick_ceremony(body.question, view["ceremonies"])
    if not cer:
        return dict(needs_ceremony=True, ceremonies=[dict(id=c["id"], name=c["name"]) for c in view["ceremonies"]])
    tasks = [t for t in view["tasks"] if t["ceremony_id"] == cer["id"]]
    try:
        res = brain.ask(body.question, cer, tasks, m["role"] == auth.BRIDE, get_llm())
    except Exception as e:
        raise HTTPException(502, f"the AI service failed: {type(e).__name__}")
    D.log(conn, m["wedding_id"], m["id"], "asked", "ask", None, ceremony=cer["name"], mode=res["mode"])
    conn.commit()
    return res


# ------------------------------------------------------------------ what-if and recovery
class WhatIfIn(BaseModel):
    task_id: int
    slip_days: int = Field(ge=1, le=90)


def _whatif(conn, m, body):
    g = service.load_graph(conn, m["wedding_id"])
    if body.task_id not in g["nodes"]:
        raise HTTPException(404, "task not found")
    if g["nodes"][body.task_id].status == "done":
        raise HTTPException(422, "that task is already done")
    t = today()
    slips = {body.task_id: body.slip_days}
    imp = E.impact(g["nodes"], g["preds"], t, slips)
    rec = E.plan_recovery(g["nodes"], g["preds"], t, slips)
    title = {x["id"]: x["title"] for x in g["tasks"]}
    who = {x["id"]: x["assignee_id"] for x in g["tasks"]}
    dl = lambda i: g["nodes"][i].deadline
    facts = dict(
        slipped=dict(title=title[body.task_id], days=body.slip_days),
        moved=[dict(id=a["id"], title=title[a["id"]], shift_days=a["shift_days"], new_finish=brain.fmt_date(a["ef_after"]), chain=[title[c] for c in a["chain"]]) for a in imp["affected"]],
        pressured=[dict(id=p["id"], title=title[p["id"]]) for p in imp["pressured"]],
        deadline_misses=[dict(id=x["id"], title=title[x["id"]], late_days=x["late_days"], deadline=brain.fmt_date(dl(x["id"])), newly=x["newly"]) for x in imp["deadline_misses"]],
        ceremonies_at_risk=sorted({c["name"] for c in g["ceremonies"] if any(g["task_cer"][x["id"]] == c["id"] for x in imp["deadline_misses"])}),
        recovery=dict(steps=[dict(task_id=s["task_id"], title=title[s["task_id"]], cut_days=s["cut_days"], cost=s["cost"]) for s in rec["steps"]],
                      total_cost=rec["total_cost"], fully_recovered=rec["fully_recovered"],
                      remaining=[dict(id=r["id"], title=title[r["id"]], late_days=r["late_days"]) for r in rec["remaining_late"]]),
        notify=sorted({who[i] for i in [a["id"] for a in imp["affected"]] if who.get(i)}))
    return facts, rec, imp


@app.post("/api/whatif")
def whatif(body: WhatIfIn, m=Depends(bride), conn=Depends(get_conn)):
    """Pure simulation: nothing is saved."""
    facts, _, _ = _whatif(conn, m, body)
    try:
        expl = brain.explain_whatif(facts, get_llm())
    except Exception as e:
        raise HTTPException(502, f"the AI service failed: {type(e).__name__}")
    return dict(facts=facts, explanation=expl)


@app.post("/api/whatif/propose", status_code=201)
def whatif_propose(body: WhatIfIn, m=Depends(bride), conn=Depends(get_conn)):
    facts, _, _ = _whatif(conn, m, body)
    payload = dict(slip=dict(task_id=body.task_id, add_days=body.slip_days), steps=facts["recovery"]["steps"], facts=facts)
    pid = conn.execute("INSERT INTO proposal (wedding_id, kind, payload, created_by, created_at) VALUES (?,?,?,?,?)",
                       (m["wedding_id"], "recovery", json.dumps(payload), m["id"], D.now())).lastrowid
    D.log(conn, m["wedding_id"], m["id"], "proposed_recovery", "task", body.task_id, slip_days=body.slip_days)
    conn.commit()
    return dict(proposal_id=pid)


class DecideProposal(BaseModel):
    approve: bool


@app.post("/api/proposals/{pid}/decide")
def decide_proposal(pid: int, body: DecideProposal, m=Depends(bride), conn=Depends(get_conn)):
    p = D.one(conn, "SELECT * FROM proposal WHERE id = ? AND wedding_id = ? AND kind = 'recovery'", (pid, m["wedding_id"]))
    if not p:
        raise HTTPException(404, "proposal not found")
    if p["status"] != "pending":
        raise HTTPException(409, "already decided")
    if not body.approve:
        conn.execute("UPDATE proposal SET status='rejected', decided_by=?, decided_at=? WHERE id=?", (m["id"], D.now(), pid))
        D.log(conn, m["wedding_id"], m["id"], "rejected_recovery", "proposal", pid)
        conn.commit()
        return dict(applied=False)
    pl = json.loads(p["payload"])
    s = pl["slip"]
    conn.execute("UPDATE task SET duration_days = duration_days + ? WHERE id = ? AND wedding_id = ?", (s["add_days"], s["task_id"], m["wedding_id"]))
    D.log(conn, m["wedding_id"], m["id"], "recorded_delay", "task", s["task_id"], add_days=s["add_days"])
    for st in pl["steps"]:
        t = D.one(conn, "SELECT * FROM task WHERE id = ?", (st["task_id"],))
        conn.execute("UPDATE task SET duration_days = MAX(0, duration_days - ?), expedite_days = MAX(0, expedite_days - ?), expedite_cost = MAX(0, expedite_cost - ?), amount = COALESCE(amount, 0) + ? WHERE id = ?",
                     (st["cut_days"], st["cut_days"], st["cost"], st["cost"], st["task_id"]))
        D.log(conn, m["wedding_id"], m["id"], "expedited", "task", st["task_id"], cut_days=st["cut_days"], cost=st["cost"], title=t["title"])
    conn.execute("UPDATE proposal SET status='approved', decided_by=?, decided_at=? WHERE id=?", (m["id"], D.now(), pid))
    conn.commit()
    return dict(applied=True)


# ------------------------------------------------------------------ static UI
STATIC = ROOT / "static"
if STATIC.exists():
    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
