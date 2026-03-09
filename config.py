"""
Configuration for the Resume Sorter application.

Edit the values below or override them via a .env file placed next to this script.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Gmail credentials
# ──────────────────────────────────────────────
GMAIL_ADDRESS: str = os.getenv("GMAIL_ADDRESS", "your_email@gmail.com")
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "your_app_password")

# IMAP settings
IMAP_SERVER: str = "imap.gmail.com"
IMAP_PORT: int = 993

# Gmail label / mailbox to read from.  Use "INBOX" or a custom label.
MAILBOX: str = os.getenv("MAILBOX", "INBOX")

# Label to move processed emails to (will be created if missing).
PROCESSED_LABEL: str = "Processed Applications"

# ──────────────────────────────────────────────
# OpenAI API key (for AI-based classification)
# ──────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ──────────────────────────────────────────────
# File-system paths
# ──────────────────────────────────────────────
BASE_DIR: Path = Path(os.getenv("BASE_DIR", Path(__file__).resolve().parent / "Resumes"))
UNSORTED_DIR: Path = BASE_DIR / "2026" / "Unsorted"
LOG_FILE: Path = BASE_DIR / "processing_log.csv"

# ──────────────────────────────────────────────
# Accepted resume file extensions
# ──────────────────────────────────────────────
ACCEPTED_EXTENSIONS: set = {".pdf", ".doc", ".docx"}

# ──────────────────────────────────────────────
# Organisational folder structure (year/dept/sub)
# ──────────────────────────────────────────────
FOLDER_STRUCTURE: dict = {
    "ASH": ["Chemistry", "Economics", "English", "Maths", "Mechanical", "Physics"],
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
}

YEAR: str = "2026"


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
# Quick keyword → folder mapping (checked BEFORE
# the AI classifier for instant, zero-cost matches)
# ──────────────────────────────────────────────
KEYWORD_ROLE_MAP: dict[str, str] = {
    # CSE
    "software engineer": f"{YEAR}/CSE/Faculty",
    "data analyst": f"{YEAR}/CSE/Faculty",
    "web developer": f"{YEAR}/CSE/Faculty",
    "assistant professor cse": f"{YEAR}/CSE/Faculty",
    "assistant professor - cse": f"{YEAR}/CSE/Faculty",
    "assistant professor – cse": f"{YEAR}/CSE/Faculty",
    "professor cse": f"{YEAR}/CSE/Faculty",
    "cse faculty": f"{YEAR}/CSE/Faculty",
    "cse lab": f"{YEAR}/CSE/Lab",
    "lab technician cse": f"{YEAR}/CSE/Lab",
    # ECE
    "embedded engineer": f"{YEAR}/ECE/Faculty",
    "assistant professor ece": f"{YEAR}/ECE/Faculty",
    "assistant professor - ece": f"{YEAR}/ECE/Faculty",
    "assistant professor – ece": f"{YEAR}/ECE/Faculty",
    "professor ece": f"{YEAR}/ECE/Faculty",
    "ece faculty": f"{YEAR}/ECE/Faculty",
    "ece lab": f"{YEAR}/ECE/Lab",
    "lab technician ece": f"{YEAR}/ECE/Lab",
    "lab technician – ece": f"{YEAR}/ECE/Lab",
    # EEE
    "assistant professor eee": f"{YEAR}/EEE/Faculty",
    "assistant professor - eee": f"{YEAR}/EEE/Faculty",
    "assistant professor – eee": f"{YEAR}/EEE/Faculty",
    "professor eee": f"{YEAR}/EEE/Faculty",
    "eee faculty": f"{YEAR}/EEE/Faculty",
    "eee lab": f"{YEAR}/EEE/Lab",
    "lab technician eee": f"{YEAR}/EEE/Lab",
    "lab technician – eee": f"{YEAR}/EEE/Lab",
    # CE
    "assistant professor ce": f"{YEAR}/CE/Faculty",
    "assistant professor - ce": f"{YEAR}/CE/Faculty",
    "assistant professor – ce": f"{YEAR}/CE/Faculty",
    "civil engineering": f"{YEAR}/CE/Faculty",
    "ce faculty": f"{YEAR}/CE/Faculty",
    "ce lab": f"{YEAR}/CE/Lab",
    "lab technician ce": f"{YEAR}/CE/Lab",
    "lab technician – ce": f"{YEAR}/CE/Lab",
    # BM
    "assistant professor bm": f"{YEAR}/BM/Faculty",
    "biomedical": f"{YEAR}/BM/Faculty",
    "bm faculty": f"{YEAR}/BM/Faculty",
    "bm lab": f"{YEAR}/BM/Lab",
    "lab technician bm": f"{YEAR}/BM/Lab",
    "lab technician – bm": f"{YEAR}/BM/Lab",
    # BT
    "assistant professor bt": f"{YEAR}/BT/Faculty",
    "biotechnology": f"{YEAR}/BT/Faculty",
    "bt faculty": f"{YEAR}/BT/Faculty",
    "bt lab": f"{YEAR}/BT/Lab",
    "lab technician bt": f"{YEAR}/BT/Lab",
    "lab technician – bt": f"{YEAR}/BT/Lab",
    # ASH
    "assistant professor in chemistry": f"{YEAR}/ASH/Chemistry",
    "faculty position in chemistry": f"{YEAR}/ASH/Chemistry",
    "chemistry": f"{YEAR}/ASH/Chemistry",
    "professor chemistry": f"{YEAR}/ASH/Chemistry",
    "economics": f"{YEAR}/ASH/Economics",
    "professor economics": f"{YEAR}/ASH/Economics",
    "english": f"{YEAR}/ASH/English",
    "professor english": f"{YEAR}/ASH/English",
    "maths": f"{YEAR}/ASH/Maths",
    "mathematics": f"{YEAR}/ASH/Maths",
    "professor maths": f"{YEAR}/ASH/Maths",
    "mechanical": f"{YEAR}/ASH/Mechanical",
    "professor mechanical": f"{YEAR}/ASH/Mechanical",
    "mechanical engineering": f"{YEAR}/ASH/Mechanical",
    "faculty position in mechanical engineering department": f"{YEAR}/ASH/Mechanical",
    "faculty position in mechanical engineering": f"{YEAR}/ASH/Mechanical",
    "faculty mechanical engineering": f"{YEAR}/ASH/Mechanical",
    "physics": f"{YEAR}/ASH/Physics",
    "professor physics": f"{YEAR}/ASH/Physics",
    # Dean offices
    "administration": f"{YEAR}/Dean/Administration",
    "admin officer": f"{YEAR}/Dean/Administration",
    "hostel warden": f"{YEAR}/Dean/Student Affairs",
    "student affairs": f"{YEAR}/Dean/Student Affairs",
    "placement officer": f"{YEAR}/Dean/Placement & Career Guidance",
    "placement": f"{YEAR}/Dean/Placement & Career Guidance",
    "career guidance": f"{YEAR}/Dean/Placement & Career Guidance",
    "consultancy": f"{YEAR}/Dean/Consultancy & Industry Connect",
    "industry connect": f"{YEAR}/Dean/Consultancy & Industry Connect",
    "dean pg": f"{YEAR}/Dean/Dean PG",
    "dean ug": f"{YEAR}/Dean/Dean UG",
    "research associate": f"{YEAR}/Dean/Research",
    "research officer": f"{YEAR}/Dean/Research",
    "research": f"{YEAR}/Dean/Research",
    "planning": f"{YEAR}/Dean/Planning",
    "planning officer": f"{YEAR}/Dean/Planning",
    # Standalone
    "librarian": f"{YEAR}/Librarian",
    "library assistant": f"{YEAR}/Librarian",
    "library": f"{YEAR}/Librarian",
    "physical education": f"{YEAR}/Physical Education",
    "sports officer": f"{YEAR}/Physical Education",
    "physical director": f"{YEAR}/Physical Education",
    "gym instructor": f"{YEAR}/Physical Education",
}
