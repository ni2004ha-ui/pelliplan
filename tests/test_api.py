import io, json, os
import pytest
from conftest import by_title

MONEY = {"amount", "notes_private", "evidence", "source_doc_id", "expedite_days", "expedite_cost"}
def plan(c, who): return c.get("/api/plan", headers=c.h(who)).json()
def tid(c, text, who="Sravya"): return by_title(plan(c, who), text)["id"]


# ---------------------------------------------------------------- authentication
def test_no_token_or_bad_token_is_rejected(client):
    assert client.get("/api/plan").status_code == 401
    assert client.get("/api/plan", headers={"X-Token": "nope"}).status_code == 401

def test_tokens_are_stored_hashed(client):
    from app import db as D
    rows = D.many(D.connect(), "SELECT token_hash FROM member")
    assert all(len(r["token_hash"]) == 64 and r["token_hash"] not in client.tok.values() for r in rows)


# ---------------------------------------------------------------- who can see what
def test_each_role_sees_a_different_plan(client):
    bride, ann, venkat = plan(client, "Sravya"), plan(client, "Anusha"), plan(client, "Venkat Garu")
    assert len(bride["tasks"]) > len(ann["tasks"]) > len(venkat["tasks"])
    titles = lambda p: {t["title"] for t in p["tasks"]}
    assert "Surprise gift for parents" in titles(bride) and "Surprise gift for parents" not in titles(ann)
    assert "Collect bridal jewellery from jeweller" not in titles(ann)
    assert all(t["visibility"] == "family" or t["title"].startswith("Finalise catering") for t in venkat["tasks"])

@pytest.mark.parametrize("who", ["Anusha", "Divya", "Venkat Garu", "Lakshmi Atha"])
def test_money_and_private_fields_never_reach_non_brides(client, who):
    raw = json.dumps(plan(client, who))
    for t in plan(client, who)["tasks"]:
        assert not (MONEY & set(t))
    for secret in ("Do not mention to anyone", "Aunt suggested", "350000", "85000", "Jewellery budget"):
        assert secret not in raw

def test_bride_does_see_money_and_notes(client):
    t = by_title(plan(client, "Sravya"), "Buy bridal kanchi")
    assert t["amount"] == 85000 and "backup saree" in t["notes_private"]

def test_hidden_blockers_are_counted_not_named(client):
    c = client
    gid = tid(c, "Surprise gift")
    # make a team task depend on the private gift task: the bridesmaid must not learn the gift task exists
    dec = tid(c, "Confirm decor plan")
    assert c.post(f"/api/tasks/{dec}/dependencies", headers=c.h("Sravya"), json={"depends_on_id": gid}).status_code == 201
    ann = by_title(plan(c, "Anusha"), "Confirm decor plan") if False else by_title(plan(c, "Divya"), "Confirm decor plan")
    assert ann["hidden_blockers"] == 1 and gid not in ann["depends_on"]
    assert "Surprise gift" not in json.dumps(plan(c, "Divya"))

def test_family_sees_an_approval_task_but_only_title_and_deadline(client):
    rows = client.get("/api/approvals", headers=client.h("Venkat Garu")).json()
    assert len(rows) == 1 and set(rows[0]) == {"id", "status", "question", "comment", "task_id", "title", "due_date"}
    assert not (MONEY & set(rows[0]))


# ---------------------------------------------------------------- who can change what
def test_bridesmaid_can_update_only_the_status_of_her_own_tasks(client):
    c = client
    mine, theirs = tid(c, "Blouse fitting"), tid(c, "Book DJ")            # Anusha's, Divya's
    assert c.patch(f"/api/tasks/{mine}", headers=c.h("Anusha"), json={"status": "in_progress"}).status_code == 200
    assert c.patch(f"/api/tasks/{theirs}", headers=c.h("Anusha"), json={"status": "done"}).status_code == 403
    assert c.patch(f"/api/tasks/{mine}", headers=c.h("Anusha"), json={"title": "hacked"}).status_code == 403
    assert c.patch(f"/api/tasks/{mine}", headers=c.h("Anusha"), json={"status": "done", "amount": 1}).status_code == 403
    assert c.patch(f"/api/tasks/{mine}", headers=c.h("Anusha"), json={"assignee_id": 1}).status_code == 403

