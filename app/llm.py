"""LLM clients. ClaudeClient calls the Claude API; MockClient is deterministic for demos and tests."""

import re
from typing import Protocol

from . import config
from .prompts import EVALUATOR_SYSTEM, GRADE_TOOL, RESPOND_TOOL, TUTOR_SYSTEM


class LLMClient(Protocol):
    def tutor(self, turn_prompt: str, history: list[dict], ctx: dict) -> dict: ...
    def evaluate(self, turn_prompt: str, ctx: dict) -> dict: ...


class ClaudeClient:
    def __init__(self, tutor_model: str = config.TUTOR_MODEL, evaluator_model: str = config.EVALUATOR_MODEL):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.tutor_model = tutor_model
        self.evaluator_model = evaluator_model
        # Some models reject a forced tool_choice. We learn that per model on the first call
        # and from then on let the model choose, falling back to reading its text if needed.
        self._no_forced_tools: set[str] = set()

    def _create(self, model: str, system: str, messages: list[dict], tool: dict, max_tokens: int, forced: bool):
        kwargs = dict(model=model, max_tokens=max_tokens, system=system, messages=messages, tools=[tool])
        if forced:
            kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}
        else:
            kwargs["system"] = system + f"\n\nIMPORTANT: reply ONLY by calling the `{tool['name']}` tool."
        return self.client.messages.create(**kwargs)

    def _tool_call(self, model: str, system: str, messages: list[dict], tool: dict, max_tokens: int) -> tuple[dict | None, str]:
        """Returns (tool input, or None if the model answered in plain text; the plain text)."""
        forced = model not in self._no_forced_tools
        try:
            resp = self._create(model, system, messages, tool, max_tokens, forced)
        except self._anthropic.BadRequestError as e:
            if forced and "tool_choice" in str(e):
                self._no_forced_tools.add(model)
                resp = self._create(model, system, messages, tool, max_tokens, forced=False)
            else:
                raise
        text_parts = []
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return dict(block.input), ""
            if block.type == "text":
                text_parts.append(block.text)
        return None, "\n".join(text_parts).strip()

    def tutor(self, turn_prompt: str, history: list[dict], ctx: dict) -> dict:
        # The conversation so far, then the orchestrator's instructions for this turn as the last user message.
        messages = [*history, {"role": "user", "content": turn_prompt}]
        messages = _merge_consecutive(messages)
        result, text = self._tool_call(self.tutor_model, TUTOR_SYSTEM, messages, RESPOND_TOOL, 4000)
        if result is None:
            result = tutor_from_text(text)
        return result

    def evaluate(self, turn_prompt: str, ctx: dict) -> dict:
        result, text = self._tool_call(
            self.evaluator_model, EVALUATOR_SYSTEM, [{"role": "user", "content": turn_prompt}], GRADE_TOOL, 4000
        )
        if result is None:
            result = grade_from_text(text)
        return normalize_grade(result)


def tutor_from_text(text: str) -> dict:
    """Fallback when the model replied in plain text instead of calling `respond`."""
    data = _json_in(text)
    if data and data.get("message"):
        return {"message": str(data["message"]), "question": str(data.get("question") or data["message"])}
    if not text:
        raise RuntimeError("The tutor model returned an empty reply")
    questions = re.findall(r"[^.!?\n]*\?", text)
    return {"message": text, "question": questions[-1].strip(" *") if questions else text}


def grade_from_text(text: str) -> dict:
    """Fallback when the evaluator replied in plain text instead of calling `grade`."""
    data = _json_in(text)
    if data and "verdict" in data:
        return data
    m = re.search(r"\b(pass|partial|misconception|evasive)\b", text, re.I)
    return {
        "verdict": m.group(1).lower() if m else "partial",
        "covered": [], "missing": [], "misconception": None,
        "grounded": True, "consistent": True,
        "notes": text[:300] or "Evaluator gave no details.",
    }


def normalize_grade(g: dict) -> dict:
    g = dict(g)
    if g.get("verdict") not in {"pass", "partial", "misconception", "evasive"}:
        g["verdict"] = "partial"
    g.setdefault("covered", [])
    g.setdefault("missing", [])
    g.setdefault("misconception", None)
    g.setdefault("grounded", True)
    g.setdefault("consistent", True)
    g.setdefault("notes", "")
    return g


def _json_in(text: str) -> dict | None:
    import json

    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _merge_consecutive(messages: list[dict]) -> list[dict]:
    """The API needs alternating roles starting with user; merge neighbours with the same role."""
    merged: list[dict] = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n\n" + m["content"]}
        else:
            merged.append(dict(m))
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(session start)"})
    return merged


EVASIVE_PATTERNS = re.compile(
    r"\b(give me|just (tell|show|write)|write (it|the code|the schema)|the answer|i don'?t know|idk|skip)\b", re.I
)


class MockClient:
    """Deterministic stand-in so the app runs without an API key.

    Grading rule: evasive phrases -> evasive; 25+ words -> pass; 8-24 words -> partial; shorter -> misconception.
    """

    def tutor(self, turn_prompt: str, history: list[dict], ctx: dict) -> dict:
        mode = ctx["mode"]
        q = ctx["seed_question"]
        lead = {
            "QUESTION": "",
            "HINT_1": "Let me put that more simply. ",
            "HINT_2": "Here's a nudge: think about what could go wrong in your own system. ",
            "HINT_3": "Think of a pizza shop: who orders, who cooks, and what happens at the busiest hour? ",
            "HINT_4": f"Here's half of it: {ctx['rubric'].split(';')[0].split('.')[0]}. Can you complete it? ",
            "EXPLAIN": f"Let me explain. {ctx['rubric']} Now check yourself: ",
            "RELEASE": f"Great work, you finished a stage! A checklist is unlocked, and the next template is ready. "
                       f"Now we start {ctx['stage_name']}. ",
            "COMPLETE": "You've completed the design phase! ",
        }[mode]
        verdict = ctx.get("verdict")
        feedback = {
            "pass": "Good answer. ",
            "partial": "That's a start, but it's missing something. ",
            "misconception": "I think there's a misunderstanding there. ",
            "evasive": "We'll get there together. ",
        }.get(verdict or "", "")
        if mode == "COMPLETE":
            q = "In two sentences, what is the biggest trade-off in your design?"
        message = f"{feedback}{lead}**{q}**"
        return {"message": message, "question": q}

    def evaluate(self, turn_prompt: str, ctx: dict) -> dict:
        answer = ctx["answer"]
        words = len(answer.split())
        if EVASIVE_PATTERNS.search(answer) and words < 25:
            verdict = "evasive"
        elif words >= 25:
            verdict = "pass"
        elif words >= 8:
            verdict = "partial"
        else:
            verdict = "misconception"
        brief_terms = {w.lower() for w in re.findall(r"[A-Za-z]{5,}", ctx["brief"])}
        grounded = any(w.lower() in brief_terms for w in re.findall(r"[A-Za-z]{5,}", answer))
        return {
            "verdict": verdict,
            "covered": [],
            "missing": [] if verdict == "pass" else ["(mock) more detail needed"],
            "misconception": "(mock) answer too short" if verdict == "misconception" else None,
            "grounded": grounded,
            "consistent": True,
            "notes": f"(mock) verdict {verdict}",
        }


def get_client() -> LLMClient:
    if config.LLM_PROVIDER == "claude":
        return ClaudeClient()
    return MockClient()
