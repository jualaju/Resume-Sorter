#!/usr/bin/env python3
"""
resume_sorter.py
================
Connects to Gmail via IMAP, reads application emails, detects the applied
role, downloads resume attachments, classifies them into the correct
organisational folder, and logs every action.

Usage
-----
    python resume_sorter.py              # process all emails in configured mailbox
    python resume_sorter.py --dry-run    # preview without downloading / moving
    python resume_sorter.py --label Apps  # read from a custom Gmail label
"""

from __future__ import annotations

import argparse
import csv
import email
import email.header
import email.utils
import imaplib
import logging
import os
import re
import sys
import time
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Optional

from config import (
    ACCEPTED_EXTENSIONS,
    BASE_DIR,
    FOLDER_STRUCTURE,
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    IMAP_PORT,
    IMAP_SERVER,
    LOG_FILE,
    MAILBOX,
    PROCESSED_LABEL,
    UNSORTED_DIR,
    YEAR,
)
from classifier import classify_role, extract_role

# ──────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("resume_sorter")


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
    # Always ensure Unsorted exists
    UNSORTED_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Folder tree verified under %s", BASE_DIR)


# ──────────────────────────────────────────────
# CSV log helper
# ──────────────────────────────────────────────


def _init_csv_log() -> None:
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "email_from",
                "candidate_name",
                "detected_role",
                "classification_result",
                "destination_folder",
                "filename",
                "status",
            ])


def log_to_csv(
    email_from: str,
    candidate_name: str,
    detected_role: str,
    classification: str,
    dest_folder: str,
    filename: str,
    status: str,
) -> None:
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            email_from,
            candidate_name,
            detected_role,
            classification,
            dest_folder,
            filename,
            status,
        ])


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
# Helper: extract candidate name
# ──────────────────────────────────────────────


def extract_candidate_name(from_header: str, subject: str, filename: str) -> str:
    """
    Best-effort extraction of the candidate's name.

    Priority: From header display name → subject heuristics → filename.
    """
    # 1. Display name from From header  e.g.  "John Doe <john@…>"
    display, _ = email.utils.parseaddr(from_header)
    if display and not re.match(r"^[\w.+-]+@", display):
        return display.strip()

    # 2. Filename without extension, split on underscores/dashes
    if filename:
        name_part = re.sub(r"\.(pdf|docx?)$", "", filename, flags=re.I)
        name_part = re.sub(r"[_\-]+", " ", name_part).strip()
        # Remove role-ish tokens
        name_part = re.sub(
            r"(resume|cv|application|cover\s*letter)", "", name_part, flags=re.I
        ).strip()
        if name_part and len(name_part) > 2:
            return name_part.title()

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
# Core: process a single email
# ──────────────────────────────────────────────


def process_email(
    msg: Message,
    uid: bytes,
    imap_conn: imaplib.IMAP4_SSL,
    dry_run: bool = False,
) -> None:
    """Download attachments, classify, save, and log."""
    from_header = decode_header_value(msg.get("From", ""))
    subject = decode_header_value(msg.get("Subject", ""))
    body = get_body_text(msg)

    logger.info("── Processing email ──")
    logger.info("  From   : %s", from_header)
    logger.info("  Subject: %s", subject)

    # Collect valid attachments
    attachments: list[tuple[str, bytes]] = []
    for part in msg.walk():
        content_disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disp:
            continue
        raw_filename = part.get_filename()
        if not raw_filename:
            continue
        filename = decode_header_value(raw_filename)
        ext = Path(filename).suffix.lower()
        if ext not in ACCEPTED_EXTENSIONS:
            logger.info("  Skipping unsupported attachment: %s", filename)
            continue
        data = part.get_payload(decode=True)
        if data:
            attachments.append((filename, data))

    if not attachments:
        logger.warning("  ⚠  No resume attachment found in this email.")
        detected_role = extract_role(subject, body)
        candidate_name = extract_candidate_name(from_header, subject, "")
        log_to_csv(
            from_header,
            candidate_name,
            detected_role or "(none)",
            "(no attachment)",
            "",
            "",
            "NO_ATTACHMENT",
        )
        return

    for filename, data in attachments:
        # 1. Extract role
        detected_role = extract_role(subject, body, filename)
        logger.info("  Detected role: %s", detected_role or "(none)")

        # 2. Classify
        folder_rel = classify_role(detected_role)
        logger.info("  Classification: %s", folder_rel)

        # 3. Candidate name
        candidate_name = extract_candidate_name(from_header, subject, filename)
        logger.info("  Candidate: %s", candidate_name)

        # 4. Build destination path
        role_tag = safe_filename(detected_role) if detected_role else "Unknown"
        ext = Path(filename).suffix
        new_name = f"{safe_filename(candidate_name)}_{role_tag}{ext}"
        dest_dir = BASE_DIR / folder_rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = deduplicate_path(dest_dir / new_name)

        logger.info("  Destination: %s", dest_path)

        if not dry_run:
            dest_path.write_bytes(data)
            logger.info("  ✓  Saved: %s", dest_path.name)
        else:
            logger.info("  [DRY-RUN] Would save to: %s", dest_path)

        log_to_csv(
            from_header,
            candidate_name,
            detected_role or "(none)",
            folder_rel,
            str(dest_path),
            filename,
            "SAVED" if not dry_run else "DRY_RUN",
        )

    # 5. Move email to processed label (unless dry-run)
    if not dry_run:
        _move_to_processed(imap_conn, uid)