def test_bridesmaid_cannot_touch_a_private_task_and_gets_404_not_403(client):
    gid = tid(client, "Surprise gift")
    assert client.patch(f"/api/tasks/{gid}", headers=client.h("Anusha"), json={"status": "done"}).status_code == 404

def test_family_and_bridesmaid_cannot_create_or_delete(client):
    for who in ("Anusha", "Venkat Garu"):
        assert client.post("/api/tasks", headers=client.h(who), json={"title": "x task"}).status_code == 403
        assert client.delete(f"/api/tasks/{tid(client, 'Book DJ')}", headers=client.h(who)).status_code == 403

def test_only_the_asked_family_member_can_decide_and_only_once(client):
    c = client
    aid = c.get("/api/approvals", headers=c.h("Venkat Garu")).json()[0]["id"]
    assert c.post(f"/api/approvals/{aid}/decide", headers=c.h("Lakshmi Atha"), json={"approve": True}).status_code == 404
    assert c.post(f"/api/approvals/{aid}/decide", headers=c.h("Anusha"), json={"approve": True}).status_code == 404
    assert c.post(f"/api/approvals/{aid}/decide", headers=c.h("Venkat Garu"), json={"approve": True, "comment": "ok"}).status_code == 200
    assert c.post(f"/api/approvals/{aid}/decide", headers=c.h("Venkat Garu"), json={"approve": False}).status_code == 409
    assert c.get("/api/approvals", headers=c.h("Venkat Garu")).json()[0]["status"] == "approved"

def test_marking_done_stamps_the_date_and_frees_the_schedule(client):
    c = client
    d = tid(c, "Deliver saree")
    before = by_title(plan(c, "Sravya"), "Stitch bridal blouse")["es"]
    assert c.patch(f"/api/tasks/{d}", headers=c.h("Anusha"), json={"status": "done"}).status_code == 200
    t = by_title(plan(c, "Sravya"), "Deliver saree")
    assert t["status"] == "done" and t["completed_on"] == "2026-10-27"
    assert by_title(plan(c, "Sravya"), "Stitch bridal blouse")["es"] <= before


# ---------------------------------------------------------------- dependencies
def test_cycle_is_refused_with_the_loop_spelled_out(client):
    c = client
    r = c.post(f"/api/tasks/{tid(c, 'Buy bridal kanchi')}/dependencies", headers=c.h("Sravya"), json={"depends_on_id": tid(c, "Blouse alterations")})
    assert r.status_code == 409 and "circular" in r.json()["detail"] and "Blouse alterations" in r.json()["detail"]

def test_cannot_link_to_a_task_in_another_wedding(client):
    from app import db as D, seed
    from datetime import date
    conn = D.connect()
    wid2, _ = seed.create_demo(conn, date(2026, 10, 27))
    foreign = D.one(conn, "SELECT id FROM task WHERE wedding_id = ? LIMIT 1", (wid2,))["id"]
    mine = tid(client, "Book DJ")
    assert client.post(f"/api/tasks/{mine}/dependencies", headers=client.h("Sravya"), json={"depends_on_id": foreign}).status_code == 404
    assert client.patch(f"/api/tasks/{foreign}", headers=client.h("Sravya"), json={"title": "pwned"}).status_code == 404

def test_sql_injection_text_is_stored_as_plain_text(client):
    evil = "x'); DROP TABLE task;--"
    assert client.post("/api/tasks", headers=client.h("Sravya"), json={"title": evil}).status_code == 201
    assert evil in {t["title"] for t in plan(client, "Sravya")["tasks"]}

def test_input_validation(client):
    h = client.h("Sravya")
    assert client.post("/api/tasks", headers=h, json={"title": "ok title", "visibility": "everyone"}).status_code == 422
    assert client.post("/api/tasks", headers=h, json={"title": "ok title", "amount": -5}).status_code == 422
    assert client.post("/api/tasks", headers=h, json={"title": "ok title", "ceremony_id": 9999}).status_code == 422
    assert client.post("/api/tasks", headers=h, json={"title": "ok title", "assignee_id": 9999}).status_code == 422


