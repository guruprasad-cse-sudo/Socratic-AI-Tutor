import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, orchestrator
from .db import Project, SessionLocal, init_db
from .llm import LLMClient, get_client

STATIC = Path(__file__).parent / "static"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    print(f"\n  Tutor mode: {config.LLM_PROVIDER}")
    print(f"  API key:    {config.key_hint()}")
    if config.key_problem():
        print(f"  WARNING:    {config.key_problem()}")
    if not config.INSTRUCTOR_PASSWORD:
        print("  WARNING:    INSTRUCTOR_PASSWORD is not set, so the instructor view is locked.")
    print()
    yield


def _friendly(e: Exception) -> str:
    """Turn common Claude API errors into instructions a teacher can act on."""
    try:
        import anthropic
    except ImportError:
        anthropic = None
    if anthropic is not None:
        if isinstance(e, anthropic.AuthenticationError):
            return (f"The Claude API rejected the API key ({config.key_hint()}). Create a new key at "
                    "console.anthropic.com > API Keys, paste it into config.env after ANTHROPIC_API_KEY=, "
                    "save, and restart the server.")
        if isinstance(e, anthropic.PermissionDeniedError):
            return "This API key isn't allowed to use the model. Check your Anthropic Console account and models."
        if isinstance(e, anthropic.NotFoundError):
            return (f"Model not found. Check TUTOR_MODEL ({config.TUTOR_MODEL}) and EVALUATOR_MODEL "
                    f"({config.EVALUATOR_MODEL}) in config.env.")
        if isinstance(e, anthropic.RateLimitError):
            return "Rate limit reached on the Claude API. Wait a minute and send again."
        if isinstance(e, anthropic.BadRequestError) and "credit" in str(e).lower():
            return "Your Anthropic account has no API credits left. Add credits at console.anthropic.com > Billing."
        if isinstance(e, anthropic.APIConnectionError):
            return "Couldn't reach the Claude API. Check this computer's internet connection or proxy."
    return f"Tutor unavailable, please resend: {e}"


app = FastAPI(title="Socratic Project Tutor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_llm: LLMClient | None = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = get_client()
    return _llm


def _is_instructor(password: str | None) -> bool:
    if not config.INSTRUCTOR_PASSWORD or not password:
        return False
    return hmac.compare_digest(password.encode(), config.INSTRUCTOR_PASSWORD.encode())


def require_instructor(x_instructor_password: str | None = Header(default=None)) -> None:
    if not config.INSTRUCTOR_PASSWORD:
        raise HTTPException(503, "The instructor view is locked: set INSTRUCTOR_PASSWORD on the server first.")
    if not _is_instructor(x_instructor_password):
        raise HTTPException(401, "Wrong instructor password.")


def _project(
    project_id: int,
    db: Session = Depends(get_db),
    x_project_token: str | None = Header(default=None),
    x_instructor_password: str | None = Header(default=None),
) -> Project:
    """The project, if the caller holds its access token or is the instructor.
    Any other caller gets 404, so project numbers can't be probed."""
    project = db.get(Project, project_id)
    if project is not None and (
        _is_instructor(x_instructor_password)
        or (x_project_token and project.access_token
            and hmac.compare_digest(x_project_token.encode(), project.access_token.encode()))
    ):
        return project
    raise HTTPException(404, "Project not found")


class NewProject(BaseModel):
    student_name: str = Field(min_length=1, max_length=120)
    brief: str = Field(min_length=10, max_length=4000)


class Answer(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    latency_ms: int | None = Field(default=None, ge=0)
    pasted_chars: int | None = Field(default=None, ge=0)


class DocumentIn(BaseModel):
    content: str = Field(max_length=50000)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/instructor")
def instructor_page():
    return FileResponse(STATIC / "instructor.html")


@app.get("/api/health")
def health():
    return {"ok": True, "llm_provider": config.LLM_PROVIDER, "api_key": config.key_hint(),
            "key_problem": config.key_problem()}


@app.get("/api/projects", dependencies=[Depends(require_instructor)])
def list_projects(db: Session = Depends(get_db)):
    projects = db.scalars(select(Project).order_by(Project.id.desc())).all()
    return [
        {"id": p.id, "student_name": p.student_name, "brief": p.brief[:120], "stage_index": p.stage_index,
         "completed": p.completed}
        for p in projects
    ]


@app.post("/api/projects", status_code=201)
def create_project(body: NewProject, db: Session = Depends(get_db), llm: LLMClient = Depends(get_llm)):
    try:
        project = orchestrator.start_project(db, llm, body.student_name, body.brief)
    except Exception as e:  # LLM/API failure
        db.rollback()
        raise HTTPException(502, _friendly(e)) from e
    # The token is returned only here; the student's browser keeps it to reopen the project.
    return {**orchestrator.project_state(db, project), "access_token": project.access_token}


@app.get("/api/projects/{project_id}")
def get_project(project: Project = Depends(_project), db: Session = Depends(get_db)):
    return orchestrator.project_state(db, project)


@app.post("/api/projects/{project_id}/answer")
def post_answer(body: Answer, project: Project = Depends(_project), db: Session = Depends(get_db),
                llm: LLMClient = Depends(get_llm)):
    try:
        orchestrator.handle_answer(db, llm, project, body.text, body.latency_ms, body.pasted_chars)
    except ValueError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # LLM/API failure: nothing is saved, the student can resend
        db.rollback()
        raise HTTPException(502, _friendly(e)) from e
    db.refresh(project)
    return orchestrator.project_state(db, project)


@app.put("/api/projects/{project_id}/documents/{stage_id}")
def put_document(stage_id: str, body: DocumentIn, project: Project = Depends(_project),
                 db: Session = Depends(get_db)):
    try:
        doc = orchestrator.save_document(db, project, stage_id, body.content)
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    return {"stage_id": doc.stage_id, "content": doc.content}


@app.get("/api/projects/{project_id}/report", dependencies=[Depends(require_instructor)])
def get_report(project: Project = Depends(_project), db: Session = Depends(get_db)):
    return orchestrator.instructor_report(db, project)