# ──────────────────────────────────────────────
# Gmail label management
# ──────────────────────────────────────────────


def _ensure_label(imap_conn: imaplib.IMAP4_SSL, label: str) -> None:
    """Create a Gmail label if it doesn't already exist."""
    try:
        # List existing labels
        status, data = imap_conn.list()
        if status == "OK" and data:
            existing = []
            for item in data:
                if isinstance(item, bytes):
                    existing.append(item.decode("utf-8", errors="replace"))
            # Check if label already exists
            label_exists = any(f'"{label}"' in entry or f" {label}" in entry for entry in existing)
            if not label_exists:
                imap_conn.create(label)
                logger.info("Created Gmail label: %s", label)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not check/create label '%s': %s", label, exc)


def _move_to_processed(imap_conn: imaplib.IMAP4_SSL, uid: bytes) -> None:
    """Add the 'Processed Applications' label and remove from INBOX."""
    try:
        _ensure_label(imap_conn, PROCESSED_LABEL)
        # Gmail uses label names in double quotes for COPY
        label = f'"{PROCESSED_LABEL}"'
        imap_conn.uid("COPY", uid, label)
        # Mark as deleted in current mailbox then expunge
        imap_conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        imap_conn.expunge()
        logger.info("  Moved to label: %s", PROCESSED_LABEL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("  Could not move email to '%s': %s", PROCESSED_LABEL, exc)


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


def run(mailbox: str, dry_run: bool = False) -> None:
    """Fetch and process all emails in *mailbox*."""
    create_folder_tree()
    _init_csv_log()

    conn = connect_gmail()

    try:
        status, _ = conn.select(f'"{mailbox}"' if " " in mailbox else mailbox)
        if status != "OK":
            logger.error("Could not open mailbox '%s'.", mailbox)
            return

        # Search ALL emails (read + unread)
        status, msg_ids = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not msg_ids or not msg_ids[0]:
            logger.info("No emails found in '%s'.", mailbox)
            return

        uids = msg_ids[0].split()
        logger.info("Found %d email(s) in '%s'.", len(uids), mailbox)

        for uid in uids:
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
                process_email(msg, uid, conn, dry_run=dry_run)
            except Exception:
                logger.exception("Error processing UID %s", uid)
                continue

            # Be polite to Gmail rate limits
            time.sleep(0.3)

    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()
        logger.info("Disconnected from Gmail.")

    logger.info("Done.  CSV log at %s", LOG_FILE)


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
    args = parser.parse_args()

    mailbox = args.label or MAILBOX

    if GMAIL_ADDRESS == "your_email@gmail.com" or GMAIL_APP_PASSWORD == "your_app_password":
        logger.error(
            "Gmail credentials not configured.  "
            "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env or config.py."
        )
        sys.exit(1)

    run(mailbox=mailbox, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