# ---------------------------------------------------------------- what-if and recovery
def test_whatif_is_a_pure_simulation(client):
    c = client
    before = json.dumps(plan(c, "Sravya"), sort_keys=True)
    r = c.post("/api/whatif", headers=c.h("Sravya"), json={"task_id": tid(c, "Deliver saree"), "slip_days": 5}).json()
    f = r["facts"]
    assert len(f["moved"]) == 5 and f["deadline_misses"][0]["late_days"] == 3 and "Muhurtham" in f["ceremonies_at_risk"]
    assert f["recovery"]["total_cost"] == 3000 and f["recovery"]["fully_recovered"]
    assert json.dumps(plan(c, "Sravya"), sort_keys=True) == before

def test_whatif_is_bride_only_and_rejects_done_tasks(client):
    c = client
    assert c.post("/api/whatif", headers=c.h("Anusha"), json={"task_id": tid(c, "Deliver saree"), "slip_days": 5}).status_code == 403
    assert c.post("/api/whatif", headers=c.h("Sravya"), json={"task_id": tid(c, "Book purohit"), "slip_days": 5}).status_code == 422
    assert c.post("/api/whatif", headers=c.h("Sravya"), json={"task_id": tid(c, "Deliver saree"), "slip_days": 0}).status_code == 422

def test_recovery_proposal_changes_nothing_until_approved(client):
    c, d, st = client, None, None
    d, st = tid(c, "Deliver saree"), tid(c, "Stitch bridal blouse")
    pid = c.post("/api/whatif/propose", headers=c.h("Sravya"), json={"task_id": d, "slip_days": 5}).json()["proposal_id"]
    assert by_title(plan(c, "Sravya"), "Stitch bridal blouse")["duration_days"] == 10          # untouched
    assert c.post(f"/api/proposals/{pid}/decide", headers=c.h("Anusha"), json={"approve": True}).status_code == 403
    assert c.post(f"/api/proposals/{pid}/decide", headers=c.h("Sravya"), json={"approve": True}).json()["applied"]
    p = plan(c, "Sravya")
    assert by_title(p, "Deliver saree")["duration_days"] == 9                                   # delay recorded: 4 + 5
    stitch = by_title(p, "Stitch bridal blouse")
    assert stitch["duration_days"] == 7 and stitch["amount"] == 9500 and stitch["expedite_days"] == 1   # 3 days bought for Rs 3000
    assert by_title(p, "Blouse alterations")["late_days"] == 0                                  # engine now says the deadline holds
    assert c.post(f"/api/proposals/{pid}/decide", headers=c.h("Sravya"), json={"approve": True}).status_code == 409

def test_rejected_recovery_changes_nothing(client):
    c = client
    pid = c.post("/api/whatif/propose", headers=c.h("Sravya"), json={"task_id": tid(c, "Deliver saree"), "slip_days": 5}).json()["proposal_id"]
    assert not c.post(f"/api/proposals/{pid}/decide", headers=c.h("Sravya"), json={"approve": False}).json()["applied"]
    assert by_title(plan(c, "Sravya"), "Deliver saree")["duration_days"] == 4


# ---------------------------------------------------------------- activity history
def test_every_change_is_logged_and_non_brides_never_see_money_in_it(client):
    c = client
    c.patch(f"/api/tasks/{tid(c, 'Blouse fitting')}", headers=c.h("Anusha"), json={"status": "in_progress"})
    sid = tid(c, "Stitch bridal blouse")
    c.patch(f"/api/tasks/{sid}", headers=c.h("Sravya"), json={"amount": 777777})
    bride = c.get("/api/activity", headers=c.h("Sravya")).json()
    assert {"updated"} <= {a["action"] for a in bride} and "777777" in json.dumps(bride)
    ann = json.dumps(c.get("/api/activity", headers=c.h("Anusha")).json())
    assert "777777" not in ann and "amount" not in ann and "Blouse fitting" in ann
    assert c.get("/api/activity", headers=c.h("Venkat Garu")).status_code == 200


# ---------------------------------------------------------------- documents and the Wedding Brain
def upload(c, who, name, data, ctype="text/plain"):
    return c.post("/api/documents", headers=c.h(who), files={"file": (name, data, ctype)})

NOTES = open(os.path.join(os.path.dirname(__file__), "..", "samples", "planning_notes.txt"), "rb").read()

