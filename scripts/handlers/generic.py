"""
handlers/generic.py
────────────────────────────────────────────────────────────────────
Generic browser automation handler — heuristic form filling.

Used for:
  - Workday (browser fallback — no public API)
  - iCIMS, SmartRecruiters, BambooHR, Ashby
  - Custom company career pages
  - Any ATS without a dedicated handler

Strategy:
  We don't know the exact field structure in advance, so we:
  1. Check for CAPTCHA immediately
  2. Find all visible inputs and textareas
  3. Match each field against FIELD_PATTERNS (label/id/placeholder heuristics)
  4. Fill matched fields from candidate profile
  5. Check savedAnswers for any remaining known fields
  6. Pause for review on unknown required fields
  7. Download resume from Supabase → upload to first file input
  8. Submit and detect success

Resume handling:
  Same pattern as Greenhouse and Lever — download from Supabase
  Storage URL via httpx to a temp file, upload, delete after.
────────────────────────────────────────────────────────────────────
"""

import os
import logging
import tempfile
import httpx
from pathlib import Path
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from models import ApplicationRequest, ApplicationResponse
from browser_utils import (
    human_type, human_type_locator, human_delay,
    safe_screenshot, scroll_to_element,
    check_for_captcha, detect_success, collect_form_errors,
)

log = logging.getLogger(__name__)

# Heuristic keyword patterns — matched against combined field signals
# (id + name + placeholder + aria-label + label text)
FIELD_PATTERNS = {
    'first_name':  ['first name', 'firstname', 'given name'],
    'last_name':   ['last name', 'lastname', 'surname', 'family name'],
    'full_name':   ['full name', 'your name'],
    'email':       ['email', 'e-mail', 'email address'],
    'phone':       ['phone', 'mobile', 'telephone', 'cell'],
    'linkedin':    ['linkedin', 'linked in', 'linkedin url', 'linkedin profile'],
    'portfolio':   ['portfolio', 'website', 'personal url', 'github'],
    'address':     ['address', 'city', 'location'],
    'work_auth':   ['authorized', 'work authorization', 'legally authorized', 'work in the us'],
    'sponsorship': ['sponsorship', 'visa sponsorship', 'require sponsorship', 'sponsor'],
    'salary':      ['salary', 'compensation', 'expected salary', 'desired salary', 'expected pay'],
}


