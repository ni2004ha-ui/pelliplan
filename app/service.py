"""Glue between the database and the pure engine."""
from datetime import date

from . import auth
from . import db as D
from . import engine as E


def pdate(s):
    return date.fromisoformat(s) if s else None


def load_graph(conn, wedding_id):
    ceremonies = D.many(conn, "SELECT * FROM ceremony WHERE wedding_id = ? ORDER BY sort, date", (wedding_id,))
    cdate = {c["id"]: pdate(c["date"]) for c in ceremonies}
    tasks = D.many(conn, "SELECT * FROM task WHERE wedding_id = ? ORDER BY id", (wedding_id,))
    ids = [t["id"] for t in tasks]
    edges = [(r["task_id"], r["depends_on_id"]) for r in D.many(
        conn, "SELECT d.task_id, d.depends_on_id FROM dependency d JOIN task t ON t.id = d.task_id WHERE t.wedding_id = ?", (wedding_id,))]
    preds = E.build_preds(ids, edges)
    nodes = {}
    for t in tasks:
        nodes[t["id"]] = E.Node(
            id=t["id"], duration=0 if t["status"] == "done" else t["duration_days"],
            deadline=pdate(t["due_date"]) or cdate.get(t["ceremony_id"]), status=t["status"],
            completed_on=pdate(t["completed_on"]), not_before=pdate(t["not_before"]),
            expedite_days=t["expedite_days"], expedite_cost=t["expedite_cost"])
    return dict(ceremonies=ceremonies, tasks=tasks, nodes=nodes, preds=preds, edges=edges,
                task_cer={t["id"]: t["ceremony_id"] for t in tasks})


def plan_view(conn, member, today):
    g = load_graph(conn, member["wedding_id"])
    sched = E.schedule(g["nodes"], g["preds"], today)
    approver = auth.approver_task_ids(conn, member)
    names = {m["id"]: m["name"] for m in D.many(conn, "SELECT id, name FROM member WHERE wedding_id = ?", (member["wedding_id"],))}
    visible = {t["id"]: t for t in g["tasks"] if auth.can_view(member, t, approver)}
    vpreds = {i: {p for p in g["preds"][i] if p in visible} for i in visible}
    depth = E.depths(vpreds)
    approvals = D.many(conn, "SELECT * FROM approval WHERE task_id IN (SELECT id FROM task WHERE wedding_id = ?)", (member["wedding_id"],))
    out = []
    for i, t in visible.items():
        s = sched[i]
        row = auth.redact(member, t)
        row.update(
            assignee_name=names.get(t["assignee_id"]), depends_on=sorted(vpreds[i]),
            hidden_blockers=len(g["preds"][i]) - len(vpreds[i]),
            effective_deadline=(g["nodes"][i].deadline.isoformat() if g["nodes"][i].deadline else None),
            es=s.es.isoformat(), ef=s.ef.isoformat(), slack=s.slack, late_days=s.late_days,
            critical=s.critical and t["status"] != "done", tight=s.tight and t["status"] != "done", depth=depth[i],
            can_change_status=auth.can_change_status(member, t), can_edit=member["role"] == auth.BRIDE,
            approvals=[dict(id=a["id"], status=a["status"], requested_from=a["requested_from"], question=a["question"])
                       for a in approvals if a["task_id"] == i and (member["role"] == auth.BRIDE or a["requested_from"] == member["id"])])
        out.append(row)
    risk = E.ceremony_risk(g["task_cer"], sched, g["nodes"])
    cer = []
    for c in g["ceremonies"]:
        mine = [t for t in out if t["ceremony_id"] == c["id"]]
        cer.append(dict(c, risk=risk.get(c["id"], "ok"), total=len(mine), done=sum(1 for t in mine if t["status"] == "done")))
    return dict(today=today.isoformat(), ceremonies=cer, tasks=out,
                members=[dict(id=m["id"], name=m["name"], role=m["role"]) for m in D.many(conn, "SELECT id, name, role FROM member WHERE wedding_id = ?", (member["wedding_id"],))])
