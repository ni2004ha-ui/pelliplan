"""Safety rails: tests never touch a real database, upload folder, .env or API key, and never spend money."""
import os, sys, tempfile
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["PELLIPLAN_NO_DOTENV"] = "1"
os.environ["PELLIPLAN_LLM"] = "mock"
os.environ["PELLIPLAN_DEMO"] = "1"
os.environ["PELLIPLAN_TODAY"] = "2026-10-27"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PELLIPLAN_DB", str(tmp_path / "t.db"))
    from fastapi.testclient import TestClient
    from app import db as D
    monkeypatch.setattr(D, "ROOT", tmp_path)               # uploads land in the temp folder, not in the project
    import app.main as main
    monkeypatch.setattr(main, "ROOT", tmp_path)
    with TestClient(main.app) as c:
        rows = c.post("/api/demo/reset").json()
        c.tok = {r["name"]: r["token"] for r in rows}
        c.h = lambda who: {"X-Token": c.tok[who]}
        yield c


def by_title(plan, text):
    return next(t for t in plan["tasks"] if text.lower() in t["title"].lower())
