from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DATABASE_URL


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_name: Mapped[str] = mapped_column(String(120))
    brief: Mapped[str] = mapped_column(Text)
    stage_index: Mapped[int] = mapped_column(Integer, default=0)
    concept_id: Mapped[str | None] = mapped_column(String(64))
    tier: Mapped[int] = mapped_column(Integer, default=1)
    hint_rung: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    attempts: Mapped[list["Attempt"]] = relationship(back_populates="project", order_by="Attempt.id")


class Mastery(Base):
    __tablename__ = "mastery"
    __table_args__ = (UniqueConstraint("project_id", "concept_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    concept_id: Mapped[str] = mapped_column(String(64))
    highest_tier_passed: Mapped[int] = mapped_column(Integer, default=0)
    mastered: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when the student ran out of attempts and the tutor explained and moved on.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class Attempt(Base):
    """One tutor turn (question) and, once answered, the student's reply and its grade."""

    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    concept_id: Mapped[str | None] = mapped_column(String(64))
    tier: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))
    hint_rung: Mapped[int] = mapped_column(Integer, default=0)
    tutor_message: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(String(16))
    evaluation: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    pasted_chars: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="attempts")


class Document(Base):
    """A student's design document for one stage (requirements, HLD, API spec, ...)."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "stage_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    stage_id: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Release(Base):
    """Material unlocked for the student: a stage template at stage start, a checklist at the gate."""

    __tablename__ = "releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    stage_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))  # "template" | "checklist"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IntegritySignal(Base):
    __tablename__ = "integrity_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def make_engine(url: str = DATABASE_URL):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None) -> None:
    eng = eng or engine
    Base.metadata.create_all(eng)
    # Small upgrade step for databases created by an earlier version of the app.
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(eng).get_columns("mastery")}
    if "needs_review" not in cols:
        with eng.begin() as conn:
            conn.execute(text("ALTER TABLE mastery ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT FALSE"))
