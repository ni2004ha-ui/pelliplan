"""A fictional demo wedding (invented people) so the app has something real to show. Dates are relative to 'today',
so the demo works whenever you run it.   python -m app.seed"""
import json
from datetime import date, timedelta

from . import db as D


def create_demo(conn, today):
    """Returns {member_name: token}. Tokens are shown once; only their hashes are stored."""
    T = lambda n: (today + timedelta(days=n)).isoformat()
    wid = conn.execute("INSERT INTO wedding (name, created_at) VALUES (?, ?)", ("Sravya & Karthik", D.now())).lastrowid
    tokens, mid = {}, {}
    for name, role in [("Sravya", "bride"), ("Anusha", "bridesmaid"), ("Divya", "bridesmaid"), ("Venkat Garu", "family"), ("Lakshmi Atha", "family")]:
        tok = D.new_token()
        mid[name] = conn.execute("INSERT INTO member (wedding_id, name, role, token_hash) VALUES (?,?,?,?)", (wid, name, role, D.hash_token(tok))).lastrowid
        tokens[name] = tok
    cer = {}
    for i, (name, off) in enumerate([("Pellikuthuru", 35), ("Mehendi & Sangeet", 36), ("Muhurtham", 37), ("Reception", 38)]):
        cer[name] = conn.execute("INSERT INTO ceremony (wedding_id, name, date, sort) VALUES (?,?,?,?)", (wid, name, T(off), i)).lastrowid
    S, A, Dv = mid["Sravya"], mid["Anusha"], mid["Divya"]
    tid = {}

    def task(key, title, c, dur=1, due=None, who=None, vis="team", status="todo", amount=None, exp=(0, 0), done=None,
             notes=None, decision=0, desc=None):
        tid[key] = conn.execute(
            """INSERT INTO task (wedding_id, ceremony_id, title, description, status, duration_days, due_date, completed_on, assignee_id,
               visibility, amount, notes_private, expedite_days, expedite_cost, is_decision, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (wid, cer.get(c), title, desc, status, dur, T(due) if due is not None else None, T(done) if done is not None else None,
             mid.get(who), vis, amount, notes, exp[0], exp[1], decision, S, D.now())).lastrowid

    M = "Muhurtham"
    task("purohit", "Book purohit (priest)", M, 1, who="Sravya", vis="family", status="done", amount=11000, done=-20)
    task("muh_time", "Confirm muhurtham time with purohit", M, 1, due=20, who="Sravya", vis="family")
    task("mandapam", "Book mandapam (Muhurtham venue)", M, 1, vis="family", status="done", amount=150000, done=-30)
    task("saree", "Buy bridal kanchi pattu saree", M, 2, who="Sravya", vis="team", status="done", amount=85000, done=-10,
         notes="Aunt suggested a backup saree. Decide quietly, do not tell the family yet.")
    task("deliver", "Deliver saree and blouse fabric to tailor", M, 4, who="Anusha", vis="team", desc="Tailor confirms receipt by phone")
    task("stitch", "Stitch bridal blouse", M, 10, who="Anusha", vis="team", amount=6500, exp=(4, 4000), desc="Tailor can add a second worker for a fee")
    task("fitting", "Blouse fitting", M, 1, who="Anusha", vis="team")
    task("alter", "Blouse alterations", M, 3, due=20, who="Anusha", vis="team")
    task("trial", "Final saree and blouse trial", M, 1, due=22, who="Sravya", vis="team")
    task("mangal_order", "Order mangalsutra", M, 7, due=10, who="Sravya", vis="team", amount=120000)
    task("mangal_collect", "Collect mangalsutra and store safely", M, 1, due=25, who="Sravya", vis="team")
    task("jewel", "Collect bridal jewellery from jeweller", M, 2, due=30, who="Sravya", vis="private", amount=300000, notes="Jewellery budget stays private.")
    task("catering", "Finalise catering menu and guest count", M, 3, due=25, who="Divya", vis="team", amount=350000, decision=1)
    task("decor", "Confirm decor plan with venue", M, 3, due=28, who="Divya", vis="team")
    task("photo", "Book photographer and videographer", M, 1, vis="family", status="done", amount=60000, done=-25)
    task("guests", "Finalise guest list", M, 3, due=5, who="Sravya", vis="family", decision=1)
    task("print", "Print invitations", M, 6, who="Divya", vis="team", amount=25000)
    task("send", "Send invitations to guests", M, 5, due=22, who="Divya", vis="team")
    task("rooms", "Arrange guest rooms near venue", M, 4, due=25, who="Anusha", vis="family", amount=80000)
    task("ritual", "Prepare ritual items list with purohit (puja samagri, talambralu items)", M, 2, due=30, who="Sravya", vis="family")
    task("makeup", "Book bridal makeup artist", M, 1, due=15, who="Anusha", vis="team", amount=40000)
    task("gift", "Surprise gift for parents", None, 1, due=34, who="Sravya", vis="private", amount=30000, notes="Do not mention to anyone.")
    P = "Pellikuthuru"
    task("p_items", "Arrange turmeric and ritual items", P, 2, due=30, who="Sravya", vis="family")
    task("p_outfit", "Pellikuthuru outfit", P, 5, due=30, who="Anusha", vis="team", amount=9000)
    task("p_photo", "Book photographer for Pellikuthuru", P, 1, due=20, who="Divya", vis="team")
    X = "Mehendi & Sangeet"
    task("mehendi", "Book mehendi artist", X, 1, vis="team", status="done", amount=12000, done=-12)
    task("dj", "Book DJ and sound system", X, 1, due=15, who="Divya", vis="team", amount=25000)
    task("dance", "Rehearse group dance (3 sessions)", X, 9, due=34, who="Anusha", vis="team")
    task("lehenga", "Stitch sangeet lehenga", X, 8, due=30, who="Divya", vis="team", amount=14000, exp=(3, 1500))
    R = "Reception"
    task("r_venue", "Book reception venue", R, 1, vis="family", status="done", amount=100000, done=-28)
    task("r_decor", "Reception stage decor", R, 3, due=34, who="Divya", vis="team", amount=45000)
    task("r_outfit", "Reception outfits for the couple", R, 10, due=32, who="Sravya", vis="team", amount=30000, exp=(2, 2000))

    deps = [("muh_time", "purohit"), ("deliver", "saree"), ("stitch", "deliver"), ("fitting", "stitch"), ("alter", "fitting"),
            ("trial", "alter"), ("mangal_collect", "mangal_order"), ("decor", "mandapam"), ("print", "guests"), ("send", "print"),
            ("r_decor", "r_venue"), ("rooms", "guests"), ("catering", "guests")]
    for a, b in deps:
        conn.execute("INSERT INTO dependency (task_id, depends_on_id) VALUES (?,?)", (tid[a], tid[b]))
    conn.execute("INSERT INTO approval (task_id, requested_from, requested_by, question, created_at) VALUES (?,?,?,?,?)",
                 (tid["catering"], mid["Venkat Garu"], S, "Do you approve the menu that Divya will propose?", D.now()))
    conn.execute("INSERT INTO approval (task_id, requested_from, requested_by, question, created_at) VALUES (?,?,?,?,?)",
                 (tid["guests"], mid["Lakshmi Atha"], S, "Please check that your side's relatives are on the list.", D.now()))
    D.log(conn, wid, S, "created", "wedding", wid, name="Sravya & Karthik (demo data)")
    conn.commit()
    return wid, tokens


def reset_demo(conn, today):
    for t in ("activity", "approval", "proposal", "document", "dependency", "task", "ceremony", "member", "wedding"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    return create_demo(conn, today)


if __name__ == "__main__":
    conn = D.connect()
    D.init(conn)
    wid, toks = reset_demo(conn, date.today())
    (D.ROOT / "data").mkdir(exist_ok=True)
    (D.ROOT / "data" / "demo_tokens.json").write_text(json.dumps(toks, indent=2))
    print(f"Demo wedding {wid} created. Tokens saved to data/demo_tokens.json (never commit this file).")
