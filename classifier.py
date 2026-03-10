"""
AI-based role classifier.

Strategy
--------
1. **Learned mappings** – if auto-learning is enabled, check ``learning_db.json``
   for previously learned role→folder mappings first.
2. **Keyword match** – fast, free, checked via ``KEYWORD_ROLE_MAP`` from config.
3. **Google Gemini** – if no keyword hit and an API key is configured, sends the
   extracted role together with the full folder tree to the model and asks it to
   return the single best-matching folder path.
4. **Fallback** – if everything fails (or confidence is low), returns the
   Unsorted path so the resume is saved for manual review.
"""

from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from config import (
    ENABLE_AUTO_LEARNING,
    KEYWORD_ROLE_MAP,
    LEARNING_DB_FILE,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    YEAR,
    get_all_folder_paths,
)

logger = logging.getLogger("resume_sorter.classifier")

# ──────────────────────────────────────────────
# Role extraction helpers
# ──────────────────────────────────────────────

# Patterns that commonly appear in application subjects / bodies
_ROLE_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(?:applying\s+for|application\s+for|applied\s+for)[:\s\u2013\u2014-]*(.+)",
        re.I,
    ),
    re.compile(r"(?:job\s+application)[:\s\u2013\u2014-]*(.+)", re.I),
    re.compile(r"(?:position\s+of|role\s+of)[:\s\u2013\u2014-]*(.+)", re.I),
    re.compile(
        r"(?:interested\s+in\s+the\s+position\s+of)[:\s\u2013\u2014-]*(.+)", re.I
    ),
    re.compile(r"(?:apply\s+for\s+the\s+post\s+of)[:\s\u2013\u2014-]*(.+)", re.I),
    re.compile(r"(?:post\s+of)[:\s\u2013\u2014-]*(.+)", re.I),
    re.compile(r"(?:application\s*[-\u2013\u2014]\s*)(.+)", re.I),
]


def _clean_role(role: str) -> str:
    """Remove noise prefixes/suffixes from an extracted role string."""
    role = role.strip().rstrip(".")
    # Strip leading noise like "the Post of", "the position of", "the post"
    role = re.sub(
        r"^\s*the\s+(post|position|role)\s+(?:(?:of|for)\s+)?",
        "",
        role,
        flags=re.I,
    ).strip()
    # Strip trailing "at XYZ Corp" but keep dept qualifiers
    role = re.sub(
        r"\s+(?:at|@)\s+(?!cse|ece|eee|ce|bm|bt|ash).*$",
        "",
        role,
        flags=re.I,
    )
    # Strip trailing "-Reg", "- Reg"
    role = re.sub(r"\s*-\s*Reg\s*$", "", role, flags=re.I)
    return role.strip()


def extract_role(subject: str, body: str = "", filename: str = "") -> str:
    """
    Try to pull a role/title string from the email subject, body, or filename.

    Returns the best candidate string (may still need classification) or an
    empty string if nothing useful is found.
    """
    # Strip Fwd:/Re: prefixes from subject for matching
    clean_subj = re.sub(r"^(re|fwd?)\s*:\s*", "", subject, flags=re.I).strip()

    # 1. Try regex patterns on cleaned subject first, then body
    for text in [clean_subj, body]:
        for pat in _ROLE_PATTERNS:
            m = pat.search(text)
            if m:
                role = _clean_role(m.group(1))
                if role:
                    return role

    # 2. If subject looks like a role itself (short)
    if 2 < len(clean_subj.split()) <= 8:
        return clean_subj

    # 3. Try the attachment filename (strip extension)
    if filename:
        name_part = re.sub(r"\.(pdf|docx?|txt)$", "", filename, flags=re.I)
        name_part = re.sub(r"[_\-]+", " ", name_part).strip()
        if name_part:
            return name_part

    return ""


# ──────────────────────────────────────────────
# Auto-learning database
# ──────────────────────────────────────────────


