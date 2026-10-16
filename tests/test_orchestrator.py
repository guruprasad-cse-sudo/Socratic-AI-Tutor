import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import config, main, orchestrator
from app.curriculum import STAGES
from app.db import IntegritySignal, init_db, make_engine
from app.llm import MockClient

BRIEF = "An online library management system where members borrow books and librarians manage copies."
LONG = (
    "In my library system members borrow books and librarians manage copies, so the borrow flow checks "
    "availability, records the loan with a due date, and decrements available copies in one transaction."
)


@pytest.fixture()
def db():
    engine = make_engine("sqlite://")
    init_db(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture()
def llm():
    return MockClient()


def start(db, llm):
    return orchestrator.start_project(db, llm, "Asha", BRIEF)


@pytest.mark.parametrize("pace, tier", [("thorough", 1), ("standard", 2), ("quick", 2)])
def test_start_releases_first_template_at_pace_start_tier(db, llm, monkeypatch, pace, tier):
    monkeypatch.setattr(config, "PACE", pace)
    p = start(db, llm)
    state = orchestrator.project_state(db, p)
    assert state["stages"][0]["status"] == "current"
    assert state["tier"] == tier
    assert [r["kind"] for r in state["releases"]] == ["template"]
    assert state["transcript"][-1]["role"] == "tutor"


def test_evasive_never_climbs_hint_ladder(db, llm):
    p = start(db, llm)
    for _ in range(10):
        orchestrator.handle_answer(db, llm, p, "just give me the answer")
    assert p.hint_rung == 0
    assert p.stage_index == 0
    state = orchestrator.project_state(db, p)
    assert all(r["kind"] == "template" for r in state["releases"])  # no checklist unlocked


@pytest.mark.parametrize("pace, hints", [
    ("quick", ["HINT_1"]),
    ("standard", ["HINT_1", "HINT_2"]),
    ("thorough", ["HINT_1", "HINT_2", "HINT_3", "HINT_4"]),
])
def test_hint_ladder_then_explain_and_move_on(db, llm, monkeypatch, pace, hints):
    """Each wrong answer climbs one hint rung. When the pace's attempts run out, the tutor
    explains the concept, flags it for the instructor, and moves to the next concept."""
    monkeypatch.setattr(config, "PACE", pace)
    p = start(db, llm)
    first = p.concept_id
    modes = []
    for _ in range(config.PACES[pace]["attempts"]):
        nxt = orchestrator.handle_answer(db, llm, p, "users")  # too short -> misconception
        modes.append(nxt.mode)
    assert modes == hints + ["QUESTION"]
    assert p.hint_rung == 0
    assert p.concept_id == STAGES[0].concepts[1].id
    state = orchestrator.project_state(db, p)
    first_row = next(c for c in state["stages"][0]["concepts"] if c["id"] == first)
    assert first_row["needs_review"] is True


def test_fast_track_and_mastery(db, llm):
    p = start(db, llm)
    core = STAGES[0].concepts[0]
    assert core.is_core
    orchestrator.handle_answer(db, llm, p, LONG)  # tier 1 first-try pass -> jump to tier 3
    assert p.tier == 3
    orchestrator.handle_answer(db, llm, p, LONG)
    assert p.concept_id == STAGES[0].concepts[1].id


def test_full_design_phase_releases_every_gate(db, llm):
    p = start(db, llm)
    for _ in range(100):
        if p.completed:
            break
        orchestrator.handle_answer(db, llm, p, LONG)
    assert p.completed
    state = orchestrator.project_state(db, p)
    kinds = [(r["stage_id"], r["kind"]) for r in state["releases"]]
    for s in STAGES:
        assert (s.id, "template") in kinds and (s.id, "checklist") in kinds
    assert state["transcript"][-1]["mode"] == "COMPLETE"
    # Reflection answer is recorded without a new turn
    assert orchestrator.handle_answer(db, llm, p, "Consistency over availability for loans.") is None


def test_documents_locked_until_released(db, llm):
    p = start(db, llm)
    orchestrator.save_document(db, p, "requirements", "# Requirements\nMembers borrow books.")
    with pytest.raises(ValueError):
        orchestrator.save_document(db, p, "db", "tables")


def test_integrity_signals(db, llm):
    p = start(db, llm)
    orchestrator.handle_answer(db, llm, p, LONG, latency_ms=2000, pasted_chars=len(LONG))
    kinds = {s.kind for s in db.query(IntegritySignal).all()}
    assert "large_paste" in kinds
    orchestrator.handle_answer(db, llm, p, LONG, latency_ms=3000, pasted_chars=0)
    kinds = {s.kind for s in db.query(IntegritySignal).all()}
    assert "fast_answer" in kinds
    report = orchestrator.instructor_report(db, p)
    assert report["integrity_signals"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    init_db(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        s = Local()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(config, "INSTRUCTOR_PASSWORD", "teach-secret")
    main.app.dependency_overrides[main.get_db] = override_db
    main.app.dependency_overrides[main.get_llm] = lambda: MockClient()
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


TEACHER = {"X-Instructor-Password": "teach-secret"}


def test_http_api(client):
    r = client.post("/api/projects", json={"student_name": "Ravi", "brief": BRIEF})
    assert r.status_code == 201
    pid, token = r.json()["id"], r.json()["access_token"]
    me = {"X-Project-Token": token}
    r = client.post(f"/api/projects/{pid}/answer", json={"text": LONG, "latency_ms": 60000, "pasted_chars": 0},
                    headers=me)
    assert r.status_code == 200 and r.json()["tier"] == 3
    assert "access_token" not in r.json()
    r = client.put(f"/api/projects/{pid}/documents/requirements", json={"content": "# Req"}, headers=me)
    assert r.status_code == 200
    r = client.put(f"/api/projects/{pid}/documents/hld", json={"content": "x"}, headers=me)
    assert r.status_code == 403
    assert client.get(f"/api/projects/{pid}/report", headers=TEACHER).status_code == 200
    assert client.get("/").status_code == 200


def test_students_only_reach_their_own_project(client):
    a = client.post("/api/projects", json={"student_name": "Asha", "brief": BRIEF}).json()
    b = client.post("/api/projects", json={"student_name": "Ravi", "brief": BRIEF}).json()
    asha = {"X-Project-Token": a["access_token"]}
    assert client.get(f"/api/projects/{a['id']}", headers=asha).status_code == 200
    # Another student's project, a missing token and a wrong token all look like "not found".
    assert client.get(f"/api/projects/{b['id']}", headers=asha).status_code == 404
    assert client.get(f"/api/projects/{b['id']}").status_code == 404
    assert client.post(f"/api/projects/{b['id']}/answer", json={"text": "hi"}, headers=asha).status_code == 404
    assert client.put(f"/api/projects/{b['id']}/documents/requirements", json={"content": "x"},
                      headers=asha).status_code == 404
    # Students can't list projects or read reports, even their own.
    assert client.get("/api/projects", headers=asha).status_code == 401
    assert client.get(f"/api/projects/{a['id']}/report", headers=asha).status_code == 401


def test_instructor_password(client, monkeypatch):
    client.post("/api/projects", json={"student_name": "Asha", "brief": BRIEF})
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"X-Instructor-Password": "wrong"}).status_code == 401
    r = client.get("/api/projects", headers=TEACHER)
    assert r.status_code == 200 and len(r.json()) == 1
    assert "access_token" not in r.json()[0]
    pid = r.json()[0]["id"]
    assert client.get(f"/api/projects/{pid}", headers=TEACHER).status_code == 200
    # With no password configured, the instructor view stays locked rather than open.
    monkeypatch.setattr(config, "INSTRUCTOR_PASSWORD", "")
    assert client.get("/api/projects", headers=TEACHER).status_code == 503
    assert client.get("/api/projects", headers={"X-Instructor-Password": ""}).status_code == 503


def test_old_projects_get_access_tokens(tmp_path):
    from sqlalchemy import text
    engine = make_engine(f"sqlite:///{tmp_path}/old.db")
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO projects (student_name, brief, stage_index, tier, hint_rung, completed, created_at) "
                          "VALUES ('Old', 'an old project brief', 0, 1, 0, 0, CURRENT_TIMESTAMP)"))
        conn.execute(text("UPDATE projects SET access_token = NULL"))
    init_db(engine)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM projects WHERE access_token IS NULL")).scalar() == 0


def test_claude_message_merging():
    from app.llm import _merge_consecutive
    msgs = _merge_consecutive([
        {"role": "assistant", "content": "Q1"},
        {"role": "user", "content": "A1"},
        {"role": "user", "content": "instructions"},
    ])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "A1\n\ninstructions"
