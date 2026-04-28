"""
main.py
────────────────────────────────────────────────────────────────────
FastAPI entrypoint for the Job Application Automation Service.

Exposes two endpoints:
  GET  /health               → liveness check for NestJS PythonAutomationService
  POST /submit-application   → triggers browser automation for a job application

The Playwright automation is CPU/IO bound and blocks the event loop,
so we run it in a separate thread with its own event loop to avoid
blocking uvicorn's async workers.
────────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from models import ApplicationRequest, ApplicationResponse
from automation import run_automation

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
)
log = logging.getLogger(__name__)

app = FastAPI(
    title='Job Application Automation Service',
    description='Playwright-based browser automation for job applications',
    version='2.0.0',
)

# Single-worker executor — one browser at a time.
# Increase max_workers if you scale to parallel applications,
# but ensure the machine has enough resources for multiple browsers.
executor = ThreadPoolExecutor(max_workers=1)


def _run_in_thread(request: ApplicationRequest) -> ApplicationResponse:
    """
    Creates a fresh event loop in a separate thread.

    This is required because Playwright's subprocess transport is not
    compatible with uvicorn's event loop on some platforms (Windows).
    A fresh loop per thread avoids this conflict entirely.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(run_automation(request))
    finally:
        loop.close()


@app.get('/health')
def health_check():
    """Liveness probe — NestJS PythonAutomationService polls this on startup."""
    return {'status': 'ok', 'service': 'job-application-automation'}


@app.post('/submit-application', response_model=ApplicationResponse)
async def submit_application(request: ApplicationRequest):
    """
    Trigger automated job application.

    NestJS sends this when an application is dequeued and routed
    to browser automation (non-API path).

    The resume is stored on Supabase Storage — not on this machine's
    filesystem. The resumeUrl in the request payload is a Supabase URL.
    The handler downloads it to a temp file at runtime via httpx.
    We validate that at least one resume reference exists before starting.
    """
    log.info(
        f"Application request received | platform={request.platform} | "
        f"email={request.email} | url={request.jobUrl}"
    )

    # Validate that we have some way to get the resume —
    # either a Supabase URL or a local path (local path only used in dev/testing)
    if not request.resumeUrl and not request.cvFilePath:
        log.error("No resume reference provided — resumeUrl and cvFilePath are both missing")
        raise HTTPException(
            status_code=400,
            detail='No resume provided. resumeUrl (Supabase) or cvFilePath (local) is required.',
        )

    result = await asyncio.get_running_loop().run_in_executor(executor, _run_in_thread, request)

    log.info(f"Request complete | status={result.status}")

    if result.error:
        log.warning(f"Application error: {result.error}")

    return result