def test_upload_extracts_items_with_evidence_and_flags_a_duplicate(client):
    r = upload(client, "Sravya", "notes.txt", NOTES)
    assert r.status_code == 201
    body = r.json()
    assert body["mode"] == "rules" and len(body["items"]) >= 5
    by = {i["title"]: i for i in body["items"]}
    mk = next(i for t, i in by.items() if "makeup" in t.lower())
    assert "possible_duplicate" in mk["flags"] and mk["amount"] == 40000 and mk["assignee_name"] == "Anusha" and mk["evidence_verified"] is True
    assert mk["due_date"] == "2026-11-12"

def test_nothing_becomes_a_task_until_a_human_confirms(client):
    n = len(plan(client, "Sravya")["tasks"])
    upload(client, "Sravya", "notes.txt", NOTES)
    assert len(plan(client, "Sravya")["tasks"]) == n

def test_confirming_creates_a_task_linked_to_its_evidence_and_warnings_need_acknowledgement(client):
    c = client
    body = upload(c, "Sravya", "notes.txt", NOTES).json()
    dup = next(i for i in body["items"] if "possible_duplicate" in i["flags"])
    r = c.post(f"/api/proposals/{body['proposal_id']}/items/{dup['index']}/confirm", headers=c.h("Sravya"), json={})
    assert r.status_code == 409 and "possible_duplicate" in r.json()["detail"]
    ok = next(i for i in body["items"] if not (set(i["flags"]) & {"possible_duplicate", "image_source", "evidence_not_in_document", "amount_not_in_document"}))
    r = c.post(f"/api/proposals/{body['proposal_id']}/items/{ok['index']}/confirm", headers=c.h("Sravya"), json={})
    assert r.status_code == 200
    t = next(t for t in plan(c, "Sravya")["tasks"] if t["id"] == r.json()["task_id"])
    assert t["evidence"] and t["source_doc_id"] == body["document_id"]
    assert c.post(f"/api/proposals/{body['proposal_id']}/items/{ok['index']}/confirm", headers=c.h("Sravya"), json={}).status_code == 409

def test_overrides_let_the_bride_fix_a_wrong_extraction(client):
    c = client
    body = upload(c, "Sravya", "notes.txt", NOTES).json()
    it = next(i for i in body["items"] if "valet" in i["title"].lower())
    r = c.post(f"/api/proposals/{body['proposal_id']}/items/{it['index']}/confirm", headers=c.h("Sravya"),
               json={"acknowledge": True, "overrides": {"title": "Book valet parking (corrected)", "duration_days": 3, "visibility": "private"}})
    t = next(t for t in plan(c, "Sravya")["tasks"] if t["id"] == r.json()["task_id"])
    assert t["title"] == "Book valet parking (corrected)" and t["duration_days"] == 3 and t["visibility"] == "private"

def test_rejecting_items_and_proposal_status(client):
    c = client
    body = upload(c, "Sravya", "notes.txt", NOTES).json()
    for i in body["items"]:
        assert c.post(f"/api/proposals/{body['proposal_id']}/items/{i['index']}/reject", headers=c.h("Sravya")).status_code == 200
    assert c.get("/api/proposals", headers=c.h("Sravya")).json()[0]["status"] == "rejected"

def test_bridesmaid_can_upload_but_cannot_see_or_confirm(client):
    c = client
    r = upload(c, "Anusha", "notes.txt", NOTES)
    assert r.status_code == 201 and "items" not in r.json() and r.json()["items_found"] >= 5
    pid = r.json()["proposal_id"]
    assert c.get("/api/proposals", headers=c.h("Anusha")).status_code == 403
    assert c.post(f"/api/proposals/{pid}/items/0/confirm", headers=c.h("Anusha"), json={"acknowledge": True}).status_code == 403
    assert upload(c, "Venkat Garu", "notes.txt", NOTES).status_code == 403

def test_upload_security(client):
    c = client
    assert upload(c, "Sravya", "evil.exe", b"MZ").status_code == 415
    assert upload(c, "Sravya", "fake.pdf", b"not a pdf").status_code == 415
    assert upload(c, "Sravya", "fake.png", b"<html>").status_code == 415
    assert upload(c, "Sravya", "empty.txt", b"").status_code == 422
    assert upload(c, "Sravya", "big.txt", b"a" * (10 * 1024 * 1024 + 5)).status_code == 413
    r = upload(c, "Sravya", "../../etc/passwd.txt", b"Book valet parking by 30 Nov")
    assert r.status_code == 201
    from app import db as D
    path = D.one(D.connect(), "SELECT path FROM document ORDER BY id DESC")["path"]
    assert "passwd" not in path and ".." not in path

