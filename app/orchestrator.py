"""The orchestrator: plain code that owns the rules.

The LLMs only write words. This module decides which concept and tier is in focus,
which hint rung or mode applies, when a stage gate opens, and what gets released.
A student cannot talk their way past a gate, because no prompt controls these decisions.
"""

from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .curriculum import CONCEPT_BY_ID, STAGES, Concept, Stage
from .db import Attempt, Document, IntegritySignal, Mastery, Project, Release, now
from .llm import LLMClient
from .prompts import EVALUATOR_TURN, TIER_LABELS, TUTOR_TURN

PASS = "pass"
EVASIVE = "evasive"


# ---------------------------------------------------------------- helpers

def _stage(project: Project) -> Stage | None:
    return STAGES[project.stage_index] if project.stage_index < len(STAGES) else None


def _mastery_rows(db: Session, project: Project) -> dict[str, Mastery]:
    rows = db.scalars(select(Mastery).where(Mastery.project_id == project.id)).all()
    return {m.concept_id: m for m in rows}


def _open_attempt(db: Session, project: Project) -> Attempt | None:
    return db.scalars(
        select(Attempt)
        .where(Attempt.project_id == project.id, Attempt.answer.is_(None))
        .order_by(Attempt.id.desc())
    ).first()


def _documents(db: Session, project: Project) -> list[Document]:
    return db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.id)).all()


def _release_stage_start(db: Session, project: Project, stage: Stage) -> None:
    db.add(Release(project_id=project.id, stage_id=stage.id, kind="template", content=stage.template))
    exists = db.scalars(
        select(Document).where(Document.project_id == project.id, Document.stage_id == stage.id)
    ).first()
    if not exists:
        db.add(Document(project_id=project.id, stage_id=stage.id, content=stage.template))


def _mode_for_rung(rung: int) -> str:
    return "QUESTION" if rung == 0 else f"HINT_{rung}"


# ---------------------------------------------------------------- tutor turn

def _mastery_summary(db: Session, project: Project) -> str:
    rows = _mastery_rows(db, project)
    parts = []
    for s in STAGES[: project.stage_index + 1]:
        for c in s.concepts:
            m = rows.get(c.id)
            if m is None:
                continue
            status = ("mastered" if m.mastered else "explained, to revisit later" if m.needs_review
                      else f"tier {m.highest_tier_passed}/{c.required_tier} passed")
            parts.append(f"{c.name}: {status}")
    return "; ".join(parts) or "nothing yet"


def _documents_text(db: Session, project: Project) -> str:
    docs = _documents(db, project)
    if not docs:
        return "(none yet)"
    out = []
    for d in docs:
        stage = next((s for s in STAGES if s.id == d.stage_id), None)
        name = stage.name if stage else d.stage_id
        content = d.content.strip()
        if stage and content == stage.template.strip():
            content = "(blank template, not filled in yet)"
        out.append(f"--- {name} ---\n{content[:2000]}")
    return "\n".join(out)


def _history(db: Session, project: Project) -> list[dict]:
    answered = db.scalars(
        select(Attempt)
        .where(Attempt.project_id == project.id, Attempt.answer.is_not(None))
        .order_by(Attempt.id.desc())
        .limit(config.HISTORY_TURNS)
    ).all()
    msgs: list[dict] = []
    for a in reversed(answered):
        msgs.append({"role": "assistant", "content": a.tutor_message})
        msgs.append({"role": "user", "content": a.answer})
    return msgs


