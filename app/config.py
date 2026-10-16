import os
from pathlib import Path

from dotenv import load_dotenv

# Settings are read from config.env in the project folder (a visible file you can open in any editor).
# A .env file is also supported. A value set in config.env wins over a system environment variable,
# so an old key saved elsewhere on the computer can't silently override the one you just pasted.
ROOT = Path(__file__).resolve().parent.parent
_system_key = os.environ.get("ANTHROPIC_API_KEY")
KEY_SOURCE = "system environment variable" if _system_key else None
for name in (".env", "config.env"):  # config.env loaded last, so it wins
    path = ROOT / name
    if path.exists():
        before = os.environ.get("ANTHROPIC_API_KEY")
        load_dotenv(path, override=True)
        after = os.environ.get("ANTHROPIC_API_KEY")
        if after and after != before or (after and not before):
            KEY_SOURCE = name
        if not after and before:  # a blank line in the file must not erase a real key
            os.environ["ANTHROPIC_API_KEY"] = before


def _clean_key(raw: str | None) -> str:
    """Remove quotes, spaces and line breaks that sneak in when a key is copied."""
    if not raw:
        return ""
    return "".join(raw.split()).strip("'\"“”‘’<>")


ANTHROPIC_API_KEY = _clean_key(os.getenv("ANTHROPIC_API_KEY"))
if ANTHROPIC_API_KEY:
    os.environ["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
else:
    KEY_SOURCE = None


def key_hint() -> str:
    """A safe description of the key in use, e.g. 'sk-ant-api03-…x7Qa (108 chars, from config.env)'."""
    if not ANTHROPIC_API_KEY:
        return "no key set"
    k = ANTHROPIC_API_KEY
    shown = f"{k[:13]}…{k[-4:]}" if len(k) > 20 else "(too short)"
    return f"{shown} ({len(k)} characters, from {KEY_SOURCE})"


def key_problem() -> str | None:
    """Obvious format problems we can spot without calling the API."""
    k = ANTHROPIC_API_KEY
    if not k:
        return None
    if not k.startswith("sk-ant-"):
        return "The key should start with 'sk-ant-'. Copy it again from console.anthropic.com > API Keys."
    if len(k) < 90:
        return f"The key is only {len(k)} characters; real keys are about 100+. It may have been cut off while copying."
    return None


# "claude" calls the Claude API; "mock" uses a deterministic fake for demos and tests.
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or ("claude" if ANTHROPIC_API_KEY else "mock")).strip()
TUTOR_MODEL = os.getenv("TUTOR_MODEL") or "claude-sonnet-5-5"
EVALUATOR_MODEL = os.getenv("EVALUATOR_MODEL") or "claude-haiku-5-5"
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{ROOT / 'tutor.db'}"

# Hint ladder: after this many failed attempts on one question, the tutor explains fully.
MAX_HINT_RUNG = 4

# PACE controls how long each stage takes.
#   start_tier      first question's tier (1 recall, 2 mechanism, 3 judgment)
#   core / other    tier a concept must reach to count as mastered
#   attempts        wrong or partial answers allowed per concept before the tutor explains it,
#                   marks it "revisit" for the instructor, and moves on
PACES = {
    "quick":    {"start_tier": 2, "core": 2, "other": 2, "attempts": 2},
    "standard": {"start_tier": 2, "core": 3, "other": 2, "attempts": 3},
    "thorough": {"start_tier": 1, "core": 3, "other": 3, "attempts": 5},
}
PACE = (os.getenv("PACE") or "standard").strip().lower()
if PACE not in PACES:
    PACE = "standard"

# Integrity signal thresholds (signals only inform instructors; they never block students).
LARGE_PASTE_CHARS = int(os.getenv("LARGE_PASTE_CHARS") or "150")
FAST_TYPING_CHARS_PER_SEC = float(os.getenv("FAST_TYPING_CHARS_PER_SEC") or "12")

# How many recent exchanges the tutor sees verbatim.
HISTORY_TURNS = 6
