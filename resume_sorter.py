#!/usr/bin/env python3
"""
resume_sorter.py
================
Connects to Gmail via IMAP, reads application emails, detects the applied
role, downloads resume attachments, classifies them into the correct
organisational folder, and logs every action.

Features (all toggle-controlled via config.json)
-------------------------------------------------
- SHA-256 duplicate detection
- Email subject filtering
- Real-time monitoring (polling)
- AI-based resume content reading
- Auto department learning
- Department statistics report
- Resume format validation (invalid formats → Invalid_Resumes/)
- Error handling folder (Errors/)
- Processing summary
- Dry-run mode

Usage
-----
    python resume_sorter.py                # process all emails
    python resume_sorter.py --dry-run      # preview without downloading
    python resume_sorter.py --realtime     # keep polling for new emails
    python resume_sorter.py --stats        # generate department statistics
    python resume_sorter.py --label Apps   # read from a custom Gmail label
"""

from __future__ import annotations

import argparse
import csv
import email
import email.header
import email.utils
import hashlib
import imaplib
import json
import logging
import re
import signal
import sys
import time
from collections import Counter
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Optional

from config import (
    ACCEPTED_EXTENSIONS,
    ALLOWED_SUBJECT_KEYWORDS,
    BASE_DIR,
    DEPARTMENT_REPORT_FILE,
    ENABLE_AUTO_LEARNING,
    ENABLE_DUPLICATE_CHECK,
    ENABLE_LOGGING,
    ENABLE_REAL_TIME,
    ENABLE_RESUME_AI,
    ENABLE_STATISTICS,
    ENABLE_SUBJECT_FILTER,
    ERROR_LOG_FILE,
    ERRORS_DIR,
    FOLDER_STRUCTURE,
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    HASHES_FILE,
    IMAP_PORT,
    IMAP_SERVER,
    INVALID_DIR,
    LEARNING_DB_FILE,
    LOG_FILE,
    MAILBOX,
    MULTI_ATTACHMENT_REVIEW_FILE,
    PROCESSED_LABEL,
    REALTIME_POLL_INTERVAL,
    UNSORTED_DIR,
    YEAR,
)
from classifier import add_learned_mapping, classify_role, extract_role

# ──────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("resume_sorter")

# Graceful shutdown flag for real-time mode
_shutdown = False


def _signal_handler(sig, frame):
    global _shutdown
    _shutdown = True
    logger.info("Shutdown signal received. Finishing current email…")


# ──────────────────────────────────────────────
# Processing counters
# ──────────────────────────────────────────────


def _new_counters() -> dict[str, int]:
    return {
        "emails_scanned": 0,
        "resumes_downloaded": 0,
        "duplicates_skipped": 0,
        "unsorted_resumes": 0,
        "invalid_format": 0,
        "filtered_out": 0,
        "errors": 0,
    }


def print_summary(counters: dict[str, int]) -> None:
    """Print a formatted processing summary."""
    print("\n" + "═" * 40)
    print("        PROCESSING SUMMARY")
    print("═" * 40)
    print(f"  Emails scanned:      {counters['emails_scanned']}")
    print(f"  Resumes downloaded:  {counters['resumes_downloaded']}")
    print(f"  Duplicates skipped:  {counters['duplicates_skipped']}")
    print(f"  Unsorted resumes:    {counters['unsorted_resumes']}")
    print(f"  Invalid format:      {counters['invalid_format']}")
    print(f"  Filtered out:        {counters['filtered_out']}")
    print(f"  Errors:              {counters['errors']}")
    print("═" * 40 + "\n")


# ──────────────────────────────────────────────
# Folder creation
# ──────────────────────────────────────────────


def create_folder_tree() -> None:
    """Create the full organisational folder tree under BASE_DIR if missing."""
    for dept, subs in FOLDER_STRUCTURE.items():
        if not subs:
            (BASE_DIR / YEAR / dept).mkdir(parents=True, exist_ok=True)
        else:
            for sub in subs:
                (BASE_DIR / YEAR / dept / sub).mkdir(parents=True, exist_ok=True)
    # Always ensure special folders exist
    UNSORTED_DIR.mkdir(parents=True, exist_ok=True)
    INVALID_DIR.mkdir(parents=True, exist_ok=True)
    ERRORS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Folder tree verified under %s", BASE_DIR)


# ──────────────────────────────────────────────
# SHA-256 duplicate detection
# ──────────────────────────────────────────────