def test_images_need_an_ai_key_offline(client):
    r = upload(client, "Sravya", "shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50, "image/png")
    assert r.status_code == 422 and "AI key" in r.json()["detail"]

def test_instructions_inside_a_document_are_just_text(client):
    n = len(plan(client, "Sravya")["tasks"])
    done = sum(t["status"] == "done" for t in plan(client, "Sravya")["tasks"])
    body = upload(client, "Sravya", "inj.txt", open(os.path.join(os.path.dirname(__file__), "..", "samples", "injection_test.txt"), "rb").read()).json()
    assert [i["title"] for i in body["items"]] == ["Book valet parking"]
    p = plan(client, "Sravya")
    assert len(p["tasks"]) == n and sum(t["status"] == "done" for t in p["tasks"]) == done

def test_only_the_bride_can_download_documents(client):
    did = upload(client, "Anusha", "n.txt", NOTES).json()["document_id"]
    assert client.get(f"/api/documents/{did}/file", headers=client.h("Anusha")).status_code == 403
    assert client.get(f"/api/documents/{did}/file", headers=client.h("Sravya")).status_code == 200


# ---------------------------------------------------------------- "what am I forgetting?"
def test_ask_finds_missing_items_and_unfinished_tasks_without_any_ai(client):
    r = client.post("/api/ask", headers=client.h("Sravya"), json={"question": "What am I forgetting for my muhurtham?"}).json()
    assert r["mode"] == "rules" and r["ceremony"]["name"] == "Muhurtham"
    refs = [i["ref"] for i in r["items"]]
    assert "missing:groom_outfit" in refs and "missing:transport" not in refs
    assert any(i["ref"].startswith("task:") for i in r["items"])
    assert r["items"][0]["urgency"] in ("at_risk", "missing", "tight")
    assert all(i["why"] for i in r["items"])

def test_ask_without_a_known_ceremony_asks_which_one(client):
    r = client.post("/api/ask", headers=client.h("Sravya"), json={"question": "what is left to do?"}).json()
    assert r["needs_ceremony"] and len(r["ceremonies"]) == 4

def test_ask_for_a_bridesmaid_never_mentions_private_tasks_or_money(client):
    r = client.post("/api/ask", headers=client.h("Anusha"), json={"question": "What am I forgetting for muhurtham?"}).json()
    blob = json.dumps(r)
    assert "jewellery" not in blob.lower().replace("missing:jewellery", "") or "missing:jewellery" in [i["ref"] for i in r["items"]]
    assert "Surprise gift" not in blob and "350000" not in blob and "amount" not in blob

class Scripted:
    def __init__(self, out): self.out = out
    def complete(self, system, user, role="", media=None, max_tokens=0): return self.out

def test_ai_answer_is_used_when_valid(client, monkeypatch):
    import app.main as main
    task_ref = next(i["ref"] for i in client.post("/api/ask", headers=client.h("Sravya"), json={"question": "muhurtham?"}).json()["items"] if i["ref"].startswith("task:"))
    monkeypatch.setattr(main, "get_llm", lambda: Scripted(json.dumps({"summary": "A few things need attention.", "items": [{"ref": task_ref, "why": "It is on the critical path.", "next_step": "Call the tailor."}]})))
    r = client.post("/api/ask", headers=client.h("Sravya"), json={"question": "What am I forgetting for muhurtham?"}).json()
    got = next(i for i in r["items"] if i["ref"] == task_ref)
    assert r["mode"] == "ai" and got["from_ai"] and got["why"] == "It is on the critical path."

def test_ai_cannot_invent_numbers_or_items(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "get_llm", lambda: Scripted(json.dumps({"summary": "Fine.", "items": [
        {"ref": "task:9999", "why": "Invented item.", "next_step": "x"},
        {"ref": "missing:purohit", "why": "Costs Rs 91234 to fix.", "next_step": "Pay 777 now."}]})))
    r = client.post("/api/ask", headers=client.h("Sravya"), json={"question": "What am I forgetting for muhurtham?"}).json()
    assert r["mode"] == "rules"                         # everything rejected, so honest fallback
    assert "91234" not in json.dumps(r) and "task:9999" not in json.dumps(r)

def test_garbage_from_the_ai_falls_back_to_rules(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "get_llm", lambda: Scripted("sorry, I cannot help"))
    r = client.post("/api/ask", headers=client.h("Sravya"), json={"question": "What am I forgetting for muhurtham?"}).json()
    assert r["mode"] == "rules" and r["items"]

def test_ai_extraction_with_fenced_json_and_fabricated_evidence_is_flagged(client, monkeypatch):
    import app.main as main
    reply = '```json\n' + json.dumps({"items": [
        {"title": "Book valet parking", "ceremony": "Reception", "amount": 5000, "evidence": "Book valet parking by 30 Nov - Rs 5,000", "confidence": 0.9},
        {"title": "Hire a helicopter", "ceremony": "Muhurtham", "amount": 99999, "evidence": "we must hire a helicopter", "confidence": 0.9},
        {"title": "x", "evidence": "bad"}]}) + '\n```'
    monkeypatch.setattr(main, "get_llm", lambda: Scripted(reply))
    body = client.post("/api/documents", headers=client.h("Sravya"), files={"file": ("n.txt", b"Reception: Book valet parking by 30 Nov - Rs 5,000", "text/plain")}).json()
    assert body["mode"] == "ai" and body["dropped_invalid"] == 1 and len(body["items"]) == 2
    good, fake = body["items"]
    assert good["evidence_verified"] is True and "amount_not_in_document" not in good["flags"]
    assert {"evidence_not_in_document", "amount_not_in_document"} <= set(fake["flags"])
    r = client.post(f"/api/proposals/{body['proposal_id']}/items/{fake['index']}/confirm", headers=client.h("Sravya"), json={})
    assert r.status_code == 409                          # cannot confirm a fabricated item without explicitly acknowledging it

def test_ai_failure_returns_a_clean_error_not_a_crash(client, monkeypatch):
    import app.main as main
    class Boom:
        def complete(self, *a, **k): raise RuntimeError("API down")
    monkeypatch.setattr(main, "get_llm", lambda: Boom())
    r = client.post("/api/ask", headers=client.h("Sravya"), json={"question": "What am I forgetting for muhurtham?"})
    assert r.status_code == 502 and "AI service failed" in r.json()["detail"]


def test_each_ceremony_gets_its_own_checklist(client):
    from app import brain
    got = {n: brain.template_for(n)["label"] for n in ("Pellikuthuru", "Mehendi & Sangeet", "Muhurtham", "Reception")}
    assert got["Pellikuthuru"].startswith("Pellikuthuru") and got["Muhurtham"].startswith("Muhurtham")
    assert got["Mehendi & Sangeet"].startswith("Mehendi") and got["Reception"] == "Reception"
    assert brain.template_for("Some Unknown Ceremony") is None

def test_ask_picks_the_right_ceremony_from_the_question(client):
    for q, want in [("what am I forgetting for my pellikuthuru?", "Pellikuthuru"), ("anything missing for the sangeet?", "Mehendi & Sangeet"),
                    ("is the reception ready?", "Reception"), ("what is left for the wedding day", "Muhurtham")]:
        r = client.post("/api/ask", headers=client.h("Sravya"), json={"question": q}).json()
        assert r["ceremony"]["name"] == want, q


def test_tests_cannot_see_a_real_key_or_dotenv():
    assert "ANTHROPIC_API_KEY" not in os.environ and os.environ.get("PELLIPLAN_NO_DOTENV") == "1"

def test_the_app_still_works_when_a_real_dotenv_exists(client):
    # regression guard for the leak that once made a 'no key' test run a real, billed request
    from app.llm import get_llm
    assert get_llm() is None


@pytest.mark.parametrize("val", ["your-key-here", "  Your-Key-Here ", "", "   "])
def test_a_placeholder_key_counts_as_no_key(monkeypatch, val):
    from app import llm
    monkeypatch.setenv("PELLIPLAN_LLM", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", val)
    assert llm.get_llm() is None

def test_a_real_looking_key_enables_ai(monkeypatch):
    from app import llm
    monkeypatch.setenv("PELLIPLAN_LLM", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abcdef")
    assert llm.get_llm() is not None
