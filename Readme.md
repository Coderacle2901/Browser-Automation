# Automation Service

A Python FastAPI service that handles browser-based job application form submissions using Playwright. This service is the automation layer of a polyglot microservices system — it receives structured application data from the Spring Boot orchestration service, launches a real browser, fills and submits ATS job application forms, and returns a structured result.

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
9. [Testing with Postman](#testing-with-postman)
10. [Testing with cURL](#testing-with-curl)
11. [Error Handling](#error-handling)
12. [Logging](#logging)
13. [Screenshots](#screenshots)
14. [Related Services](#related-services)

---

## Project Overview

The Automation Service is the Python layer of the Job Application Automation System. It is responsible for:

- Receiving application data from the Spring Boot service via a POST request
- Validating that the CV file exists on the local filesystem before launching the browser
- Launching a Chromium browser instance via Playwright
- Navigating to the target job application URL
- Filling all form fields with human-like typing speed and scroll behaviour
- Uploading the applicant's CV file
- Submitting the form and detecting success or failure from the resulting page
- Capturing screenshots at every major step for debugging and auditability
- Returning a structured JSON response (SUBMITTED or FAILED) to the Spring Boot service

The service does **not** manage any database operations or business logic. That is handled entirely by the Spring Boot orchestration service.

---

## Architecture

```
Spring Boot Service (Port 8080)
        |
        | HTTP POST /submit-application
        v
FastAPI Application (Port 8081)
        |
        v
run_in_thread()  ← new event loop per request (Windows asyncio compatibility)
        |
        v
run_automation()  ← Playwright async automation
        |
        |-- Navigate to job URL
        |-- Fill form fields (human-like typing)
        |-- Upload CV file
        |-- Submit form
        |-- Detect success / failure
        |-- Capture screenshots
        v
ApplicationResponse  →  returned to Spring Boot
```

> **Note on Windows asyncio:** Playwright requires subprocess creation which conflicts with uvicorn's default event loop on Windows. The service uses a `ThreadPoolExecutor` to run each automation in a fresh isolated event loop, bypassing this limitation.

---

## Tech Stack

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.11.x | Runtime |
| FastAPI | Latest | REST API framework |
| Uvicorn | Latest | ASGI server |
| Playwright | Latest | Browser automation |
| Pydantic | Latest | Request/response validation |

---

## Prerequisites

Before running the service ensure the following are installed:

- **Python 3.11** — specifically 3.11.x (see [Environment Setup](#environment-setup))
- **The Spring Boot Job Application Service** — must be running on port 8080 to send requests to this service
- **A CV file** on the local filesystem at the path configured in the database (e.g. `C:/resumes/john_doe_cv.pdf`)

---

## Project Structure

```
C:/automation/scripts/
│
├── venv/                  ← Python virtual environment (do not edit)
├── models.py              ← Pydantic request and response models
├── automation.py          ← Playwright browser automation logic
├── main.py                ← FastAPI application and endpoints
├── requirements.txt       ← Python dependencies
└── screenshots/           ← Auto-created, stores per-run screenshots
```

### File Responsibilities

**models.py**
Defines the Pydantic data models used for request validation and response serialisation:
- `ApplicationRequest` — all fields needed to fill and submit a job application form
- `ApplicationResponse` — status (SUBMITTED/FAILED), optional confirmation, optional error message

**automation.py**
Contains all Playwright browser automation logic:
- `scroll_to_element()` — scrolls a field into view before interacting with it
- `human_type()` — types character by character with randomised delays
- `select_react_option()` — handles React Select dropdown interactions
- `safe_screenshot()` — captures screenshots without crashing on failure
- `fill_greenhouse_form()` — fills all fields on a Greenhouse ATS application form
- `run_automation()` — main entry point, launches browser, runs form fill, detects result

**main.py**
FastAPI application with two endpoints and the Windows asyncio workaround:
- Registers the `/health` and `/submit-application` endpoints
- Uses `ThreadPoolExecutor` + `run_in_executor` to run Playwright in an isolated event loop

---

## Environment Setup

### Step 1 — Install Python 3.11

Download the Python 3.11 installer from:
```
https://www.python.org/downloads/release/python-3119/
```

During installation on Windows, check **Add Python to PATH**.

Verify:
```bash
python --version
# Must show Python 3.11.x
```

> **Important:** Do not use Python 3.12 or 3.13. Playwright has a known asyncio subprocess compatibility issue on Windows with those versions. Python 3.11 is the stable, tested version for this service.

---

### Step 2 — Create a virtual environment

Open a Command Prompt and navigate to the project folder:

```bash
cd C:\automation\scripts
```

Create the virtual environment using Python 3.11 explicitly:

```bash
py -3.11 -m venv venv
```

Activate it:

```bash
venv\Scripts\activate
```

You should see `(venv)` at the start of your terminal prompt.

---

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

---

### Step 4 — Install Playwright browsers

```bash
playwright install chromium
```

This downloads the Chromium browser that Playwright uses for automation. Only Chromium is needed.

---

### Step 5 — Verify setup

```bash
python --version
# Python 3.11.x

uvicorn --version
# uvicorn, version x.x.x
```

---

## Running the Service

### Step 1 — Activate the virtual environment

```bash
cd C:\automation\scripts
venv\Scripts\activate
```

### Step 2 — Start the service

```bash
uvicorn main:app --reload --port 8081
```

### Step 3 — Verify startup

On successful startup you will see:
```
INFO:     Uvicorn running on http://127.0.0.1:8081 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

Test the health endpoint:
```bash
curl http://127.0.0.1:8081/health
```

Expected response:
```json
{
  "status": "Python automation service is running"
}
```

### Step 4 — View interactive API docs

FastAPI provides auto-generated interactive documentation. Open in your browser:
```
http://127.0.0.1:8081/docs
```

---

### Stopping the service

Press `Ctrl+C` in the terminal where uvicorn is running.

---

### Important — zombie process warning

If you see `Access is denied` errors when trying to delete the `venv` folder or restart the service, old uvicorn processes are still running. Check for and kill them:

```bash
netstat -ano | findstr :8081
taskkill /PID <PID> /F
```

---

## API Endpoints

Base URL: `http://127.0.0.1:8081`

### GET /health

Confirms the service is running. Called by the Spring Boot service before sending application requests.

**Response (200 OK):**
```json
{
  "status": "Python automation service is running"
}
```

---

### POST /submit-application

Receives application data, runs Playwright browser automation, and returns the result.

**Request Body:**
```json
{
  "jobUrl": "https://job-boards.greenhouse.io/easyship/jobs/4609487006",
  "platform": "greenhouse",
  "firstName": "John",
  "lastName": "Doe",
  "email": "johndoe@gmail.com",
  "phone": "+1234567890",
  "cvFilePath": "<project_root>/resumes/john_doe_cv.pdf",
  "headless": false,
  "address": "New York, USA"
}
```

###
Replace <project_root> with your project-folder location.


**Request Fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| jobUrl | string | Yes | Full URL of the job application form |
| platform | string | Yes | ATS platform name (e.g. greenhouse) |
| firstName | string | Yes | Applicant first name |
| lastName | string | Yes | Applicant last name |
| email | string | Yes | Applicant email address |
| phone | string | No | Applicant phone number |
| cvFilePath | string | Yes | Absolute path to the CV file on the local filesystem |
| headless | boolean | No | Run browser invisibly (true) or visibly (false). Default: false |
| address | string | No | Applicant address. Default: New York, USA |

**Success Response (200 OK):**
```json
{
  "status": "SUBMITTED",
  "confirmation": "Application submitted for John Doe",
  "error": null
}
```

**Failure Response (200 OK):**
```json
{
  "status": "FAILED",
  "confirmation": null,
  "error": "Form validation failed: This field is required"
}
```

**CV not found Response (400 Bad Request):**
```json
{
  "detail": "CV file not found at: C:/resumes/john_doe_cv.pdf"
}
```

---

## Testing with Postman

### Health Check

```
Method: GET
URL:    http://127.0.0.1:8081/health
```

### Submit an Application

```
Method:  POST
URL:     http://127.0.0.1:8081/submit-application
Headers: Content-Type: application/json

Body (raw JSON):
{
  "jobUrl": "https://job-boards.greenhouse.io/easyship/jobs/4609487006",
  "platform": "greenhouse",
  "firstName": "John",
  "lastName": "Doe",
  "email": "johndoe@gmail.com",
  "phone": "+1234567890",
  "cvFilePath": "<project_root>/resumes/john_doe_cv.pdf",
  "headless": false,
  "address": "New York, USA"
}
```
###
Replace <project_root> with your project-folder location.

> **Tip:** Set `headless: false` to watch the browser fill the form in real time. Set `headless: true` to run silently in the background.

---

## Testing with cURL

### Health Check

```bash
curl -X GET http://127.0.0.1:8081/health
```

### Submit an Application

```bash
curl -X POST http://127.0.0.1:8081/submit-application \
  -H "Content-Type: application/json" \
  -d '{
    "jobUrl": "https://job-boards.greenhouse.io/easyship/jobs/4609487006",
    "platform": "greenhouse",
    "firstName": "John",
    "lastName": "Doe",
    "email": "johndoe@gmail.com",
    "phone": "+1234567890",
    "cvFilePath": "<project_root>/resumes/john_doe_cv.pdf",
    "headless": false,
    "address": "New York, USA"
  }'
```

###
Replace <project_root> with your project-folder location.

---

## Error Handling

The service handles errors at two levels:

**Request validation (400):** If the CV file does not exist at the specified path, the service returns a 400 immediately before launching any browser.

**Automation errors:** All Playwright errors are caught inside `run_automation()` and returned as a FAILED `ApplicationResponse` with the error message included — the service always returns 200 to the Spring Boot caller and lets Spring Boot decide how to handle a FAILED result.

| Scenario | Behaviour |
|---|---|
| CV file not found | 400 Bad Request — no browser launched |
| Page load timeout | FAILED response with timeout message |
| Form field not found | FAILED response with selector error |
| Validation errors on form | FAILED response listing the error messages |
| Success page detected | SUBMITTED response with confirmation |
| Unexpected exception | FAILED response with error details |

---

## Logging

The service logs all key events to the console using Python's standard logging module at DEBUG and INFO levels.

```
[INFO]  Application request received - platform: greenhouse | email: johndoe@gmail.com
[INFO]  Starting automation for John Doe | https://job-boards.greenhouse.io/...
[INFO]  Navigating to job URL
[INFO]  Filling first name
[INFO]  Filling last name
[INFO]  Filling email
[INFO]  Filling phone
[INFO]  Screenshot: basic fields filled
[INFO]  Uploading resume
[INFO]  Screenshot: resume uploaded
[INFO]  Clicking submit button
[INFO]  Screenshot: after submit
[INFO]  Submission successful
[INFO]  Returning result: status=SUBMITTED
```

---

## Screenshots

Screenshots are automatically saved to `C:/automation/scripts/screenshots/` at every major step of the automation. This allows debugging of any failures without re-running the browser.

| File | Captured At |
|---|---|
| `00_page_loaded.png` | After page navigation |
| `01_basic_fields.png` | After filling name, email, phone |
| `02_resume_uploaded.png` | After CV file upload |
| `03_clearance_q1.png` | After first custom question |
| `04_clearance_q2.png` | After second custom question |
| `05_all_questions.png` | After all questions filled |
| `06_gdpr_checked.png` | After GDPR consent checkbox |
| `07_after_submit.png` | After form submission |
| `08_validation_errors.png` | If form validation fails |
| `error_timeout.png` | If a timeout occurs |
| `error_general.png` | If an unexpected error occurs |

---

## Related Services

This service is part of a polyglot microservices system. It only handles browser automation and does not function independently as a standalone application submission tool.

| Service | Language | Port | Location |
|---|---|---|---|
| Job Application Service | Java / Spring Boot | 8080 | `job-application-service/` |
| Automation Service | Python / FastAPI + Playwright | 8081 | This repository |

See the **Job Application Service README** for full setup instructions for the Spring Boot service and database configuration.