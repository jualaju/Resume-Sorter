"""
Configuration for the Resume Sorter application.

Loads settings from ``config.json`` (user-editable) with optional overrides
from a ``.env`` file for secrets (gmail password, OpenAI key).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Load config.json
# ──────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _load_config() -> dict:
    """Load config.json and return as dict.  Exit with error if missing."""
    if not _CONFIG_PATH.exists():
        print(
            f"ERROR: config.json not found at {_CONFIG_PATH}\n"
            "Create a config.json file with your settings.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_cfg = _load_config()

# ──────────────────────────────────────────────
# Gmail credentials  (.env overrides config.json)
# ──────────────────────────────────────────────
GMAIL_ADDRESS: str = os.getenv("GMAIL_ADDRESS") or _cfg.get(
    "gmail_email", "your_email@gmail.com"
)
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD") or _cfg.get(
    "app_password", "your_app_password"
)

# IMAP settings (fixed for Gmail)
IMAP_SERVER: str = "imap.gmail.com"
IMAP_PORT: int = 993

# Gmail label / mailbox to read from.
MAILBOX: str = os.getenv("MAILBOX") or _cfg.get("mailbox", "INBOX")

# Label to move processed emails to.
PROCESSED_LABEL: str = _cfg.get("processed_label", "Processed Applications")

# ──────────────────────────────────────────────
# Gemini API key (.env overrides config.json)
# ──────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or _cfg.get("gemini_api_key", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL") or _cfg.get(
    "gemini_model", "gemini-2.0-flash"
)

# ──────────────────────────────────────────────
# File-system paths
# ──────────────────────────────────────────────
BASE_DIR: Path = Path(
    os.getenv("BASE_DIR")
    or _cfg.get("base_folder", str(Path(__file__).resolve().parent / "Resumes"))
)
YEAR: str = _cfg.get("year", "2026")
UNSORTED_DIR: Path = BASE_DIR / YEAR / "Unsorted"
LOG_FILE: Path = BASE_DIR / "processing_log.csv"
HASHES_FILE: Path = BASE_DIR / "hashes.json"
LEARNING_DB_FILE: Path = BASE_DIR / "learning_db.json"
ERROR_LOG_FILE: Path = BASE_DIR / "Errors" / "error_log.csv"
MULTI_ATTACHMENT_REVIEW_FILE: Path = BASE_DIR / "multi_attachment_review.csv"
INVALID_DIR: Path = BASE_DIR / "Invalid_Resumes"
ERRORS_DIR: Path = BASE_DIR / "Errors"
DEPARTMENT_REPORT_FILE: Path = BASE_DIR / "department_report.csv"

# ──────────────────────────────────────────────
# Accepted resume file extensions
# ──────────────────────────────────────────────
ACCEPTED_EXTENSIONS: set = set(
    _cfg.get("accepted_extensions", [".pdf", ".doc", ".docx"])
)

# ──────────────────────────────────────────────
# Email subject filtering keywords
# ──────────────────────────────────────────────
ALLOWED_SUBJECT_KEYWORDS: list[str] = _cfg.get("allowed_subject_keywords", [])

# ──────────────────────────────────────────────
# Real-time polling interval
# ──────────────────────────────────────────────
REALTIME_POLL_INTERVAL: int = _cfg.get("realtime_poll_interval_seconds", 60)

# ──────────────────────────────────────────────
# Feature toggles
# ──────────────────────────────────────────────
ENABLE_REAL_TIME: bool = _cfg.get("enable_real_time", False)
ENABLE_RESUME_AI: bool = _cfg.get("enable_resume_ai", False)
ENABLE_LOGGING: bool = _cfg.get("enable_logging", True)
ENABLE_DUPLICATE_CHECK: bool = _cfg.get("enable_duplicate_check", True)
ENABLE_SUBJECT_FILTER: bool = _cfg.get("enable_subject_filter", False)
ENABLE_AUTO_LEARNING: bool = _cfg.get("enable_auto_learning", False)
ENABLE_STATISTICS: bool = _cfg.get("enable_statistics", True)

# ──────────────────────────────────────────────
# Organisational folder structure (year/dept/sub)
# ──────────────────────────────────────────────
FOLDER_STRUCTURE: dict = _cfg.get(
    "folder_structure",
    {
        "ASH": [
            "Chemistry",
            "Economics",
            "English",
            "Maths",
            "Mechanical",
            "Physics",
        ],
        "BM": ["Faculty", "Lab"],
        "BT": ["Faculty", "Lab"],
        "CE": ["Faculty", "Lab"],
        "CSE": ["Faculty", "Lab"],
        "Dean": [
            "Administration",
            "Consultancy & Industry Connect",
            "Dean PG",
            "Dean UG",
            "Placement & Career Guidance",
            "Planning",
            "Research",
            "Student Affairs",
        ],
        "ECE": ["Faculty", "Lab"],
        "EEE": ["Faculty", "Lab"],
        "Librarian": [],
        "Physical Education": [],
    },
)


def get_all_folder_paths() -> list[str]:
    """Return a flat list of every valid destination folder path."""
    paths: list[str] = []
    for dept, subs in FOLDER_STRUCTURE.items():
        if not subs:
            paths.append(f"{YEAR}/{dept}")
        else:
            for sub in subs:
                paths.append(f"{YEAR}/{dept}/{sub}")
    return paths


# ──────────────────────────────────────────────
# Quick keyword → folder mapping
# Loaded from config.json; paths are prefixed with YEAR automatically.
# ──────────────────────────────────────────────
_raw_keyword_map: dict[str, str] = _cfg.get("keyword_role_map", {})

KEYWORD_ROLE_MAP: dict[str, str] = {}
for _kw, _path in _raw_keyword_map.items():
    # If the path already starts with the year, use as-is; otherwise prepend YEAR
    if _path.startswith(f"{YEAR}/"):
        KEYWORD_ROLE_MAP[_kw] = _path
    else:
        KEYWORD_ROLE_MAP[_kw] = f"{YEAR}/{_path}"


def get_config() -> dict:
    """Return the full raw configuration dictionary."""
    return _cfg.copy()