async def run_generic(page: Page, request: ApplicationRequest) -> ApplicationResponse:
    """
    Main entry point for generic form automation.
    Returns ApplicationResponse — never raises.
    """
    log.info(f"Generic handler | {request.email} | {request.jobUrl}")

    temp_files: list[str] = []

    try:
        # ── 1. Navigate ──────────────────────────────────────────────
        await page.goto(request.jobUrl, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(human_delay(1000, 2000))
        await safe_screenshot(page, 'screenshots/generic_00_loaded.png')

        # ── 2. CAPTCHA check ──────────────────────────────────────────
        if await check_for_captcha(page):
            return ApplicationResponse(
                status='REQUIRES_REVIEW',
                requiresReview=True,
                reviewReason='CAPTCHA',
                step='PAGE_LOAD',
                data={'url': request.jobUrl},
            )

        # ── 3. Fill all text fields heuristically ────────────────────
        review_result = await _fill_all_fields(page, request)
        if review_result:
            return review_result

        await safe_screenshot(page, 'screenshots/generic_01_filled.png')

        # ── 4. Resume — download from Supabase, upload to form ────────
        resume_path = await _resolve_resume(request, temp_files)
        if resume_path:
            uploaded = await _upload_resume(page, resume_path)
            if not uploaded:
                log.warning("Generic: no file input found — resume not uploaded")
        else:
            log.warning("Generic: no resume available — skipping upload")

        await safe_screenshot(page, 'screenshots/generic_02_resume.png')

        # ── 5. Submit ─────────────────────────────────────────────────
        submitted = await _click_submit(page)
        if not submitted:
            return ApplicationResponse(
                status='REQUIRES_REVIEW',
                requiresReview=True,
                reviewReason='APPROVAL_NEEDED',
                step='SUBMIT',
                data={'reason': 'Could not find submit button — manual review needed'},
            )

        await page.wait_for_timeout(4000)
        await safe_screenshot(page, 'screenshots/generic_03_submitted.png')

        # ── 6. Detect outcome ─────────────────────────────────────────
        if await detect_success(page):
            log.info("Generic submission confirmed")
            return ApplicationResponse(
                status='SUBMITTED',
                confirmation=f'Application submitted for {request.firstName} {request.lastName}',
                screenshotUrl='screenshots/generic_03_submitted.png',
            )

        errors = await collect_form_errors(page)
        if errors:
            await safe_screenshot(page, 'screenshots/generic_04_errors.png')
            return ApplicationResponse(
                status='FAILED',
                error=f'Form validation: {"; ".join(errors)}',
                step='SUBMIT',
            )

        return ApplicationResponse(
            status='FAILED',
            error='Submitted but success not confirmed — check screenshots',
            step='SUBMIT',
        )

    except PlaywrightTimeoutError as e:
        await safe_screenshot(page, 'screenshots/generic_error_timeout.png')
        log.error(f"Generic timeout: {e}")
        return ApplicationResponse(status='FAILED', error=f'Timeout: {e}', step='TIMEOUT')

    except Exception as e:
        await safe_screenshot(page, 'screenshots/generic_error_general.png')
        log.error(f"Generic error: {e}", exc_info=True)
        return ApplicationResponse(status='FAILED', error=f'Automation error: {e}', step='ERROR')

    finally:
        _cleanup_temp_files(temp_files)


# ─────────────────────────────────────────────────────────────────────
# RESUME HANDLING
# ─────────────────────────────────────────────────────────────────────

async def _resolve_resume(
    request: ApplicationRequest,
    temp_files: list[str],
) -> str | None:
    """
    Get a usable local file path for the resume.
    Tries local path first (dev only), then downloads from Supabase URL.
    """
    if request.cvFilePath and os.path.exists(request.cvFilePath):
        log.debug(f"Using local resume: {request.cvFilePath}")
        return request.cvFilePath

    if request.resumeUrl:
        log.info(f"Downloading resume from Supabase: {request.resumeUrl[:80]}...")
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            tmp_path = tmp.name
            tmp.close()

            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                response = await client.get(request.resumeUrl)
                response.raise_for_status()
                with open(tmp_path, 'wb') as f:
                    f.write(response.content)

            temp_files.append(tmp_path)
            log.info(f"Resume downloaded: {tmp_path}")
            return tmp_path

        except Exception as e:
            log.error(f"Resume download failed: {e}")
            return None

    return None


async def _upload_resume(page: Page, resume_path: str) -> bool:
    """
    Find the first visible file input and upload the resume.
    Returns True if upload succeeded.
    """
    file_inputs = await page.query_selector_all('input[type="file"]')
    for file_input in file_inputs:
        try:
            await file_input.set_input_files(resume_path)
            await page.wait_for_timeout(human_delay(800, 1400))
            log.info("Generic: resume uploaded")
            return True
        except Exception as e:
            log.warning(f"Generic: file input failed — {e}")
            continue
    return False


# ─────────────────────────────────────────────────────────────────────
# FORM FIELD FILLING
# ─────────────────────────────────────────────────────────────────────

async def _fill_all_fields(
    page: Page,
    request: ApplicationRequest,
) -> ApplicationResponse | None:
    """
    Find all visible text inputs and textareas, try to fill heuristically.
    Returns None if all handled, ApplicationResponse if review needed.
    """
    saved = request.savedAnswers or {}

    inputs = await page.query_selector_all(
        'input[type="text"], input[type="email"], input[type="tel"], '
        'input[type="number"], input[type="url"], textarea'
    )

    for el in inputs:
        try:
            is_visible   = await el.is_visible()
            is_disabled  = await el.get_attribute('disabled')
            current_val  = await el.evaluate('el => el.value')

            if not is_visible or is_disabled or current_val:
                continue

            field_id    = await el.get_attribute('id') or ''
            field_name  = await el.get_attribute('name') or ''
            placeholder = (await el.get_attribute('placeholder') or '').lower()
            aria_label  = (await el.get_attribute('aria-label') or '').lower()
            label_text  = (await _get_label(page, field_id) or '').lower()

            # Combined signal for heuristic matching
            signal = f"{field_id} {field_name} {placeholder} {aria_label} {label_text}".lower()

            log.debug(f"Generic field: '{signal[:80]}'")

            # ── Saved answer by field id or name ──
            matched_saved = False
            for key in [field_id, field_name]:
                if key and key in saved:
                    await human_type_locator(page, page.locator(el), saved[key])
                    matched_saved = True
                    break

            if matched_saved:
                continue

            # ── Heuristic fill from candidate profile ──
            filled = await _heuristic_fill(page, el, signal, request)
            if filled:
                continue

            # ── Unknown required field → pause for review ──
            is_required = await _is_required(el)
            if is_required:
                label_display = label_text or placeholder or aria_label or field_name
                log.warning(f"Generic: unknown required field: '{label_display}'")
                await safe_screenshot(page, 'screenshots/generic_unknown_field.png')
                return ApplicationResponse(
                    status='REQUIRES_REVIEW',
                    requiresReview=True,
                    reviewReason='UNKNOWN_QUESTION',
                    step='FORM_FILL',
                    data={
                        'fieldId':    field_id,
                        'fieldName':  field_name,
                        'label':      label_display,
                        'screenshotUrl': 'screenshots/generic_unknown_field.png',
                    },
                )

        except Exception as field_error:
            log.warning(f"Generic: field error — {field_error}")
            continue

    return None


async def _heuristic_fill(
    page: Page,
    element,
    signal: str,
    request: ApplicationRequest,
) -> bool:
    """
    Match signal against FIELD_PATTERNS and fill from candidate profile.
    Returns True if filled, False if no match found.
    """
    locator = page.locator(element)

    if _matches(signal, FIELD_PATTERNS['first_name']) and not _matches(signal, FIELD_PATTERNS['last_name']):
        await human_type_locator(page, locator, request.firstName)
        return True

    if _matches(signal, FIELD_PATTERNS['last_name']):
        await human_type_locator(page, locator, request.lastName)
        return True

    if _matches(signal, FIELD_PATTERNS['full_name']):
        await human_type_locator(page, locator, f"{request.firstName} {request.lastName}")
        return True

    if _matches(signal, FIELD_PATTERNS['email']):
        await human_type_locator(page, locator, request.email)
        return True

    if _matches(signal, FIELD_PATTERNS['phone']) and request.phone:
        await human_type_locator(page, locator, request.phone)
        return True

    if _matches(signal, FIELD_PATTERNS['linkedin']) and request.linkedIn:
        await human_type_locator(page, locator, request.linkedIn)
        return True

    if _matches(signal, FIELD_PATTERNS['portfolio']) and request.portfolio:
        await human_type_locator(page, locator, request.portfolio)
        return True

    if _matches(signal, FIELD_PATTERNS['address']) and request.address:
        await human_type_locator(page, locator, request.address)
        return True

    if _matches(signal, FIELD_PATTERNS['work_auth']):
        await human_type_locator(page, locator, 'Yes' if request.workAuthorization else 'No')
        return True

    if _matches(signal, FIELD_PATTERNS['sponsorship']):
        await human_type_locator(page, locator, 'Yes' if request.requiresSponsorship else 'No')
        return True

    if _matches(signal, FIELD_PATTERNS['salary']) and request.salaryExpectation:
        await human_type_locator(page, locator, str(request.salaryExpectation))
        return True

    return False


async def _click_submit(page: Page) -> bool:
    """Try multiple selector strategies to find and click the submit button."""
    selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Apply")',
        'button:has-text("Send Application")',
        '[data-testid*="submit"]',
    ]

    for selector in selectors:
        try:
            btn = page.locator(selector).first
            if await btn.count() and await btn.is_visible():
                await btn.scroll_into_view_if_needed()
                await page.wait_for_timeout(human_delay(1500, 2500))
                await btn.click()
                log.info(f"Submit clicked via '{selector}'")
                return True
        except Exception:
            continue

    return False


# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def _matches(signal: str, patterns: list[str]) -> bool:
    return any(p in signal for p in patterns)


async def _get_label(page: Page, field_id: str) -> str:
    if not field_id:
        return ''
    try:
        label = await page.query_selector(f'label[for="{field_id}"]')
        if label:
            return await label.inner_text()
    except Exception:
        pass
    return ''


async def _is_required(element) -> bool:
    try:
        required     = await element.get_attribute('required')
        aria_required = await element.get_attribute('aria-required')
        return required is not None or aria_required == 'true'
    except Exception:
        return False


def _cleanup_temp_files(temp_files: list[str]) -> None:
    for path in temp_files:
        try:
            if os.path.exists(path):
                os.remove(path)
                log.debug(f"Temp file deleted: {path}")
        except Exception as e:
            log.warning(f"Could not delete temp file {path}: {e}")