def _load_learning_db() -> dict[str, str]:
    """Load learned role→folder mappings from learning_db.json."""
    if not LEARNING_DB_FILE.exists():
        return {}
    try:
        with open(LEARNING_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load learning DB: %s", exc)
        return {}


def save_learning_db(db: dict[str, str]) -> None:
    """Persist the learning database to disk."""
    LEARNING_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEARNING_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def add_learned_mapping(role: str, folder: str) -> None:
    """Record a new role→folder mapping in the learning database."""
    db = _load_learning_db()
    norm = _normalise(role)
    if norm and norm not in db:
        db[norm] = folder
        save_learning_db(db)
        logger.info("Learned mapping: '%s' → %s", norm, folder)


def classify_by_learning(role: str) -> Optional[str]:
    """Check the learning DB for a previously learned classification."""
    if not ENABLE_AUTO_LEARNING:
        return None
    db = _load_learning_db()
    norm = _normalise(role)
    if norm in db:
        folder = db[norm]
        logger.info("Learned match: '%s' → %s", role, folder)
        return folder
    return None


# ──────────────────────────────────────────────
# Keyword-based classifier
# ──────────────────────────────────────────────


def _normalise(text: str) -> str:
    """Lower-case, collapse whitespace, strip punctuation except &."""
    text = text.lower().strip()
    text = re.sub(r"[\u2013\u2014\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def classify_by_keyword(role: str) -> Optional[str]:
    """Return a folder path if the role matches a keyword, else None."""
    norm = _normalise(role)
    # Exact hit
    if norm in KEYWORD_ROLE_MAP:
        return KEYWORD_ROLE_MAP[norm]

    # Substring / fuzzy
    best_score = 0.0
    best_path: Optional[str] = None
    for keyword, folder_path in KEYWORD_ROLE_MAP.items():
        # Check if keyword appears inside the role text
        if keyword in norm:
            # For known department identifiers (≥4 chars), treat as strong match
            if len(keyword) >= 4:
                score = 1.0
            else:
                score = len(keyword) / len(norm) if norm else 0
            if score > best_score:
                best_score = score
                best_path = folder_path
        # Also check if role text appears inside the keyword
        elif norm in keyword:
            score = len(norm) / len(keyword) if keyword else 0
            if score > best_score:
                best_score = score
                best_path = folder_path
        # Also try fuzzy similarity
        else:
            ratio = SequenceMatcher(None, norm, keyword).ratio()
            if ratio > 0.75 and ratio > best_score:
                best_score = ratio
                best_path = folder_path

    if best_path and best_score >= 0.45:
        logger.info(
            "Keyword match: '%s' → %s (score=%.2f)", role, best_path, best_score
        )
        return best_path

    return None


# ──────────────────────────────────────────────
# Google Gemini-based classifier
# ──────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a classifier that maps job application roles to the correct folder in an educational institution's organisational structure.

You will receive:
1. A role title extracted from a job application email.
2. A list of all available folder paths.

Reply with ONLY a JSON object with two keys:
- "folder": the single best-matching folder path (exactly as listed)
- "confidence": a float between 0 and 1

If you cannot confidently match, set confidence below 0.5 and folder to "UNSORTED".

Decision rules:
- "Assistant Professor", "Professor", "Faculty" roles map to the <Department>/Faculty folder.
- "Lab Technician", "Lab Assistant", "Lab" roles map to the <Department>/Lab folder.
- Roles like "Librarian", "Library Assistant" map to Librarian.
- Roles like "Hostel Warden", "Student Counsellor" map to Dean/Student Affairs.
- Roles like "Placement Officer" map to Dean/Placement & Career Guidance.
- Roles like "Research Associate" map to Dean/Research.
- Physical education / sports roles map to Physical Education.
- Administrative roles map to Dean/Administration.
- Chemistry/Physics/Maths/Economics/English/Mechanical teaching roles map to ASH/<subject>.
- Biomedical / Biomedical Engineering roles map to BM/Faculty or BM/Lab.
- Biotechnology roles map to BT/Faculty or BT/Lab.
- Civil Engineering roles map to CE/Faculty or CE/Lab.
- CSE / Computer Science roles map to CSE/Faculty or CSE/Lab.
- ECE / Electronics & Communication roles map to ECE/Faculty or ECE/Lab.
- EEE / Electrical & Electronics roles map to EEE/Faculty or EEE/Lab.
"""


def classify_by_ai(role: str) -> Optional[str]:
    """
    Use Google Gemini to classify the role.

    Returns a folder path string or None on failure / low confidence.
    """
    if not GEMINI_API_KEY:
        logger.debug("No Gemini API key configured – skipping AI classification.")
        return None

    try:
        import google.generativeai as genai  # type: ignore
    except ImportError:
        logger.warning(
            "google-generativeai package not installed – skipping AI classification."
        )
        return None

    all_folders = get_all_folder_paths()
    user_prompt = f"Role: {role}\n\nAvailable folders:\n" + "\n".join(all_folders)
    full_prompt = _SYSTEM_PROMPT + "\n" + user_prompt

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0,
                max_output_tokens=150,
            ),
        )
        text = response.text.strip()
        # Parse JSON from the response (handle markdown fences)
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        result = json.loads(text)

        folder = result.get("folder", "UNSORTED")
        confidence = float(result.get("confidence", 0))

        logger.info(
            "AI classification: '%s' → %s (confidence=%.2f)", role, folder, confidence
        )

        if confidence >= 0.5 and folder != "UNSORTED" and folder in all_folders:
            return folder

        logger.warning(
            "AI confidence too low or folder invalid for role '%s' "
            "(confidence=%.2f, folder=%s)",
            role,
            confidence,
            folder,
        )
        return None

    except Exception as exc:  # noqa: BLE001
        logger.error("AI classification failed: %s", exc)
        return None


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────


def classify_role(role: str) -> str:
    """
    Determine the destination folder path for a given role string.

    Falls back to Unsorted if nothing matches.  Returns a *relative* path
    like ``2026/CSE/Faculty``.
    """
    if not role:
        logger.info("No role detected – filing under Unsorted.")
        return f"{YEAR}/Unsorted"

    # Step 0 – check learned mappings (auto-learning)
    folder = classify_by_learning(role)
    if folder:
        return folder

    # Step 1 – keyword lookup (free & instant)
    folder = classify_by_keyword(role)
    if folder:
        return folder

    # Step 2 – AI classification
    folder = classify_by_ai(role)
    if folder:
        return folder

    # Step 3 – give up
    logger.info("Could not classify role '%s' – filing under Unsorted.", role)
    return f"{YEAR}/Unsorted"
