# JobConcierge — Python Automation Service

A Python FastAPI microservice that handles browser-based job application form submissions using Playwright. This is the automation execution layer of the **JobConcierge** polyglot system — it receives structured application payloads from the **NestJS orchestration service**, launches a real Chromium browser with bot-detection evasion, fills and submits ATS job application forms dynamically using LLM-mapped answers, and returns a structured result.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Tech Stack](#tech-stack)
4. [Prerequisites](#prerequisites)
5. [Project Structure](#project-structure)
6. [Environment Setup](#environment-setup)
7. [Running the Service](#running-the-service)
8. [API Endpoints](#api-endpoints)
9. [Bot Detection Evasion](#bot-detection-evasion)
10. [CAPTCHA Handling](#captcha-handling)
11. [Supported ATS Platforms](#supported-ats-platforms)
12. [Error Handling](#error-handling)
13. [Review Queue Triggers](#review-queue-triggers)
14. [Logging](#logging)
15. [Screenshots](#screenshots)
16. [Related Services](#related-services)

---

## Project Overview

The Automation Service is the Python execution layer of the JobConcierge system. It is responsible for:

- Receiving fully resolved application payloads from the NestJS service (including LLM-mapped answers for dynamic form questions)
- Launching a persistent Chromium browser context with stealth patches to evade bot detection
- Navigating to the target ATS job application URL
- Filling all form fields using human-like typing speed, scroll behaviour, and randomised delays
- Handling React Select dropdowns, native selects, boolean radio fields, and textarea inputs
- Downloading the candidate's resume from Supabase Storage and uploading it to the form
- Detecting and handling GDPR consent checkboxes
- Detecting email verification prompts post-submission (Greenhouse)
- Submitting the form and detecting success, validation errors, or CAPTCHA challenges
- Routing applications that require human intervention to the Review Queue via structured response codes
- Capturing screenshots at every major step for evidence and debugging

The service does **not** manage any database operations, LLM mapping, or business logic. All of that is handled by the NestJS orchestration service.

---

## Architecture

```
NestJS Orchestration Service (Port 3000)
        │
        │  HTTP POST /submit-application
        │  (ApplicationRequest payload with LLM-mapped answers + resume URL)
        ▼
FastAPI Application (Port 8000)
        │
        ▼
run_in_executor()  ← isolated event loop per request (Windows asyncio fix)
        │
        ▼
run_automation()  ← Playwright persistent context with stealth patches
        │
        ├── Stealth init script applied to browser context
        ├── Navigate to ATS URL
        ├── CAPTCHA detection (visibility + size check + badge exclusion)
        ├── Fill standard identity fields
        ├── Download resume from Supabase → temp file → upload to form
        ├── Dynamic question loop (LLM answers by field name)
        │     ├── input_text / textarea  → human_type()
        │     ├── select (React Select)  → click → wait → click option
        │     ├── select (native)        → select_option()
        │     └── boolean               → radio click with force
        ├── GDPR consent checkbox
        ├── Submit form
        ├── Detect: success / email verification / validation errors / CAPTCHA
        └── Return ApplicationResponse → NestJS
```

> **Note on Windows asyncio:** Playwright requires subprocess creation which conflicts with uvicorn's default event loop on Windows. The service uses a `ThreadPoolExecutor` to run each automation in a fresh isolated event loop per request.

> **Note on primary caller:** This service is designed to be called by the NestJS orchestration service. It can also be called directly from any HTTP client (Postman, cURL, other services) as long as the `ApplicationRequest` schema is satisfied.

---

## Tech Stack

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.11.x | Runtime |
| FastAPI | Latest | REST API framework |
| Uvicorn | Latest | ASGI server |
| Playwright | Latest | Browser automation |
| Pydantic | v2 | Request/response validation |
| httpx | Latest | Async HTTP client for resume download |
| certifi | Latest | SSL certificate bundle (Windows fix) |

---

## Prerequisites

- **Python 3.11** — specifically 3.11.x (3.12/3.13 have Playwright asyncio issues on Windows)
- **NestJS JobConcierge service** running on port 3000 (primary caller)
- **Supabase** — resume files must be accessible via public or signed URL

---

## Project Structure

```
automation/
│
├── scripts/
│   ├── main.py                ← FastAPI app, endpoints, Windows asyncio workaround
│   ├── automation.py          ← Browser launch, stealth patches, dispatch to handlers
│   ├── models.py              ← Pydantic request/response models
│   ├── browser_utils.py       ← Shared Playwright utilities (human_type, CAPTCHA detection, etc.)
│   ├── handlers/
│   │   ├── greenhouse.py      ← Greenhouse ATS form handler
│   │   ├── lever.py           ← Lever ATS form handler
│   │   └── generic.py         ← Generic/fallback handler
│   └── screenshots/           ← Auto-created, per-run screenshot evidence
│
├── venv/                      ← Python virtual environment (do not commit)
├── requirements.txt
└── .gitignore
```

---

## Environment Setup

### Step 1 — Install Python 3.11

Download from:
```
https://www.python.org/downloads/release/python-3119/
```

Check **Add Python to PATH** during installation. Verify:
```bash
python --version
# Must show Python 3.11.x
```

### Step 2 — Create virtual environment

```powershell
cd "C:\Users\rapheal\Documents\cyberpurview projects\automation"
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
```

If you get a script execution policy error:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Step 3 — Install dependencies

```powershell
pip install -r requirements.txt
```

### Step 4 — Install Playwright browsers

```powershell
playwright install chromium
```

### Step 5 — Verify setup

```powershell
python --version   # Python 3.11.x
uvicorn --version  # uvicorn, version x.x.x
```

---

## Running the Service

### Start

```powershell
cd "C:\Users\rapheal\Documents\cyberpurview projects\automation"
.\venv\Scripts\Activate.ps1
cd scripts
uvicorn main:app --reload --port 8000
```

### Verify

```bash
curl http://127.0.0.1:8000/health
```

Expected:
```json
{ "status": "Python automation service is running" }
```

### Stop

Press `Ctrl+C`. If old processes are blocking port 8000:
```powershell
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

---

## API Endpoints

Base URL: `http://127.0.0.1:8000`

### GET /health

```json
{ "status": "Python automation service is running" }
```

---

### POST /submit-application

Receives the full application payload from NestJS and runs automation.

**Request Body:**
```json
{
  "jobUrl": "https://boards.greenhouse.io/easyship/jobs/4396366006",
  "platform": "greenhouse",
  "firstName": "Rapheal",
  "lastName": "Miracle",
  "email": "miraclerapheal14@gmail.com",
  "phone": "+2349126146712",
  "linkedIn": "https://www.linkedin.com/in/rapheal-miracle",
  "address": "Bangalore",
  "resumeUrl": "https://your-supabase-url/storage/v1/object/public/resumes/resume.pdf",
  "cvFilePath": null,
  "headless": false,
  "salaryExpectation": "120000",
  "questions": [...],
  "llmAnswers": {
    "question_7871023006": "immediately",
    "question_7802645006": "120000",
    "question_5792560006": "Yes",
    "question_7802646006": "https://www.linkedin.com/in/rapheal-miracle",
    "question_8394481006": "Claude"
  },
  "savedAnswers": {}
}
```

**Request Fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| jobUrl | string | Yes | Full ATS job application URL |
| platform | string | Yes | `greenhouse`, `lever`, or `generic` |
| firstName | string | Yes | Candidate first name |
| lastName | string | Yes | Candidate last name |
| email | string | Yes | Candidate email |
| phone | string | No | Candidate phone number |
| linkedIn | string | No | LinkedIn profile URL |
| address | string | No | Candidate location |
| resumeUrl | string | No | Supabase Storage URL — downloaded to temp file and uploaded |
| cvFilePath | string | No | Local file path (dev/testing only) |
| headless | boolean | No | `false` = visible browser (default), `true` = headless |
| salaryExpectation | string/number | No | Salary for standard salary fields |
| questions | array | No | Raw ATS questions array from Greenhouse API |
| llmAnswers | object | No | Field name → answer map produced by Gemini/LLM mapping |
| savedAnswers | object | No | Previously cached answers |

**Success Response:**
```json
{
  "status": "SUBMITTED",
  "confirmation": "Application submitted for Rapheal Miracle — email verification pending",
  "screenshotUrl": "screenshots/gh_04_submitted.png"
}
```

**Review Required Response:**
```json
{
  "status": "REQUIRES_REVIEW",
  "requiresReview": true,
  "reviewReason": "CAPTCHA",
  "step": "PAGE_LOAD"
}
```

**Failure Response:**
```json
{
  "status": "FAILED",
  "error": "Form validation failed: This field is required.",
  "step": "SUBMIT"
}
```

---

## Bot Detection Evasion

The service uses a layered approach to avoid detection as an automated browser:

**1. Persistent browser context** (`launch_persistent_context`)
Maintains cookies, localStorage, and session data across runs so the browser looks like a returning human user rather than a fresh bot session each time.

**2. Custom stealth init script** (applied via `context.add_init_script`)
Injected into every page before any scripts run. Patches the key properties that bot detectors check:

| Property | Patch |
|---|---|
| `navigator.webdriver` | Set to `undefined` (default is `true` for automation) |
| `window.chrome` | Populated with realistic runtime object |
| `navigator.plugins` | Faked as non-empty array |
| `navigator.languages` | Set to `['en-US', 'en']` |
| `navigator.permissions` | Returns realistic notification permission state |
| `WebGL vendor/renderer` | Faked as `Intel Inc.` / `Intel Iris OpenGL Engine` |

**3. Launch flags**
```python
'--disable-blink-features=AutomationControlled'
'--no-sandbox'
'--disable-dev-shm-usage'
```

**4. Human-like interactions**
- `slow_mo=100` — all browser actions slowed by 100ms
- `human_type()` — character-by-character typing with 60–180ms random keystroke delays
- `scroll_to_element()` — smooth scroll before every interaction
- `human_delay()` — random waits between major actions

---

## CAPTCHA Handling

The service distinguishes between real CAPTCHA challenges and decorative/background scoring elements.

**What is NOT a CAPTCHA (ignored):**
- The `grecaptcha-badge` floating tab — a decorative element present on all Greenhouse forms, ~256px wide but positioned 186px off-screen. Hidden via JS before detection runs.
- reCAPTCHA v3/enterprise invisible scoring — runs silently in the background with no user interaction. Detected by `size=invisible` in the iframe URL.

**What IS a real CAPTCHA (triggers review):**
- `bframe` iframes — only appear when an active challenge popup is shown
- Visible `anchor` iframes wider than 100px that are not the badge

**Detection logic in `check_for_captcha()`:**
1. Hide `.grecaptcha-badge` via JS evaluation
2. Check for `bframe` iframes (challenge popup — always real)
3. Check anchor/widget iframes for visibility AND size > 100px

**When a real CAPTCHA is detected:**
- `headless=false` — waits 2 minutes for manual solve in the visible browser window
- `headless=true` — immediately returns `REQUIRES_REVIEW` with `reviewReason: CAPTCHA`

---

## Supported ATS Platforms

| Platform | Handler | Status |
|---|---|---|
| Greenhouse | `handlers/greenhouse.py` | ✅ Active |
| Lever | `handlers/lever.py` | 🔧 In progress |
| Generic | `handlers/generic.py` | 🔧 Fallback |

**Greenhouse-specific features:**
- Fetches job questions from `boards-api.greenhouse.io` public API
- Handles React Select dropdowns (detect by `role="combobox"`, click → wait → click option)
- Handles native `<select>` elements
- Handles boolean radio fields with `force=True` click (label overlay workaround)
- Detects and handles post-submission email verification prompt (8-digit code)
- GDPR consent checkbox detection and auto-check
- Resume downloaded from Supabase Storage via httpx with certifi SSL bundle

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Resume download fails | `FAILED` — `RESUME_UPLOAD` step |
| Required LLM answer missing | `REQUIRES_REVIEW` — `UNKNOWN_QUESTION` |
| Real CAPTCHA detected | `REQUIRES_REVIEW` — `CAPTCHA` |
| Page load timeout | `FAILED` — `TIMEOUT` step |
| Form validation errors | `FAILED` — lists error messages |
| Email verification prompt | `SUBMITTED` — treated as successful (application received) |
| Success page detected | `SUBMITTED` |
| Unexpected exception | `FAILED` — full error message and step |

---

## Review Queue Triggers

These conditions cause the service to return `REQUIRES_REVIEW` instead of `FAILED` or `SUBMITTED`. The NestJS service routes these to the human Review Queue:

| Trigger | reviewReason |
|---|---|
| Real CAPTCHA challenge detected | `CAPTCHA` |
| Required field has no LLM answer | `UNKNOWN_QUESTION` |
| Essay/open-ended question | `ESSAY_REQUIRED` |
| Manual approval needed | `APPROVAL_NEEDED` |
| Email verification timeout | `CAPTCHA_TIMEOUT` |

---

## Logging

All key events are logged to console with timestamps and log levels.

```
[INFO]  Application request received | platform=greenhouse | email=... | url=...
[INFO]  Automation starting | platform=greenhouse | ...
[INFO]  Routing to Greenhouse handler
[INFO]  Greenhouse handler | email | 14 questions | 8 LLM answers
[INFO]  Downloading resume from: https://...
[INFO]  Resume downloaded to temp: C:\...\tmp7jjz6cfj.pdf
[INFO]  React Select filled: #question_5792560006 → Yes
[INFO]  GDPR checked: #gdpr_consent_given_1
[INFO]  About to submit — final screenshot taken
[INFO]  Email verification prompt detected — application submitted successfully
[INFO]  Automation complete | status=SUBMITTED
```

---

## Screenshots

Screenshots are saved to `scripts/screenshots/` at every major step.

| File | Captured At |
|---|---|
| `gh_00_loaded.png` | After page navigation |
| `gh_01_identity.png` | After filling name, email, phone |
| `gh_02_resume.png` | After resume upload |
| `gh_03_questions.png` | After dynamic question fill |
| `gh_04_submitted.png` | After form submission |
| `gh_05_verified.png` | After email verification (if completed) |
| `gh_05_errors.png` | If form validation fails |
| `gh_unanswered_{name}.png` | When a required field has no LLM answer |
| `gh_error_timeout.png` | On page load or wait timeout |
| `gh_error_general.png` | On unexpected exception |

Screenshots are excluded from Git via `.gitignore`.

---

## Related Services

This service is the automation execution layer of the **JobConcierge** system. It does not function as a standalone application tool — it depends on receiving fully resolved payloads from the NestJS orchestration service.

| Service | Language | Port | Role |
|---|---|---|---|
| NestJS Orchestration Service | TypeScript / NestJS | 3000 | Orchestration, LLM mapping, queue, DB |
| Python Automation Service | Python / FastAPI + Playwright | 8000 | Browser automation execution |
| Spring Boot Scout Scraper | Java / Spring Boot | — | Job scraping (WeWorkRemotely, Remotive) |

The NestJS service handles all of: client management, resume storage (Supabase), LLM field mapping (Gemini), saved answers caching, BullMQ job queue, application record persistence, and review queue routing. This service only handles the browser.