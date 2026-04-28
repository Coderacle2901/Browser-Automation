from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Literal


class ApplicationRequest(BaseModel):
    """
    Canonical request sent from NestJS to the Python automation service.
    All ATS handlers receive this same shape.

    NestJS builds this in PythonAutomationService and sends it via HTTP POST
    to /submit-application after the orchestrator resolves the adapter and
    determines the browser route is needed.
    """

    # ── Routing ───────────────────────────────────────────────────────
    jobUrl: str
    platform: str           # 'greenhouse' | 'lever' | 'generic'

    # ── Candidate identity ────────────────────────────────────────────
    firstName: str
    lastName: str
    email: str
    phone: Optional[str] = None
    linkedIn: Optional[str] = None
    portfolio: Optional[str] = None
    address: Optional[str] = None

    # ── Resume ────────────────────────────────────────────────────────
    # resumeUrl is the Supabase Storage URL — primary source in production.
    # Python downloads it to a temp file at runtime via httpx.
    # cvFilePath is a local path — only used in local dev/testing.
    # At least one must be present — validated in main.py before automation starts.
    resumeUrl: Optional[str] = None
    cvFilePath: Optional[str] = None

    # ── Automation settings ───────────────────────────────────────────
    headless: bool = False

    # ── Candidate preferences ─────────────────────────────────────────
    workAuthorization: Optional[str] = None
    requiresSponsorship: Optional[bool] = False
    salaryExpectation: Optional[int] = None

    # ── LLM-mapped answers (Greenhouse only) ─────────────────────────
    # Populated by NestJS GreenhouseAdapter before calling Python.
    # Key: Greenhouse question name field (e.g. "question_12345678")
    # Value: answer string from Gemini, or null if LLM couldn't answer.
    # Null values for required questions should already be caught by NestJS
    # before reaching Python — but the handler checks again as a safety net.
    llmAnswers: Optional[Dict[str, Optional[str]]] = {}

    # ── Questions array (Greenhouse only) ─────────────────────────────
    # The full questions array fetched from the Greenhouse API.
    # Tells the handler each field's type, label, required status, and
    # dropdown options so it knows HOW to fill each field.
    questions: Optional[List[Dict[str, Any]]] = []

    # ── Saved answers (Lever / Generic fallback) ──────────────────────
    # Pre-cached answers keyed by field name or question text.
    # Used by Lever and Generic handlers which don't have LLM mapping yet.
    savedAnswers: Optional[Dict[str, str]] = {}


class ApplicationResponse(BaseModel):
    """
    Normalized response returned to NestJS after automation completes.
    NestJS PythonAutomationService maps this into PythonExecutionResult.
    """

    status: Literal['SUBMITTED', 'FAILED', 'REQUIRES_REVIEW']

    # On SUBMITTED
    confirmation: Optional[str] = None
    screenshotUrl: Optional[str] = None

    # On FAILED
    error: Optional[str] = None

    # On REQUIRES_REVIEW — tells NestJS exactly why automation paused
    requiresReview: Optional[bool] = False
    reviewReason: Optional[Literal[
        'CAPTCHA',
        'UNKNOWN_QUESTION',
        'ESSAY_REQUIRED',
        'APPROVAL_NEEDED',
    ]] = None
    step: Optional[str] = None
    data: Optional[Dict[str, Any]] = None