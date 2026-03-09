# Resume Sorter – Gmail Application Email Processor

An automated Python tool that reads job application emails from Gmail, detects the applied role, downloads resume attachments, and organises them into a predefined departmental folder structure. Includes AI-powered classification via OpenAI for ambiguous roles.

---

## Features

| Feature | Description |
|---|---|
| **Gmail IMAP** | Secure connection via App Password |
| **Role detection** | Regex extraction from subject / body / filename |
| **Keyword classifier** | Instant, zero-cost mapping for common roles |
| **AI classifier** | OpenAI GPT fallback for ambiguous roles |
| **Resume download** | Supports PDF, DOC, DOCX |
| **Smart naming** | `CandidateName_Role.pdf` with deduplication |
| **CSV log** | Timestamped log of every processed email |
| **Processed label** | Moves handled emails to *Processed Applications* |
| **Dry-run mode** | Preview without downloading or moving |

---

## Quick Start

### 1. Clone & install

```bash
cd sheetal_is_lazy
pip install -r requirements.txt
```

### 2. Set up Gmail App Password

> **You must use a Google App Password** (regular passwords won't work with IMAP).

1. Go to <https://myaccount.google.com/security>.
2. Enable **2-Step Verification** if not already on.
3. Go to <https://myaccount.google.com/apppasswords>.
4. Create a new App Password for **Mail** → **Other (custom name)** → e.g. "Resume Sorter".
5. Copy the 16-character password.

### 3. Enable IMAP in Gmail

1. Open Gmail → ⚙ Settings → **See all settings** → **Forwarding and POP/IMAP** tab.
2. Under *IMAP access*, select **Enable IMAP**.
3. Save changes.

### 4. Configure credentials

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```ini
GMAIL_ADDRESS=your_email@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
MAILBOX=INBOX
```

### 5. (Optional) OpenAI API key

For AI-based classification of ambiguous roles, add your key:

```ini
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

If omitted, the tool falls back to keyword matching only. Unmatched roles go to `Resumes/2026/Unsorted/`.

### 6. Run

```bash
python resume_sorter.py
```

Options:

```
python resume_sorter.py --dry-run        # preview, don't download or move
python resume_sorter.py --label Apps     # read from custom Gmail label
```

---

## Folder Structure

The tool automatically creates this tree under `Resumes/`:

```
2026/
├── ASH/
│   ├── Chemistry/
│   ├── Economics/
│   ├── English/
│   ├── Maths/
│   ├── Mechanical/
│   └── Physics/
├── BM/
│   ├── Faculty/
│   └── Lab/
├── BT/
│   ├── Faculty/
│   └── Lab/
├── CE/
│   ├── Faculty/
│   └── Lab/
├── CSE/
│   ├── Faculty/
│   └── Lab/
├── Dean/
│   ├── Administration/
│   ├── Consultancy & Industry Connect/
│   ├── Dean PG/
│   ├── Dean UG/
│   ├── Placement & Career Guidance/
│   ├── Planning/
│   ├── Research/
│   └── Student Affairs/
├── ECE/
│   ├── Faculty/
│   └── Lab/
├── EEE/
│   ├── Faculty/
│   └── Lab/
├── Librarian/
├── Physical Education/
└── Unsorted/
```

---

## How Classification Works

```
Email arrives
  │
  ├── Extract role from subject / body / filename
  │
  ├── 1. Keyword match (KEYWORD_ROLE_MAP in config.py)
  │      Hit? → use that folder
  │
  ├── 2. AI classification (OpenAI GPT)
  │      Confidence ≥ 0.5? → use AI result
  │
  └── 3. Fallback → 2026/Unsorted/
```

### Example classifications

| Input Role | Output Folder |
|---|---|
| Assistant Professor – CSE | `2026/CSE/Faculty/` |
| Lab Technician – ECE | `2026/ECE/Lab/` |
| Hostel Warden | `2026/Dean/Student Affairs/` |
| Placement Officer | `2026/Dean/Placement & Career Guidance/` |
| Library Assistant | `2026/Librarian/` |
| Research Associate | `2026/Dean/Research/` |

---

## CSV Log

Every run appends to `Resumes/processing_log.csv`:

| timestamp | email_from | candidate_name | detected_role | classification_result | destination_folder | filename | status |
|---|---|---|---|---|---|---|---|
| 2026-03-09T10:30:00 | john@gmail.com | John Doe | Assistant Professor – CSE | 2026/CSE/Faculty | .../John_Doe_Assistant_Professor_CSE.pdf | resume.pdf | SAVED |

---

## Configuration Reference

All settings live in `config.py` (or override via `.env`):

| Variable | Description | Default |
|---|---|---|
| `GMAIL_ADDRESS` | Gmail address | — |
| `GMAIL_APP_PASSWORD` | 16-char app password | — |
| `MAILBOX` | IMAP mailbox to read | `INBOX` |
| `PROCESSED_LABEL` | Label for processed emails | `Processed Applications` |
| `OPENAI_API_KEY` | OpenAI key (optional) | `""` |
| `OPENAI_MODEL` | Model name | `gpt-4o-mini` |
| `BASE_DIR` | Root folder for resume tree | `./Resumes` |
| `ACCEPTED_EXTENSIONS` | Allowed file types | `.pdf`, `.doc`, `.docx` |

### Adding custom role → folder mappings

Edit `KEYWORD_ROLE_MAP` in [config.py](config.py) to add instant keyword mappings:

```python
KEYWORD_ROLE_MAP["network engineer"] = f"{YEAR}/CSE/Faculty"
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `AUTHENTICATIONFAILED` | Double-check App Password; ensure IMAP is enabled |
| No emails found | Verify `MAILBOX` value; try `"INBOX"` |
| Role goes to Unsorted | Add a keyword mapping in `config.py` or enable OpenAI |
| `openai` import error | Run `pip install openai` |
| Permission denied on folders | Check write permissions on `BASE_DIR` |

---

## Project Files

```
sheetal_is_lazy/
├── resume_sorter.py    # main entry point
├── classifier.py       # role extraction + AI classification
├── config.py           # all configurable settings
├── requirements.txt    # Python dependencies
├── .env.example        # template for credentials
└── README.md           # this file
```

---

## License

Internal use. Modify freely.