def _load_hashes() -> dict[str, dict]:
    """Load the hash database from hashes.json."""
    if not HASHES_FILE.exists():
        return {}
    try:
        with open(HASHES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_hashes(hashes: dict[str, dict]) -> None:
    """Persist the hash database."""
    HASHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HASHES_FILE, "w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2, ensure_ascii=False)


def compute_file_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest for raw bytes."""
    return hashlib.sha256(data).hexdigest()


def is_duplicate(data: bytes, filename: str, email_from: str) -> bool:
    """Check if this file has been seen before. Records it if new.

    Uses two checks:
      1. SHA-256 hash match (identical content)
      2. filename + sender email match (same file from same person)
    """
    if not ENABLE_DUPLICATE_CHECK:
        return False
    file_hash = compute_file_hash(data)
    hashes = _load_hashes()
    # Check 1: SHA-256 hash match
    if file_hash in hashes:
        logger.info(
            "  ⊘  Duplicate detected (SHA-256 match): %s", hashes[file_hash]["filename"]
        )
        return True
    # Check 2: filename + email match
    for existing in hashes.values():
        if (
            existing.get("filename") == filename
            and existing.get("email_from") == email_from
        ):
            logger.info(
                "  ⊘  Duplicate detected (filename + sender match): %s", filename
            )
            return True
    # Record new hash
    hashes[file_hash] = {
        "filename": filename,
        "email_from": email_from,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    _save_hashes(hashes)
    return False


# ──────────────────────────────────────────────
# CSV log helpers
# ──────────────────────────────────────────────

_CSV_HEADERS = [
    "timestamp",
    "email_from",
    "email_address",
    "candidate_name",
    "detected_role",
    "classification_result",
    "destination_folder",
    "filename",
    "status",
]


def _init_csv_log() -> None:
    if not ENABLE_LOGGING:
        return
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_CSV_HEADERS)


def log_to_csv(
    email_from: str,
    email_address: str,
    candidate_name: str,
    detected_role: str,
    classification: str,
    dest_folder: str,
    filename: str,
    status: str,
) -> None:
    if not ENABLE_LOGGING:
        return
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                email_from,
                email_address,
                candidate_name,
                detected_role,
                classification,
                dest_folder,
                filename,
                status,
            ]
        )


_MULTI_ATTACHMENT_HEADERS = [
    "timestamp",
    "uid",
    "message_id",
    "email_from",
    "email_address",
    "subject",
    "candidate_name",
    "detected_role",
    "attachment_names",
    "status",
]


def _init_multi_attachment_review_log() -> None:
    if not MULTI_ATTACHMENT_REVIEW_FILE.exists():
        MULTI_ATTACHMENT_REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(MULTI_ATTACHMENT_REVIEW_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_MULTI_ATTACHMENT_HEADERS)


def log_multi_attachment_review(
    uid: bytes,
    message_id: str,
    email_from: str,
    email_address: str,
    subject: str,
    candidate_name: str,
    detected_role: str,
    attachment_names: list[str],
) -> None:
    _init_multi_attachment_review_log()
    with open(MULTI_ATTACHMENT_REVIEW_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                uid.decode("utf-8", errors="replace"),
                message_id,
                email_from,
                email_address,
                subject,
                candidate_name,
                detected_role,
                "; ".join(attachment_names),
                "AMBIGUOUS_ATTACHMENT",
            ]
        )


# ──────────────────────────────────────────────
# Error log helpers
# ──────────────────────────────────────────────


def _init_error_log() -> None:
    if not ERROR_LOG_FILE.exists():
        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ERROR_LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "email_from", "filename", "error_reason"])


def log_error(email_from: str, filename: str, error_reason: str) -> None:
    _init_error_log()
    with open(ERROR_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                email_from,
                filename,
                error_reason,
            ]
        )


# ──────────────────────────────────────────────
# Helper: decode header
# ──────────────────────────────────────────────


def decode_header_value(raw: Optional[str]) -> str:
    """Decode an RFC-2047 encoded header value into a plain string."""
    if raw is None:
        return ""
    parts = email.header.decode_header(raw)
    decoded_parts: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded_parts.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(data)
    return " ".join(decoded_parts)


# ──────────────────────────────────────────────
# Helper: extract email address
# ──────────────────────────────────────────────


def extract_email_address(from_header: str) -> str:
    """Extract the bare email address from a From header."""
    _, addr = email.utils.parseaddr(from_header)
    return addr or ""


# ──────────────────────────────────────────────
# Helper: extract candidate name
# ──────────────────────────────────────────────


def _extract_name_from_filename(filename: str) -> str:
    """Try to extract a person name from an attachment filename."""
    if not filename:
        return ""
    name_part = re.sub(r"\.(pdf|docx?)$", "", filename, flags=re.I)
    # Replace separators (underscores, hyphens) with spaces
    name_part = re.sub(r"[_\-]+", " ", name_part)
    # Replace dots between letters with spaces (e.g., Elavarasi.K → Elavarasi K)
    name_part = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", " ", name_part)
    name_part = name_part.strip()
    # Remove common non-name tokens
    name_part = re.sub(
        r"\b(resume|cv|application|cover\s*letter|qualifications?"
        r"|and\s+experiences?|experiences?|certificates?|documents?"
        r"|new|final|updated|latest|copy|scan|draft)\b",
        "",
        name_part,
        flags=re.I,
    ).strip()
    # Remove month abbreviations (with optional trailing digits) and pure numbers
    name_part = re.sub(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d*\b",
        "",
        name_part,
        flags=re.I,
    )
    name_part = re.sub(r"\b\d{2,4}\b", "", name_part)
    # Collapse whitespace
    name_part = re.sub(r"\s+", " ", name_part).strip(" .-–—")
    # A person name: 1+ alphabetic words
    if name_part and len(name_part) >= 2:
        words = name_part.split()
        alpha_words = [w for w in words if re.match(r"^[A-Za-z.]+$", w) and len(w) >= 1]
        if alpha_words:
            result = " ".join(alpha_words).title()
            if len(result) >= 2:
                return result
    return ""


def extract_candidate_name(from_header: str, subject: str, filename: str) -> str:
    """
    Best-effort extraction of the candidate's name.

    Priority:
      1. Filename (most reliable for forwarded emails like "Resume - John Doe.pdf")
      2. From header display name (if it looks like a person, not an org)
      3. Email local part
    """
    # 1. Try filename first — often contains the actual candidate name
    fname_name = _extract_name_from_filename(filename)
    if fname_name:
        return fname_name

    # 2. Display name from From header  e.g.  "John Doe <john@…>"
    display, _ = email.utils.parseaddr(from_header)
    if display and not re.match(r"^[\w.+-]+@", display):
        return display.strip()

    # 3. Email local part
    _, addr = email.utils.parseaddr(from_header)
    if addr:
        local = addr.split("@")[0]
        local = re.sub(r"[\d_.\-]+", " ", local).strip()
        if local:
            return local.title()

    return "Unknown"


# ──────────────────────────────────────────────
# Helper: safe filename
# ──────────────────────────────────────────────


def safe_filename(name: str) -> str:
    """Remove or replace characters that are unsafe in file names."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("_. ")
    return name


def _clean_role_tag(role: str) -> str:
    """Produce a compact role tag for use in filenames.

    E.g. 'Assistant Professor -EEE' → 'AssistantProfessor_EEE'
         'Faculty Position in Mechanical Engineering Department'
             → 'FacultyPosition_MechanicalEngineering'
    """
    if not role:
        return "Unknown"
    # Remove noise words
    tag = re.sub(
        r"\b(the|of|for|in|and|a|an|post|position|dept|department)\b",
        "",
        role,
        flags=re.I,
    )
    # Collapse separators
    tag = re.sub(r"[\s_\-\u2013\u2014]+", " ", tag).strip()
    # Title-case each word and join with underscore
    words = [w.strip() for w in tag.split() if w.strip()]
    if not words:
        return safe_filename(role)
    return "_".join(w.title() for w in words)


def deduplicate_path(dest: Path) -> Path:
    """If *dest* already exists, append _1, _2, … before the extension."""
    if not dest.exists():
        return dest
    stem = dest.stem
    ext = dest.suffix
    parent = dest.parent
    counter = 1
    while True:
        new = parent / f"{stem}_{counter}{ext}"
        if not new.exists():
            return new
        counter += 1


# ──────────────────────────────────────────────
# Helper: get plain-text body
# ──────────────────────────────────────────────


def get_body_text(msg: Message) -> str:
    """Return the first text/plain part of a MIME message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


# ──────────────────────────────────────────────
# Subject filtering
# ──────────────────────────────────────────────


def passes_subject_filter(subject: str) -> bool:
    """Return True if the subject passes the keyword filter (or filter is off)."""
    if not ENABLE_SUBJECT_FILTER:
        return True
    if not ALLOWED_SUBJECT_KEYWORDS:
        return True
    subject_lower = subject.lower()
    return any(kw.lower() in subject_lower for kw in ALLOWED_SUBJECT_KEYWORDS)


# ──────────────────────────────────────────────
# Attachment classification: resume vs supporting docs
# ──────────────────────────────────────────────

# Filename patterns that indicate a CV/resume (case-insensitive)
_RESUME_PATTERNS = re.compile(
    r"(resume|cv|curriculum[\s_-]*vitae|biodata|application)", re.I
)
# Filename patterns that indicate a supporting document (not a resume)
_SUPPORT_DOC_PATTERNS = re.compile(
    r"(certificate|mark\s*sheet|grade|gate[\s_-]?\d|award|letter|"
    r"appointment|experience[\s_-]*(cert|letter)|transcript|"
    r"degree|diploma|scorecard|id[\s_-]*proof|passport|aadhaar|"
    r"pan[\s_-]*card|photo|signature|recommend)",
    re.I,
)


def _split_resume_and_docs(
    attachments: list[tuple[str, bytes]],
) -> tuple[Optional[tuple[str, bytes]], list[tuple[str, bytes]]]:
    """Identify the single resume/CV from a list of attachments.

    Returns (resume_or_none, other_attachments).

    Heuristic:
      1. If only one attachment → it's the resume.
      2. If a filename matches _RESUME_PATTERNS → that's the resume.
      3. If a filename matches _SUPPORT_DOC_PATTERNS → it's a supporting doc.
      4. If exactly one file is NOT a supporting doc → that's the resume.
      5. If ambiguous (multiple unknowns, or all look like supporting docs)
         → return None so the email goes to Unsorted for manual review.
    """
    if len(attachments) == 1:
        return attachments[0], []

    resume_candidates: list[tuple[str, bytes]] = []
    docs: list[tuple[str, bytes]] = []
    unknown: list[tuple[str, bytes]] = []

    for filename, data in attachments:
        name_lower = Path(filename).stem.lower()
        if _RESUME_PATTERNS.search(name_lower):
            resume_candidates.append((filename, data))
        elif _SUPPORT_DOC_PATTERNS.search(name_lower):
            docs.append((filename, data))
        else:
            unknown.append((filename, data))

    # Exactly one file explicitly looks like a resume
    if len(resume_candidates) == 1:
        others = docs + unknown + resume_candidates[1:]
        return resume_candidates[0], others

    # No explicit resume match — if exactly one unknown file, that's the CV
    if not resume_candidates and len(unknown) == 1:
        return unknown[0], docs

    # Multiple resume candidates — pick the largest by file size
    if len(resume_candidates) > 1:
        resume_candidates.sort(key=lambda x: len(x[1]), reverse=True)
        others = docs + unknown + resume_candidates[1:]
        return resume_candidates[0], others

    # Ambiguous: multiple unknowns, or zero unknowns + all supporting docs
    # → can't confidently identify the CV
    return None, attachments


# ──────────────────────────────────────────────
# Core: process a single email
# ──────────────────────────────────────────────


def process_email(
    msg: Message,
    uid: bytes,
    imap_conn: imaplib.IMAP4_SSL,
    dry_run: bool = False,
    counters: Optional[dict[str, int]] = None,
) -> None:
    """Download attachments, classify, save, and log."""
    if counters is None:
        counters = _new_counters()

    counters["emails_scanned"] += 1

    from_header = decode_header_value(msg.get("From", ""))
    subject = decode_header_value(msg.get("Subject", ""))
    body = get_body_text(msg)
    email_addr = extract_email_address(from_header)
    message_id = decode_header_value(msg.get("Message-ID", ""))

    logger.info("── Processing email ──")
    logger.info("  From   : %s", from_header)
    logger.info("  Subject: %s", subject)

    # ── Subject filter ──
    if not passes_subject_filter(subject):
        logger.info("  ⊘  Filtered out (subject does not match keywords).")
        counters["filtered_out"] += 1
        log_to_csv(
            from_header, email_addr, "", "", "(filtered)", "", "", "FILTERED_OUT"
        )
        return

    # ── Collect ALL attachments (valid + invalid) ──
    valid_attachments: list[tuple[str, bytes]] = []
    invalid_attachments: list[tuple[str, bytes]] = []

    for part in msg.walk():
        content_disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disp:
            continue
        raw_filename = part.get_filename()
        if not raw_filename:
            continue
        filename = decode_header_value(raw_filename)
        ext = Path(filename).suffix.lower()
        data = part.get_payload(decode=True)
        if not data:
            continue
        if ext in ACCEPTED_EXTENSIONS:
            valid_attachments.append((filename, data))
        else:
            invalid_attachments.append((filename, data))

    # ── Handle invalid format attachments ──
    for filename, data in invalid_attachments:
        logger.info("  ⚠  Invalid format skipped: %s", filename)
        counters["invalid_format"] += 1
        log_to_csv(
            from_header,
            email_addr,
            "",
            "",
            "(invalid format)",
            "",
            filename,
            "INVALID_FORMAT" if not dry_run else "DRY_RUN",
        )

    if not valid_attachments:
        if not invalid_attachments:
            logger.warning("  ⚠  No resume attachment found in this email.")
            detected_role = extract_role(subject, body)
            candidate_name = extract_candidate_name(from_header, subject, "")
            log_to_csv(
                from_header,
                email_addr,
                candidate_name,
                detected_role or "(none)",
                "(no attachment)",
                "",
                "",
                "NO_ATTACHMENT",
            )
        return

    # ── Multiple attachments: save all in a candidate-named subfolder ──
    if len(valid_attachments) > 1:
        try:
            result_status = _process_multiple_attachments(
                valid_attachments, from_header, email_addr,
                subject, body, dry_run, counters,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("  Error processing multiple attachments: %s", exc)
            counters["errors"] += 1
            log_error(from_header, ", ".join(f[0] for f in valid_attachments), str(exc))
            result_status = "ERROR"
    else:
        # ── Single attachment ──
        filename, data = valid_attachments[0]
        try:
            result_status = _process_single_attachment(
                filename, data, from_header, email_addr,
                subject, body, dry_run, counters,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("  Error processing attachment '%s': %s", filename, exc)
            counters["errors"] += 1
            log_error(from_header, filename, str(exc))
            log_to_csv(from_header, email_addr, "", "", "", "", filename, "ERROR")
            if not dry_run:
                try:
                    err_dest = deduplicate_path(ERRORS_DIR / filename)
                    err_dest.write_bytes(data)
                    logger.info("  Saved error file to: %s", err_dest)
                except Exception:  # noqa: BLE001
                    pass
            result_status = "ERROR"

    # ── Label the email (unless dry-run) ──
    if not dry_run:
        if result_status == "UNSORTED":
            _label_as_unsorted(imap_conn, uid)
        else:
            _move_to_processed(imap_conn, uid)


def _classify_and_get_folder(
    attachments: list[tuple[str, bytes]],
    from_header: str,
    subject: str,
    body: str,
) -> tuple[str, str, str]:
    """Classify role and return (detected_role, folder_rel, candidate_name).

    For multi-attachment emails, tries the resume-like attachment first for
    content-based AI classification.
    """
    first_filename = attachments[0][0] if attachments else ""
    detected_role = extract_role(subject, body, first_filename)
    folder_rel = classify_role(detected_role)

    # If Unsorted, try resume-content AI on the first resume-like attachment
    if ENABLE_RESUME_AI and folder_rel == f"{YEAR}/Unsorted":
        for fname, fdata in attachments:
            ext = Path(fname).suffix.lower()
            if ext in ACCEPTED_EXTENSIONS:
                try:
                    from resume_reader import detect_department_from_content, extract_text
                    resume_text = extract_text(fdata, ext)
                    ai_folder = detect_department_from_content(resume_text)
                    if ai_folder:
                        folder_rel = ai_folder
                        logger.info("  Resume content override: %s", folder_rel)
                        if ENABLE_AUTO_LEARNING and detected_role:
                            add_learned_mapping(detected_role, folder_rel)
                        break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("  Resume AI reading failed: %s", exc)

    candidate_name = extract_candidate_name(from_header, subject, first_filename)
    return detected_role, folder_rel, candidate_name


def _extract_profile_name(from_header: str) -> str:
    """Extract the display name from the email From header (profile name)."""
    display, addr = email.utils.parseaddr(from_header)
    if display and not re.match(r"^[\w.+-]+@", display):
        return display.strip()
    # Fallback to email local part
    if addr:
        local = addr.split("@")[0]
        local = re.sub(r"[\d_.\-]+", " ", local).strip()
        if local:
            return local.title()
    return "Unknown"


def _process_multiple_attachments(
    attachments: list[tuple[str, bytes]],
    from_header: str,
    email_addr: str,
    subject: str,
    body: str,
    dry_run: bool,
    counters: dict[str, int],
) -> str:
    """Process multiple attachments: classify, create a candidate subfolder, save all.

    Returns 'UNSORTED' if classified as unsorted (no download), else 'SAVED'/'DRY_RUN'.
    """
    detected_role, folder_rel, _ = _classify_and_get_folder(
        attachments, from_header, subject, body,
    )
    # Use the email profile name for the folder
    profile_name = _extract_profile_name(from_header)
    logger.info("  Detected role: %s", detected_role or "(none)")
    logger.info("  Classification: %s", folder_rel)
    logger.info("  Applicant (profile): %s", profile_name)

    # If Unsorted, don't download — label for manual review
    if folder_rel.endswith("/Unsorted"):
        counters["unsorted_resumes"] += 1
        logger.info("  ⊘  Unsorted — skipping download, will label for manual review.")
        log_to_csv(
            from_header, email_addr, profile_name,
            detected_role or "(none)", folder_rel, "",
            "; ".join(f[0] for f in attachments), "UNSORTED",
        )
        return "UNSORTED"

    # Create a subfolder named after the email profile name
    candidate_folder_name = safe_filename(profile_name) or "Unknown"
    dest_dir = BASE_DIR / folder_rel / candidate_folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for filename, data in attachments:
        # Duplicate check per file
        if is_duplicate(data, filename, from_header):
            counters["duplicates_skipped"] += 1
            logger.info("  ⊘  Duplicate skipped: %s", filename)
            continue

        dest_path = deduplicate_path(dest_dir / filename)

        if dry_run:
            dept_label = folder_rel.replace(f"{YEAR}/", "")
            logger.info(
                "  [DRY-RUN] Would save: %s → %s/%s",
                filename, dept_label, candidate_folder_name,
            )
        else:
            dest_path.write_bytes(data)
            logger.info("  ✓  Saved: %s", dest_path.name)
            saved_count += 1

    if not dry_run:
        counters["resumes_downloaded"] += saved_count

    status = "SAVED" if not dry_run else "DRY_RUN"
    log_to_csv(
        from_header, email_addr, profile_name,
        detected_role or "(none)", folder_rel, str(dest_dir),
        "; ".join(f[0] for f in attachments), status,
    )
    logger.info(
        "  Saved %d attachment(s) to: %s/%s",
        saved_count if not dry_run else len(attachments),
        folder_rel, candidate_folder_name,
    )
    return status


def _process_single_attachment(
    filename: str,
    data: bytes,
    from_header: str,
    email_addr: str,
    subject: str,
    body: str,
    dry_run: bool,
    counters: dict[str, int],
) -> str:
    """Process a single valid attachment: classify, deduplicate, save.

    Returns 'UNSORTED' if classified as unsorted (no download), else 'SAVED'/'DRY_RUN'/'DUPLICATE'.
    """

    # ── Duplicate check ──
    if is_duplicate(data, filename, from_header):
        counters["duplicates_skipped"] += 1
        log_to_csv(
            from_header, email_addr, "", "", "(duplicate)", "", filename, "DUPLICATE"
        )
        return "DUPLICATE"

    # 1. Extract role
    detected_role = extract_role(subject, body, filename)
    logger.info("  Detected role: %s", detected_role or "(none)")

    # 2. Classify
    folder_rel = classify_role(detected_role)

    # 3. If classified as Unsorted and resume AI is enabled, try content-based
    if ENABLE_RESUME_AI and folder_rel == f"{YEAR}/Unsorted":
        try:
            from resume_reader import detect_department_from_content, extract_text

            ext = Path(filename).suffix.lower()
            resume_text = extract_text(data, ext)
            ai_folder = detect_department_from_content(resume_text)
            if ai_folder:
                folder_rel = ai_folder
                logger.info("  Resume content override: %s", folder_rel)
                # Learn this mapping for the future
                if ENABLE_AUTO_LEARNING and detected_role:
                    add_learned_mapping(detected_role, folder_rel)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  Resume AI reading failed: %s", exc)

    logger.info("  Classification: %s", folder_rel)

    # If Unsorted, don't download — label for manual review
    if folder_rel.endswith("/Unsorted"):
        counters["unsorted_resumes"] += 1
        candidate_name = extract_candidate_name(from_header, subject, filename)
        logger.info("  ⊘  Unsorted — skipping download, will label for manual review.")
        log_to_csv(
            from_header, email_addr, candidate_name,
            detected_role or "(none)", folder_rel, "",
            filename, "UNSORTED",
        )
        return "UNSORTED"

    # 3. Candidate name
    candidate_name = extract_candidate_name(from_header, subject, filename)
    logger.info("  Candidate: %s", candidate_name)

    # 4. Build destination path — CandidateName_Role.ext
    role_tag = _clean_role_tag(detected_role) if detected_role else "Unknown"
    ext = Path(filename).suffix
    new_name = f"{safe_filename(candidate_name)}_{role_tag}{ext}"
    dest_dir = BASE_DIR / folder_rel
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = deduplicate_path(dest_dir / new_name)

    if dry_run:
        dept_label = folder_rel.replace(f"{YEAR}/", "")
        logger.info("  [DRY-RUN] Would save: %s → %s", candidate_name, dept_label)
        log_to_csv(
            from_header, email_addr, candidate_name,
            detected_role or "(none)", folder_rel,
            str(dest_path), filename, "DRY_RUN",
        )
        return "DRY_RUN"
    else:
        dest_path.write_bytes(data)
        logger.info("  ✓  Saved: %s", dest_path.name)
        counters["resumes_downloaded"] += 1
        log_to_csv(
            from_header, email_addr, candidate_name,
            detected_role or "(none)", folder_rel,
            str(dest_path), filename, "SAVED",
        )
        return "SAVED"


# ──────────────────────────────────────────────
# Gmail label management
# ──────────────────────────────────────────────


def _ensure_label(imap_conn: imaplib.IMAP4_SSL, label: str) -> None:
    """Create a Gmail label if it doesn't already exist."""
    try:
        status, data = imap_conn.list()
        if status == "OK" and data:
            existing = []
            for item in data:
                if isinstance(item, bytes):
                    existing.append(item.decode("utf-8", errors="replace"))
            label_exists = any(
                f'"{label}"' in entry or f" {label}" in entry for entry in existing
            )
            if not label_exists:
                imap_conn.create(label)
                logger.info("Created Gmail label: %s", label)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not check/create label '%s': %s", label, exc)


def _has_processed_label(imap_conn: imaplib.IMAP4_SSL, uid: bytes) -> bool:
    """Check if the email already has the processed or unsorted label."""
    try:
        status, data = imap_conn.uid("FETCH", uid, "(X-GM-LABELS)")
        if status == "OK" and data and data[0]:
            raw = data[0]
            if isinstance(raw, tuple):
                raw = raw[1]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            labels_str = str(raw)
            return PROCESSED_LABEL in labels_str or UNSORTED_LABEL in labels_str
    except Exception:  # noqa: BLE001
        pass
    return False


def _apply_gmail_label(imap_conn: imaplib.IMAP4_SSL, uid: bytes, label: str) -> None:
    """Add a Gmail label via X-GM-LABELS."""
    try:
        _ensure_label(imap_conn, label)
        label_str = f'"{label}"'
        result = imap_conn.uid("STORE", uid, "+X-GM-LABELS", label_str)
        if result[0] == "OK":
            logger.info("  ✓ Labelled: %s", label)
        else:
            imap_conn.uid("COPY", uid, label_str)
            logger.info("  ✓ Labelled (copy): %s", label)
    except Exception as exc:  # noqa: BLE001
        logger.warning("  Could not label email '%s': %s", label, exc)


UNSORTED_LABEL = "Unsorted"


def _move_to_processed(imap_conn: imaplib.IMAP4_SSL, uid: bytes) -> None:
    """Add the configured processed label via Gmail X-GM-LABELS."""
    _apply_gmail_label(imap_conn, uid, PROCESSED_LABEL)


def _label_as_unsorted(imap_conn: imaplib.IMAP4_SSL, uid: bytes) -> None:
    """Label the email as 'Unsorted' for manual review."""
    _apply_gmail_label(imap_conn, uid, UNSORTED_LABEL)


# ──────────────────────────────────────────────
# Auto-learning: detect manual moves
# ──────────────────────────────────────────────


def learn_from_manual_moves() -> int:
    """
    Scan processing_log.csv for entries that were saved to Unsorted.
    If the file no longer exists in Unsorted but exists in a department
    folder, learn the mapping.

    Returns the number of new mappings learned.
    """
    if not ENABLE_AUTO_LEARNING:
        return 0
    if not LOG_FILE.exists():
        return 0

    learned = 0
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row.get("status", "")
            if status != "SAVED":
                continue
            dest = row.get("destination_folder", "")
            if "Unsorted" not in dest:
                continue
            role = row.get("detected_role", "")
            if not role or role == "(none)":
                continue
            filename = row.get("filename", "")
            if not filename:
                continue

            # Check if the file was moved out of Unsorted
            unsorted_file = Path(dest)
            if unsorted_file.exists():
                continue  # Still in Unsorted

            # Search for the file in department folders
            for dept, subs in FOLDER_STRUCTURE.items():
                folders_to_check = (
                    [BASE_DIR / YEAR / dept]
                    if not subs
                    else [BASE_DIR / YEAR / dept / sub for sub in subs]
                )
                for folder in folders_to_check:
                    candidate = folder / unsorted_file.name
                    if candidate.exists():
                        new_folder = (
                            f"{YEAR}/{dept}"
                            if not subs
                            else f"{YEAR}/{dept}/{folder.name}"
                        )
                        add_learned_mapping(role, new_folder)
                        learned += 1
                        break

    if learned:
        logger.info("Learned %d new mapping(s) from manual moves.", learned)
    return learned


# ──────────────────────────────────────────────
# Department statistics generator
# ──────────────────────────────────────────────


def generate_statistics() -> None:
    """Read processing_log.csv and generate department_report.csv."""
    if not LOG_FILE.exists():
        logger.warning("No processing log found at %s", LOG_FILE)
        return

    dept_counts: Counter = Counter()

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "SAVED":
                continue
            classification = row.get("classification_result", "")
            if not classification:
                continue
            # Extract department from path like "2026/CSE/Faculty"
            parts = classification.split("/")
            if len(parts) >= 2:
                dept = parts[1]  # Department name
                dept_counts[dept] += 1

    # Write report
    with open(DEPARTMENT_REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Department", "Applications"])
        for dept, count in dept_counts.most_common():
            writer.writerow([dept, count])

    # Print report
    print("\n" + "═" * 40)
    print("      DEPARTMENT STATISTICS")
    print("═" * 40)
    print(f"  {'Department':<30} {'Applications':>10}")
    print("  " + "─" * 42)
    for dept, count in dept_counts.most_common():
        print(f"  {dept:<30} {count:>10}")
    print("═" * 40)
    print(f"\n  Report saved to: {DEPARTMENT_REPORT_FILE}\n")


# ──────────────────────────────────────────────
# Main driver
# ──────────────────────────────────────────────


def connect_gmail() -> imaplib.IMAP4_SSL:
    """Establish an authenticated IMAP connection to Gmail."""
    logger.info("Connecting to Gmail IMAP …")
    conn = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    conn.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    logger.info("Authenticated as %s", GMAIL_ADDRESS)
    return conn


def run(mailbox: str, dry_run: bool = False) -> dict[str, int]:
    """Fetch and process all emails in *mailbox*. Returns counters."""
    create_folder_tree()
    _init_csv_log()
    _init_multi_attachment_review_log()

    counters = _new_counters()

    # Auto-learning: detect manual moves on startup
    if ENABLE_AUTO_LEARNING:
        learn_from_manual_moves()

    conn = connect_gmail()

    try:
        status, _ = conn.select(f'"{mailbox}"' if " " in mailbox else mailbox)
        if status != "OK":
            logger.error("Could not open mailbox '%s'.", mailbox)
        else:
            # Search ALL emails (read + unread)
            status, msg_ids = conn.uid("SEARCH", None, "ALL")
            if status != "OK" or not msg_ids or not msg_ids[0]:
                logger.info("No emails found in '%s'.", mailbox)
            else:
                uids = msg_ids[0].split()
                logger.info("Found %d email(s) in '%s'.", len(uids), mailbox)

                for uid in uids:
                    if _shutdown:
                        logger.info("Shutting down gracefully…")
                        break

                    # Skip emails already labelled as processed
                    if _has_processed_label(conn, uid):
                        continue

                    status, raw = conn.uid("FETCH", uid, "(RFC822)")
                    if status != "OK" or not raw or not raw[0]:
                        logger.warning("Failed to fetch UID %s", uid)
                        continue

                    raw_email = raw[0]
                    if isinstance(raw_email, tuple):
                        raw_bytes = raw_email[1]
                    else:
                        raw_bytes = raw_email  # type: ignore[assignment]

                    msg = email.message_from_bytes(raw_bytes)
                    try:
                        process_email(
                            msg, uid, conn, dry_run=dry_run, counters=counters
                        )
                    except Exception:
                        logger.exception("Error processing UID %s", uid)
                        counters["errors"] += 1
                        continue

                    time.sleep(0.3)

    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass
        logger.info("Disconnected from Gmail.")

        # Print summary (always, even on early exit)
        print_summary(counters)

        # Generate statistics if enabled
        if ENABLE_STATISTICS and not dry_run:
            generate_statistics()

    logger.info("Done.  CSV log at %s", LOG_FILE)
    return counters


def run_realtime(mailbox: str, dry_run: bool = False) -> None:
    """
    Real-time mode: process existing emails, then poll for new ones.

    Polls every ``REALTIME_POLL_INTERVAL`` seconds for new UNSEEN emails.
    Press Ctrl+C to stop.
    """
    global _shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info(
        "Starting real-time monitoring (poll every %ds)…", REALTIME_POLL_INTERVAL
    )
    logger.info("Press Ctrl+C to stop.\n")

    create_folder_tree()
    _init_csv_log()
    _init_multi_attachment_review_log()

    total_counters = _new_counters()

    while not _shutdown:
        batch_counters = _new_counters()

        try:
            conn = connect_gmail()
            status, _ = conn.select(f'"{mailbox}"' if " " in mailbox else mailbox)
            if status != "OK":
                logger.error("Could not open mailbox '%s'.", mailbox)
                time.sleep(REALTIME_POLL_INTERVAL)
                continue

            # Search for UNSEEN (unread) emails
            status, msg_ids = conn.uid("SEARCH", None, "UNSEEN")
            if status != "OK" or not msg_ids or not msg_ids[0]:
                logger.info("No new emails. Waiting %ds…", REALTIME_POLL_INTERVAL)
            else:
                uids = msg_ids[0].split()
                logger.info("Found %d new email(s).", len(uids))

                for uid in uids:
                    if _shutdown:
                        break
                    # Skip emails already labelled as processed
                    if _has_processed_label(conn, uid):
                        continue
                    status, raw = conn.uid("FETCH", uid, "(RFC822)")
                    if status != "OK" or not raw or not raw[0]:
                        continue
                    raw_email = raw[0]
                    if isinstance(raw_email, tuple):
                        raw_bytes = raw_email[1]
                    else:
                        raw_bytes = raw_email  # type: ignore[assignment]

                    msg = email.message_from_bytes(raw_bytes)
                    try:
                        process_email(
                            msg, uid, conn, dry_run=dry_run, counters=batch_counters
                        )
                    except Exception:
                        logger.exception("Error processing UID %s", uid)
                        batch_counters["errors"] += 1

                    time.sleep(0.3)

            try:
                conn.close()
            except Exception:
                pass
            conn.logout()

        except Exception as exc:  # noqa: BLE001
            logger.error("Connection error: %s. Retrying…", exc)

        # Accumulate
        for key in total_counters:
            total_counters[key] += batch_counters[key]

        if batch_counters["emails_scanned"] > 0:
            print_summary(batch_counters)

        if not _shutdown:
            time.sleep(REALTIME_POLL_INTERVAL)

    # Final summary
    print("\n── Final totals ──")
    print_summary(total_counters)

    if ENABLE_STATISTICS:
        generate_statistics()


# ──────────────────────────────────────────────
# Clear / Reset
# ──────────────────────────────────────────────


def clear_all() -> None:
    """Delete all processed resumes, logs, hashes, and learning DB.

    Asks for interactive confirmation before proceeding.
    """
    print("\n⚠  This will delete ALL processed resumes and reset logs.")
    print("   Files in the following locations will be removed:")
    print(f"     • {BASE_DIR / YEAR}/  (all department folders)")
    print(f"     • {UNSORTED_DIR}/")
    print(f"     • {INVALID_DIR}/")
    print(f"     • {ERRORS_DIR}/")
    print(f"     • {LOG_FILE}")
    print(f"     • {HASHES_FILE}")
    print(f"     • {LEARNING_DB_FILE}")
    print(f"     • {DEPARTMENT_REPORT_FILE}")
    answer = input("\n  Are you sure? [y/N] ").strip().lower()
    if answer != "y":
        print("  Cancelled.")
        return

    deleted = 0

    # Delete resume files from all department folders
    year_dir = BASE_DIR / YEAR
    if year_dir.exists():
        for f in year_dir.rglob("*"):
            if f.is_file():
                f.unlink()
                deleted += 1

    # Delete files in Invalid_Resumes and Errors
    for folder in [INVALID_DIR, ERRORS_DIR]:
        if folder.exists():
            for f in folder.iterdir():
                if f.is_file():
                    f.unlink()
                    deleted += 1

    # Reset CSV log (keep header only)
    if LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        import csv as _csv

        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = _csv.writer(f)
            writer.writerow(_CSV_HEADERS)
        deleted += 1

    # Reset hashes
    if HASHES_FILE.exists():
        with open(HASHES_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        deleted += 1

    # Reset learning DB
    if LEARNING_DB_FILE.exists():
        with open(LEARNING_DB_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        deleted += 1

    # Reset department report
    if DEPARTMENT_REPORT_FILE.exists():
        DEPARTMENT_REPORT_FILE.unlink()
        deleted += 1

    print(f"\n  ✓ Cleared {deleted} file(s). Ready for a fresh run.")
    print(
        "  Tip: use  --undo-labels  to also remove Gmail 'Processed Applications' "
        "labels so emails are re-processed.\n"
    )


def undo_labels(mailbox: str) -> None:
    """Remove the 'Processed Applications' label from all emails in the mailbox.

    This allows previously processed emails to be re-processed on the next run.
    """
    conn = connect_gmail()
    removed = 0
    try:
        status, _ = conn.select(f'"{mailbox}"' if " " in mailbox else mailbox)
        if status != "OK":
            logger.error("Could not open mailbox '%s'.", mailbox)
            return

        status, msg_ids = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not msg_ids or not msg_ids[0]:
            logger.info("No emails found in '%s'.", mailbox)
            return

        uids = msg_ids[0].split()
        label_str = f'"{PROCESSED_LABEL}"'
        for uid in uids:
            if _has_processed_label(conn, uid):
                try:
                    conn.uid("STORE", uid, "-X-GM-LABELS", label_str)
                    removed += 1
                except Exception:  # noqa: BLE001
                    pass

    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass

    print(f"\n  ✓ Removed '{PROCESSED_LABEL}' label from {removed} email(s).")
    print("  Emails will be re-processed on the next run.\n")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process Gmail application emails and sort resumes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without downloading or moving emails.",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Gmail label / mailbox to read from (overrides config).",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Real-time mode: keep polling for new emails.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Generate department statistics report and exit.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all processed resumes and reset logs/hashes.",
    )
    parser.add_argument(
        "--undo-labels",
        action="store_true",
        help="Remove 'Processed Applications' label from all emails.",
    )
    args = parser.parse_args()

    mailbox = args.label or MAILBOX

    # Clear mode
    if args.clear:
        clear_all()
        if not args.undo_labels:
            return

    # Undo labels mode
    if args.undo_labels:
        undo_labels(mailbox)
        return

    # Stats-only mode
    if args.stats:
        generate_statistics()
        return

    if (
        GMAIL_ADDRESS == "your_email@gmail.com"
        or GMAIL_APP_PASSWORD == "your_app_password"
    ):
        logger.error(
            "Gmail credentials not configured.  "
            "Set gmail_email and app_password in config.json or "
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env."
        )
        sys.exit(1)

    # Real-time mode (CLI flag or config toggle)
    if args.realtime or ENABLE_REAL_TIME:
        run_realtime(mailbox=mailbox, dry_run=args.dry_run)
    else:
        run(mailbox=mailbox, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
