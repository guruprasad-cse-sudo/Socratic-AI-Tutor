# Socratic Project Tutor (MVP)

An AI tutor that walks students through the **design phase** of their own project — requirements,
system specifications, high-level design, low-level design, API documentation and DB design — by asking
questions instead of handing over answers. It implements the design doc's MVP: orchestrator, evaluator,
hint ladder, stage gates, mastery tracking, integrity signals and a basic instructor view.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Add your API key:** open `config.env` in the project folder with any text editor (Notepad, TextEdit, VS Code),
paste your key after `ANTHROPIC_API_KEY=`, save, and restart the server. The top bar of the app shows
"Claude API" once the key is picked up. Keep this file private: don't commit it to Git or share it.

Open http://localhost:8000 for the student view and http://localhost:8000/instructor for the instructor view.

**No API key?** The app starts in *mock mode* (shown in the top bar). The mock tutor asks the seed questions and
grades by answer length (25+ words = pass, 8–24 = partial, shorter = misconception, "give me the answer" = evasive),
so you can click through every stage and gate without spending tokens.

Run the tests (they use the mock, no key needed):

```bash
pytest -q
```

## How a turn works

1. The student answers in the chat. The browser also sends answer latency and how many characters were pasted.
2. **Evaluator** (Claude, fast model) grades the answer against the concept's rubric for the current tier and
   returns a verdict: `pass`, `partial`, `misconception` or `evasive`, plus `grounded` and `consistent` flags.
3. **Orchestrator** (`app/orchestrator.py`, plain Python) updates the state:
   - **pass** → record mastery; move up a tier (a clean first-try pass at tier 1 fast-tracks to the required tier);
     when the concept is mastered, move to the next concept; when the stage's concepts are all mastered, the
     **gate opens**: the stage checklist unlocks and the next stage's document template is released.
   - **partial / misconception** → climb the hint ladder (`HINT_1` rephrase → `HINT_2` nudge → `HINT_3` analogy →
     `HINT_4` partial answer → `EXPLAIN` full explanation with a fresh check question).
   - **evasive** ("just give me the answer") → re-ask more simply. Evasion never climbs the ladder, so asking
     for the answer can't unlock it.
4. **Tutor** (Claude, stronger model) writes the next message within the mode the orchestrator chose, grounded in
   the student's brief, documents and earlier answers.

The LLMs only write words. Gating and release decisions live in code, so a student can't prompt their way past a gate.

## Curriculum

`app/curriculum.py` defines 6 stages × 3 concepts. Each concept has a rubric and a seed question for tier 1
(recall), tier 2 (mechanism) and tier 3 (judgment). Core concepts require tier 3; others require tier 2.
Each stage also has a document template (released at stage start) and a checklist (released at the gate).
Edit this file to tune questions or add stages; no other code changes are needed.

## Integrity signals

Recorded per answer and shown only in the instructor view. They never block or penalise a student.

| Signal | Trigger |
|---|---|
| `large_paste` | 150+ characters pasted into the answer |
| `fast_answer` | more than 80 typed characters at over 12 characters/second |
| `ungrounded_pass` | a correct tier 2–3 answer that never refers to the student's own project |
| `inconsistent` | the evaluator finds a contradiction with earlier answers |
| `style_shift` | an answer over 60 words and 4× the student's typical length |

Thresholds are configurable in `config.env`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/projects` | Start a project `{student_name, brief}` → state with the first question |
| `GET` | `/api/projects` | List projects |
| `GET` | `/api/projects/{id}` | Full state: stages, mastery, transcript, documents, releases |
| `POST` | `/api/projects/{id}/answer` | Answer `{text, latency_ms, pasted_chars}` → updated state |
| `PUT` | `/api/projects/{id}/documents/{stage_id}` | Save a design document (403 until that stage's template is released) |
| `GET` | `/api/projects/{id}/report` | Instructor report: mastery, verdicts, misconceptions, integrity signals |

If the Claude API call fails, nothing is saved and the endpoint returns 502 so the student can resend.

## Project layout

```
app/
  main.py          FastAPI routes
  orchestrator.py  state machine: modes, hint ladder, gates, releases, integrity signals
  curriculum.py    stages, concepts, rubrics, seed questions, templates, checklists
  prompts.py       tutor and evaluator prompts and tool schemas
  llm.py           ClaudeClient (Claude API with forced tool use) and MockClient
  db.py            SQLAlchemy models (SQLite by default, Postgres via DATABASE_URL)
  static/          student chat UI and instructor view (no build step)
tests/             pytest suite using the mock client
```

## Not in this MVP (from the design doc)

- Student accounts and authentication — anyone with the URL can open any project. Add auth before using with a class.
- Implementation stages (backend, frontend, testing, deployment) and fill-the-gap code releases.
- Viva generator, spaced review, and the interview practice mode (e.g. "Design an SMS notification system").
- React frontend — this MVP uses plain HTML/JS so it runs without a build step.