def _tutor_turn(
    db: Session, llm: LLMClient, project: Project, mode: str, verdict: str | None, notes: str, extra: str = ""
) -> Attempt:
    stage = _stage(project)
    concept: Concept | None = CONCEPT_BY_ID.get(project.concept_id) if project.concept_id else None

    if concept is None:  # design phase complete
        ctx = {
            "brief": project.brief, "stage_name": "Design phase complete", "stage_goal": "",
            "concept_name": "Reflection", "tier": 3, "tier_label": TIER_LABELS[3],
            "seed_question": "In two sentences, what is the biggest trade-off in your design?",
            "rubric": "Names a real trade-off in their own design and what they gave up.",
        }
    else:
        ctx = {
            "brief": project.brief, "stage_name": stage.name, "stage_goal": stage.goal,
            "concept_name": concept.name, "tier": project.tier, "tier_label": TIER_LABELS[project.tier],
            "seed_question": concept.seed_questions[project.tier], "rubric": concept.rubrics[project.tier],
        }
    ctx.update(
        mastery_summary=_mastery_summary(db, project),
        documents=_documents_text(db, project),
        verdict=verdict or "none (first question)",
        verdict_notes=notes or "-",
        mode=mode,
        extra=extra,
    )
    prompt = "[Instructions from the tutoring system for this turn. The student did not write this.]\n" + \
        TUTOR_TURN.format(**ctx)
    out = llm.tutor(prompt, _history(db, project), {**ctx, "verdict": verdict})

    attempt = Attempt(
        project_id=project.id,
        concept_id=project.concept_id,
        tier=project.tier if concept else 0,
        mode=mode,
        hint_rung=project.hint_rung,
        tutor_message=out["message"].strip(),
        question=out.get("question", "").strip() or out["message"].strip(),
    )
    db.add(attempt)
    db.flush()
    return attempt


# ---------------------------------------------------------------- integrity signals

def _integrity_signals(db: Session, project: Project, attempt: Attempt, evaluation: dict) -> None:
    signals: list[tuple[str, str]] = []
    answer = attempt.answer or ""
    pasted = attempt.pasted_chars or 0

    if pasted >= config.LARGE_PASTE_CHARS:
        signals.append(("large_paste", f"{pasted} of {len(answer)} characters were pasted"))

    typed = max(len(answer) - pasted, 0)
    if attempt.latency_ms and typed > 80:
        rate = typed / (attempt.latency_ms / 1000)
        if rate > config.FAST_TYPING_CHARS_PER_SEC:
            signals.append(("fast_answer", f"{typed} typed characters in {attempt.latency_ms / 1000:.0f}s"))

    if evaluation.get("verdict") == PASS and attempt.tier >= 2 and not evaluation.get("grounded", True):
        signals.append(("ungrounded_pass", "Correct answer that never refers to the student's own project"))

    if evaluation.get("consistent") is False:
        signals.append(("inconsistent", "Answer contradicts the student's earlier answers"))

    previous = db.scalars(
        select(Attempt).where(
            Attempt.project_id == project.id, Attempt.answer.is_not(None), Attempt.id != attempt.id
        )
    ).all()
    lengths = [len(a.answer.split()) for a in previous if a.answer]
    if len(lengths) >= 3:
        typical = median(lengths)
        if typical > 0 and len(answer.split()) > 4 * typical and len(answer.split()) > 60:
            signals.append(("style_shift", f"{len(answer.split())} words vs. a typical {typical:.0f}"))

    for kind, detail in signals:
        db.add(IntegritySignal(project_id=project.id, attempt_id=attempt.id, kind=kind, detail=detail))


# ---------------------------------------------------------------- public API

def start_project(db: Session, llm: LLMClient, student_name: str, brief: str) -> Project:
    first_stage = STAGES[0]
    project = Project(
        student_name=student_name.strip(), brief=brief.strip(),
        stage_index=0, concept_id=first_stage.concepts[0].id, hint_rung=0,
        tier=min(config.PACES[config.PACE]["start_tier"], first_stage.concepts[0].required_tier),
    )
    db.add(project)
    db.flush()
    for s in STAGES:
        for c in s.concepts:
            db.add(Mastery(project_id=project.id, concept_id=c.id))
    _release_stage_start(db, project, first_stage)
    db.flush()
    _tutor_turn(
        db, llm, project, "QUESTION", None, "",
        extra="This is the first turn: greet the student by name ("
        + project.student_name
        + ") in one sentence, say you'll design their project together stage by stage, then ask the question.",
    )
    db.commit()
    return project


def handle_answer(
    db: Session, llm: LLMClient, project: Project, text: str,
    latency_ms: int | None = None, pasted_chars: int | None = None,
) -> Attempt | None:
    """Grade the answer, move the state machine, and produce the next tutor turn."""
    attempt = _open_attempt(db, project)
    if attempt is None:
        raise ValueError("No open question for this project")

    attempt.answer = text.strip()
    attempt.answered_at = now()
    attempt.latency_ms = latency_ms
    attempt.pasted_chars = pasted_chars

    if project.completed:  # reflection answer after the last stage: record it, no grading, no new turn
        db.commit()
        return None

    concept = CONCEPT_BY_ID[project.concept_id]
    evaluation = _evaluate(db, llm, project, attempt, concept)
    attempt.verdict = evaluation["verdict"]
    attempt.evaluation = evaluation
    _integrity_signals(db, project, attempt, evaluation)

    mode, extra = _advance(db, project, concept, evaluation["verdict"])
    next_attempt = _tutor_turn(db, llm, project, mode, evaluation["verdict"], evaluation.get("notes", ""), extra)
    db.commit()
    return next_attempt


