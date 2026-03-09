"""
Resume content reader for AI-based department detection.

When ``enable_resume_ai`` is **True** in config.json and the email-based
classification lands in *Unsorted*, this module extracts text from the
resume file and attempts to detect the correct department from the content
itself (education, skills, experience sections).

Supports:
- PDF  → via ``pdfplumber``
- DOCX → via ``python-docx``
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

from config import FOLDER_STRUCTURE, YEAR

logger = logging.getLogger("resume_sorter.reader")

# ──────────────────────────────────────────────
# Text extraction
# ──────────────────────────────────────────────


def extract_text_from_pdf(data: bytes) -> str:
    """Extract plain text from a PDF file given as raw bytes."""
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.warning("pdfplumber not installed – cannot read PDF content.")
        return ""

    text_parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to extract text from PDF: %s", exc)
    return "\n".join(text_parts)


def extract_text_from_docx(data: bytes) -> str:
    """Extract plain text from a DOCX file given as raw bytes."""
    try:
        import docx  # type: ignore  (python-docx)
    except ImportError:
        logger.warning("python-docx not installed – cannot read DOCX content.")
        return ""

    try:
        doc = docx.Document(io.BytesIO(data))
        return "\n".join(para.text for para in doc.paragraphs if para.text)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to extract text from DOCX: %s", exc)
        return ""


def extract_text(data: bytes, extension: str) -> str:
    """Extract text from a resume file based on its extension."""
    ext = extension.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(data)
    elif ext in (".doc", ".docx"):
        return extract_text_from_docx(data)
    return ""


# ──────────────────────────────────────────────
# Department detection from resume content
# ──────────────────────────────────────────────

# Patterns mapping keywords found in resume content → folder paths.
# Checked in order; first match wins.  More specific patterns come first.
_CONTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # CSE / IT
    (
        re.compile(
            r"\b(computer\s+science|cse|information\s+technology|software\s+engineer|data\s+science|machine\s+learning|artificial\s+intelligence|cyber\s*security|cloud\s+computing|full[\s-]?stack|web\s+develop|python|java\b|c\+\+)\b",
            re.I,
        ),
        f"{YEAR}/CSE/Faculty",
    ),
    # ECE
    (
        re.compile(
            r"\b(electronics?\s*(and|&)?\s*communication|ece|vlsi|embedded\s+systems?|signal\s+processing|antenna|microwave|telecommunication)\b",
            re.I,
        ),
        f"{YEAR}/ECE/Faculty",
    ),
    # EEE
    (
        re.compile(
            r"\b(electrical\s*(and|&)?\s*electronics?|eee|power\s+systems?|power\s+electronics|high\s+voltage|electrical\s+machines?)\b",
            re.I,
        ),
        f"{YEAR}/EEE/Faculty",
    ),
    # CE
    (
        re.compile(
            r"\b(civil\s+engineer|structural\s+engineer|geotechnical|transportation\s+engineer|construction\s+manage|concrete|surveying)\b",
            re.I,
        ),
        f"{YEAR}/CE/Faculty",
    ),
    # BM
    (
        re.compile(
            r"\b(bio\s*medical|biomedical\s+engineer|medical\s+instrument|bio[\s-]?signal|bio[\s-]?mechanics)\b",
            re.I,
        ),
        f"{YEAR}/BM/Faculty",
    ),
    # BT
    (
        re.compile(
            r"\b(bio\s*technolog|genetic\s+engineer|molecular\s+biology|microbiology|biochemistry|bio[\s-]?informatics)\b",
            re.I,
        ),
        f"{YEAR}/BT/Faculty",
    ),
    # ASH subjects
    (
        re.compile(
            r"\b(mechanical\s+engineer|thermodynamics|fluid\s+mechanics|manufacturing|robotics|automobile)\b",
            re.I,
        ),
        f"{YEAR}/ASH/Mechanical",
    ),
    (
        re.compile(
            r"\b(chemistry|chemical\s+engineer|organic\s+chemistry|inorganic\s+chemistry|analytical\s+chemistry)\b",
            re.I,
        ),
        f"{YEAR}/ASH/Chemistry",
    ),
    (
        re.compile(
            r"\b(physics|quantum\s+mechanics|optics|condensed\s+matter|astrophysics|semiconductor)\b",
            re.I,
        ),
        f"{YEAR}/ASH/Physics",
    ),
    (
        re.compile(
            r"\b(mathematics|maths|algebra|calculus|statistics|probability|differential\s+equations)\b",
            re.I,
        ),
        f"{YEAR}/ASH/Maths",
    ),
    (
        re.compile(
            r"\b(economics|econometrics|macro\s*economics|micro\s*economics|financial\s+economics)\b",
            re.I,
        ),
        f"{YEAR}/ASH/Economics",
    ),
    (
        re.compile(
            r"\b(english\s+literature|english\s+language|linguistics|communication\s+skills|technical\s+writing)\b",
            re.I,
        ),
        f"{YEAR}/ASH/English",
    ),
    # Dean offices
    (
        re.compile(
            r"\b(hostel\s+warden|student\s+affairs|counsellor|counselor|student\s+welfare)\b",
            re.I,
        ),
        f"{YEAR}/Dean/Student Affairs",
    ),
    (
        re.compile(
            r"\b(placement|career\s+guidance|campus\s+recruit|training\s+and\s+placement)\b",
            re.I,
        ),
        f"{YEAR}/Dean/Placement & Career Guidance",
    ),
    (
        re.compile(
            r"\b(research\s+associate|research\s+officer|research\s+scholar|phd\s+supervisor)\b",
            re.I,
        ),
        f"{YEAR}/Dean/Research",
    ),
    (
        re.compile(
            r"\b(administration|admin\s+officer|office\s+manage|registrar)\b", re.I
        ),
        f"{YEAR}/Dean/Administration",
    ),
    # Standalone
    (
        re.compile(r"\b(librarian|library\s+science|library\s+manage)\b", re.I),
        f"{YEAR}/Librarian",
    ),
    (
        re.compile(
            r"\b(physical\s+education|sports?\s+officer|gym\s+instructor|physical\s+director|fitness\s+trainer)\b",
            re.I,
        ),
        f"{YEAR}/Physical Education",
    ),
]


def detect_department_from_content(text: str) -> Optional[str]:
    """
    Scan resume text for keywords indicating a department.

    Returns a folder path like ``2026/CSE/Faculty`` or ``None`` if no
    department can be determined with confidence.
    """
    if not text or len(text) < 50:
        return None

    # Count hits per pattern to find the strongest match
    scores: dict[str, int] = {}
    for pattern, folder in _CONTENT_PATTERNS:
        hits = len(pattern.findall(text))
        if hits:
            scores[folder] = scores.get(folder, 0) + hits

    if not scores:
        return None

    # Return the folder with the most keyword hits
    best_folder = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best_folder]

    if best_score >= 2:
        logger.info("Resume content → %s (score=%d)", best_folder, best_score)
        return best_folder

    # Single hit is too weak — might be a false positive
    logger.debug(
        "Resume content: weak match %s (score=%d) – not confident enough",
        best_folder,
        best_score,
    )
    return None
