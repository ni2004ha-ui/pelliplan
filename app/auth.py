"""Who can see and change what. Every rule lives here so it can be read, tested and audited in one place.
  bride        everything, including budget and private notes
  bridesmaid   tasks marked 'team' or 'family'; may update the status of tasks assigned to them; NO money, NO private notes
  family       tasks marked 'family', plus any task they were asked to approve; may decide those approvals; NO money
Hiding is done on the server. The browser is never trusted to hide anything."""
from . import db as D

BRIDE, BRIDESMAID, FAMILY = "bride", "bridesmaid", "family"
MONEY_FIELDS = ("amount", "notes_private", "evidence", "source_doc_id", "expedite_days", "expedite_cost")


class Forbidden(Exception):
    pass


def member_from_token(conn, token):
    if not token:
        return None
    return D.one(conn, "SELECT * FROM member WHERE token_hash = ?", (D.hash_token(token),))


def approver_task_ids(conn, member):
    return {r["task_id"] for r in D.many(conn, "SELECT task_id FROM approval WHERE requested_from = ?", (member["id"],))}


def can_view(member, task, approver_ids=frozenset()):
    r = member["role"]
    if r == BRIDE:
        return True
    if r == BRIDESMAID:
        return task["visibility"] in ("team", "family")
    return task["visibility"] == "family" or task["id"] in approver_ids


def can_change_status(member, task):
    if member["role"] == BRIDE:
        return True
    return member["role"] == BRIDESMAID and task["assignee_id"] == member["id"] and task["visibility"] in ("team", "family")


def require_bride(member):
    if member["role"] != BRIDE:
        raise Forbidden("only the bride can do this")


def redact(member, task):
    """Remove money and private fields for anyone but the bride."""
    t = dict(task)
    if member["role"] != BRIDE:
        for f in MONEY_FIELDS:
            t.pop(f, None)
    return t