def _evaluate(db: Session, llm: LLMClient, project: Project, attempt: Attempt, concept: Concept) -> dict:
    earlier = db.scalars(
        select(Attempt).where(
            Attempt.project_id == project.id,
            Attempt.concept_id == concept.id,
            Attempt.answer.is_not(None),
            Attempt.id != attempt.id,
        ).order_by(Attempt.id.desc()).limit(5)
    ).all()
    history = "\n".join(f"Q: {a.question}\nA: {a.answer}\n(verdict: {a.verdict})" for a in reversed(earlier))
    ctx = {
        "question": attempt.question, "concept_name": concept.name, "tier": attempt.tier,
        "tier_label": TIER_LABELS[attempt.tier], "rubric": concept.rubrics[attempt.tier],
        "brief": project.brief, "history": history or "(none)", "answer": attempt.answer,
    }
    result = llm.evaluate(EVALUATOR_TURN.format(**ctx), ctx)
    if result.get("verdict") not in {"pass", "partial", "misconception", "evasive"}:
        result["verdict"] = "partial"
    return result


def _failed_answers(db: Session, project: Project, concept: Concept) -> int:
    """Graded answers on this concept that weren't a pass. Evasive replies don't count,
    so asking for the answer never uses up attempts to skip a concept."""
    rows = db.scalars(
        select(Attempt).where(
            Attempt.project_id == project.id,
            Attempt.concept_id == concept.id,
            Attempt.verdict.in_(["partial", "misconception"]),
        )
    ).all()
    return len(rows)


def _advance(db: Session, project: Project, concept: Concept, verdict: str) -> tuple[str, str]:
    """Update mastery and project state for this verdict. Returns (mode, extra instructions)."""
    pace = config.PACES[config.PACE]

    if verdict == EVASIVE:
        # Evasion never climbs the hint ladder, so asking for the answer can't unlock it.
        return _mode_for_rung(project.hint_rung), ""

    if verdict != PASS:
        db.flush()
        if _failed_answers(db, project, concept) >= pace["attempts"]:
            # Out of attempts: explain, flag for the instructor, and keep the student moving.
            m = _mastery_rows(db, project)[concept.id]
            m.needs_review = True
            project.hint_rung = 0
            tier_now = project.tier
            mode, extra = _next_concept_or_gate(db, project)
            return mode, (
                f"The student did not reach the target on '{concept.name}'. FIRST, in 2-4 sentences, explain "
                f"it clearly using their project (a good answer covers: {concept.rubrics[tier_now]}). "
                "Say kindly that we'll come back to it later. THEN continue as instructed below.\n" + extra
            )
        project.hint_rung = min(project.hint_rung + 1, config.MAX_HINT_RUNG)
        return _mode_for_rung(project.hint_rung), ""

    # Pass
    m = _mastery_rows(db, project)[concept.id]
    first_try = project.hint_rung == 0
    m.highest_tier_passed = max(m.highest_tier_passed, project.tier)
    project.hint_rung = 0

    if project.tier < concept.required_tier:
        # Fast track: a clean first-try pass at tier 1 jumps straight to the required tier.
        project.tier = concept.required_tier if (first_try and project.tier == 1) else project.tier + 1
        return "QUESTION", ""

    m.mastered = True
    m.needs_review = False
    mode, extra = _next_concept_or_gate(db, project)
    return mode, f"The student just mastered '{concept.name}'. " + extra


