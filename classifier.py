"""
AI-based role classifier.

Strategy
--------
1. **Keyword match** – fast, free, checked first via KEYWORD_ROLE_MAP.
2. **OpenAI (GPT)** – if no keyword hit and an API key is configured, sends the
   extracted role together with the full folder tree to the model and asks it to
   return the single best-matching folder path.
3. **Fallback** – if everything fails (or confidence is low), returns the
   Unsorted path so the resume is saved for manual review.
"""

from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from config import (
    KEYWORD_ROLE_MAP,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    UNSORTED_DIR,
    YEAR,
    get_all_folder_paths,
)

logger = logging.getLogger("resume_sorter.classifier")

# ──────────────────────────────────────────────
# Role extraction helpers
# ──────────────────────────────────────────────

# Patterns that commonly appear in application subjects / bodies
_ROLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:applying\s+for|application\s+for|applied\s+for)[:\s–—-]*(.+)", re.I),
    re.compile(r"(?:job\s+application)[:\s–—-]*(.+)", re.I),
    re.compile(r"(?:position\s+of|role\s+of)[:\s–—-]*(.+)", re.I),
    re.compile(r"(?:interested\s+in\s+the\s+position\s+of)[:\s–—-]*(.+)", re.I),
    re.compile(r"(?:apply\s+for\s+the\s+post\s+of)[:\s–—-]*(.+)", re.I),
    re.compile(r"(?:post\s+of)[:\s–—-]*(.+)", re.I),
    re.compile(r"(?:application\s*[-–—]\s*)(.+)", re.I),
]


def extract_role(subject: str, body: str = "", filename: str = "") -> str:
    """
    Try to pull a role/title string from the email subject, body, or filename.

    Returns the best candidate string (may still need classification) or an
    empty string if nothing useful is found.
    """
    # 1. Try regex patterns on subject first, then body
    for text in [subject, body]:
        for pat in _ROLE_PATTERNS:
            m = pat.search(text)
            if m:
                role = m.group(1).strip().rstrip(".")
                # Remove trailing noise like "at XYZ Corp" but keep
                # department qualifiers like "in Mechanical Engineering"
                role = re.sub(
                    r"\s+(?:at|@)\s+(?!cse|ece|eee|ce|bm|bt|ash).*$",
                    "", role, flags=re.I,
                )
                if role:
                    return role

    # 2. If subject looks like a role itself (short, no "fw:"/"re:" prefix)
    clean_subj = re.sub(r"^(re|fwd?)\s*:\s*", "", subject, flags=re.I).strip()
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
# Keyword-based classifier
# ──────────────────────────────────────────────


def _normalise(text: str) -> str:
    """Lower-case, collapse whitespace, strip punctuation except &."""
    text = text.lower().strip()
    text = re.sub(r"[–—\-]+", " ", text)
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
            score = len(keyword) / len(norm) if norm else 0
            if score > best_score:
                best_score = score
                best_path = folder_path
        # Also try fuzzy similarity
        ratio = SequenceMatcher(None, norm, keyword).ratio()
        if ratio > 0.80 and ratio > best_score:
            best_score = ratio
            best_path = folder_path

    if best_path and best_score >= 0.45:
        logger.info("Keyword match: '%s' → %s (score=%.2f)", role, best_path, best_score)
        return best_path

    return None


# ──────────────────────────────────────────────
# OpenAI-based classifier
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
"""


def classify_by_ai(role: str) -> Optional[str]:
    """
    Use OpenAI chat completion to classify the role.

    Returns a folder path string or None on failure / low confidence.
    """
    if not OPENAI_API_KEY:
        logger.debug("No OpenAI API key configured – skipping AI classification.")
        return None

    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        logger.warning("openai package not installed – skipping AI classification.")
        return None

    all_folders = get_all_folder_paths()
    user_prompt = (
        f"Role: {role}\n\n"
        f"Available folders:\n" + "\n".join(all_folders)
    )

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            max_tokens=150,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = response.choices[0].message.content.strip()
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
            "AI confidence too low or folder invalid for role '%s' (confidence=%.2f, folder=%s)",
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