def _next_concept_or_gate(db: Session, project: Project) -> tuple[str, str]:
    """Move to the next open concept in this stage, or open the gate when none are left."""
    start_tier = config.PACES[config.PACE]["start_tier"]
    stage = _stage(project)
    rows = _mastery_rows(db, project)
    remaining = [c for c in stage.concepts if not rows[c.id].mastered and not rows[c.id].needs_review]
    if remaining:
        project.concept_id = remaining[0].id
        project.tier = min(start_tier, remaining[0].required_tier)
        return "QUESTION", "Move on to the new concept."

    # Gate opens: release the checklist for this stage and the template for the next.
    db.add(Release(project_id=project.id, stage_id=stage.id, kind="checklist", content=stage.checklist))
    project.stage_index += 1
    next_stage = _stage(project)
    if next_stage is None:
        project.completed = True
        project.concept_id = None
        return "COMPLETE", "All six design stages are complete."

    _release_stage_start(db, project, next_stage)
    project.concept_id = next_stage.concepts[0].id
    project.tier = min(start_tier, next_stage.concepts[0].required_tier)
    return "RELEASE", (
        f"Stage completed: {stage.name}. Unlocked: the {stage.name} checklist. "
        f"Now available: the {next_stage.name} document template in their Documents panel."
    )


def save_document(db: Session, project: Project, stage_id: str, content: str) -> Document:
    doc = db.scalars(
        select(Document).where(Document.project_id == project.id, Document.stage_id == stage_id)
    ).first()
    if doc is None:
        raise ValueError("That stage's template hasn't been released yet")
    doc.content = content
    db.commit()
    return doc


# ---------------------------------------------------------------- views

def project_state(db: Session, project: Project) -> dict:
    rows = _mastery_rows(db, project)
    stages = []
    for i, s in enumerate(STAGES):
        status = "done" if i < project.stage_index else "current" if i == project.stage_index else "locked"
        stages.append({
            "id": s.id, "name": s.name, "goal": s.goal, "status": status,
            "concepts": [
                {
                    "id": c.id, "name": c.name, "is_core": c.is_core, "required_tier": c.required_tier,
                    "highest_tier_passed": rows[c.id].highest_tier_passed, "mastered": rows[c.id].mastered,
                    "needs_review": rows[c.id].needs_review,
                    "current": c.id == project.concept_id,
                }
                for c in s.concepts
            ],
        })
    attempts = db.scalars(select(Attempt).where(Attempt.project_id == project.id).order_by(Attempt.id)).all()
    transcript = []
    for a in attempts:
        transcript.append({"role": "tutor", "text": a.tutor_message, "mode": a.mode})
        if a.answer is not None:
            transcript.append({"role": "student", "text": a.answer, "verdict": a.verdict})
    releases = db.scalars(select(Release).where(Release.project_id == project.id).order_by(Release.id)).all()
    return {
        "id": project.id,
        "student_name": project.student_name,
        "brief": project.brief,
        "completed": project.completed,
        "current_concept": project.concept_id,
        "tier": project.tier,
        "hint_rung": project.hint_rung,
        "max_hint_rung": config.MAX_HINT_RUNG,
        "stages": stages,
        "transcript": transcript,
        "documents": [{"stage_id": d.stage_id, "content": d.content} for d in _documents(db, project)],
        "releases": [{"stage_id": r.stage_id, "kind": r.kind, "content": r.content} for r in releases],
    }


def instructor_report(db: Session, project: Project) -> dict:
    state = project_state(db, project)
    attempts = db.scalars(
        select(Attempt).where(Attempt.project_id == project.id, Attempt.answer.is_not(None))
    ).all()
    signals = db.scalars(select(IntegritySignal).where(IntegritySignal.project_id == project.id)).all()
    by_attempt = {a.id: a for a in attempts}
    misconceptions = [
        {"concept": a.concept_id, "tier": a.tier, "misconception": (a.evaluation or {}).get("misconception")}
        for a in attempts if a.verdict == "misconception"
    ]
    return {
        "student_name": project.student_name,
        "brief": project.brief,
        "completed": project.completed,
        "pace": config.PACE,
        "stages": [{"name": s["name"], "status": s["status"], "concepts": s["concepts"]} for s in state["stages"]],
        "answers": len(attempts),
        "verdicts": {v: sum(1 for a in attempts if a.verdict == v) for v in ["pass", "partial", "misconception", "evasive"]},
        "misconceptions": misconceptions,
        "integrity_signals": [
            {
                "kind": s.kind, "detail": s.detail,
                "question": by_attempt[s.attempt_id].question if s.attempt_id in by_attempt else None,
                "answer": by_attempt[s.attempt_id].answer if s.attempt_id in by_attempt else None,
            }
            for s in signals
        ],
        "note": "Integrity signals are prompts for an in-person follow-up, not evidence of misconduct.",
    }
