"""
handlers/lever.py
────────────────────────────────────────────────────────────────────
Lever browser automation handler.

Used when:
  - NestJS LeverAdapter falls back from API to browser
  - Or when platform='lever' is sent directly

Lever application pages:
  - Single full name field (not first/last separately)
  - Standard fields by name attribute: name, email, phone, org
  - URL fields: urls[LinkedIn], urls[Portfolio]
  - File input inside a drop zone for resume
  - "Cards" — Lever's custom additional question sections
  - Clean URL structure: jobs.lever.co/{company}/{posting-id}
    → application form lives at the same URL + /apply

Resume handling:
  - Resume lives on Supabase Storage (resumeUrl)
  - Python downloads it to a temp file via httpx
  - Playwright uploads from the temp path
  - Temp file deleted after upload regardless of outcome

Custom questions:
  - Lever doesn't expose a public questions API like Greenhouse
  - We use savedAnswers dict for known questions
  - Heuristic label matching for common patterns (salary, auth, etc.)
  - Unknown required questions → REQUIRES_REVIEW
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


async def run_lever(page: Page, request: ApplicationRequest) -> ApplicationResponse:
    """
    Main entry point for Lever form automation.
    Returns ApplicationResponse — never raises.
    """
    log.info(f"Lever handler | {request.email} | {request.jobUrl}")

    temp_files: list[str] = []

    try:
        # ── 1. Navigate to /apply URL ─────────────────────────────────
        apply_url = _ensure_apply_url(request.jobUrl)
        await page.goto(apply_url, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(human_delay(800, 1500))
        await safe_screenshot(page, 'screenshots/lever_00_loaded.png')

        # ── 2. CAPTCHA check ──────────────────────────────────────────
        if await check_for_captcha(page):
            return ApplicationResponse(
                status='REQUIRES_REVIEW',
                requiresReview=True,
                reviewReason='CAPTCHA',
                step='PAGE_LOAD',
                data={'url': apply_url},
            )

        # ── 3. Standard identity fields ───────────────────────────────
        await _fill_standard_fields(page, request)
        await safe_screenshot(page, 'screenshots/lever_01_identity.png')

        # ── 4. Resume — download from Supabase, upload to form ────────
        resume_path = await _resolve_resume(request, temp_files)
        if not resume_path:
            return ApplicationResponse(
                status='FAILED',
                error='Could not resolve resume — resumeUrl and cvFilePath both unavailable',
                step='RESUME_UPLOAD',
            )

        await _upload_resume(page, resume_path)
        await safe_screenshot(page, 'screenshots/lever_02_resume.png')

        # ── 5. Card questions ─────────────────────────────────────────
        review_result = await _fill_card_questions(page, request)
        if review_result:
            return review_result

        await safe_screenshot(page, 'screenshots/lever_03_questions.png')

        # ── 6. Submit ─────────────────────────────────────────────────
        submit_btn = page.get_by_role('button', name='Submit application')
        if not await submit_btn.count():
            submit_btn = page.locator('button[type="submit"]')

        await submit_btn.scroll_into_view_if_needed()
        await page.wait_for_timeout(human_delay(1500, 2500))
        await submit_btn.click()
        await page.wait_for_timeout(4000)
        await safe_screenshot(page, 'screenshots/lever_04_submitted.png')

        # ── 7. Detect outcome ─────────────────────────────────────────
        if await detect_success(page):
            log.info("Lever submission confirmed")
            return ApplicationResponse(
                status='SUBMITTED',
                confirmation=f'Application submitted for {request.firstName} {request.lastName}',
                screenshotUrl='screenshots/lever_04_submitted.png',
            )

        errors = await collect_form_errors(page)
        if errors:
            await safe_screenshot(page, 'screenshots/lever_05_errors.png')
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
        await safe_screenshot(page, 'screenshots/lever_error_timeout.png')
        log.error(f"Lever timeout: {e}")
        return ApplicationResponse(status='FAILED', error=f'Timeout: {e}', step='TIMEOUT')

    except Exception as e:
        await safe_screenshot(page, 'screenshots/lever_error_general.png')
        log.error(f"Lever error: {e}", exc_info=True)
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


async def _upload_resume(page: Page, resume_path: str) -> None:
    """
    Upload resume to Lever's file input.
    The <input type="file"> may be visually hidden inside a drop zone —
    set_input_files() bypasses the UI entirely.
    """
    file_input = page.locator('input[type="file"]').first
    if await file_input.count():
        await file_input.set_input_files(resume_path)
        await page.wait_for_timeout(human_delay(800, 1400))
        log.info("Resume uploaded to Lever")
    else:
        log.warning("No file input found — resume upload skipped")


# ─────────────────────────────────────────────────────────────────────
# STANDARD FIELDS
# ─────────────────────────────────────────────────────────────────────

async def _fill_standard_fields(page: Page, request: ApplicationRequest) -> None:
    """
    Fill Lever's fixed identity fields.
    Lever uses name attributes rather than IDs — locator-based for resilience.
    """
    full_name = f"{request.firstName} {request.lastName}"

    # Full name — Lever uses one combined field
    name_field = page.locator('input[name="name"]')
    if not await name_field.count():
        name_field = page.get_by_label('Full name', exact=False)
    if await name_field.count():
        await human_type_locator(page, name_field, full_name)

    # Email
    email_field = page.locator('input[name="email"]')
    if await email_field.count():
        await human_type_locator(page, email_field, request.email)

    # Phone
    if request.phone:
        phone_field = page.locator('input[name="phone"]')
        if await phone_field.count():
            await human_type_locator(page, phone_field, request.phone)

    # LinkedIn
    if request.linkedIn:
        linkedin_field = page.locator('input[name="urls[LinkedIn]"]')
        if not await linkedin_field.count():
            linkedin_field = page.locator('input[placeholder*="linkedin" i]')
        if await linkedin_field.count():
            await human_type_locator(page, linkedin_field, request.linkedIn)

    # Portfolio
    if request.portfolio:
        portfolio_field = page.locator('input[name="urls[Portfolio]"]')
        if not await portfolio_field.count():
            portfolio_field = page.locator('input[placeholder*="portfolio" i]')
        if await portfolio_field.count():
            await human_type_locator(page, portfolio_field, request.portfolio)


# ─────────────────────────────────────────────────────────────────────
# CARD QUESTIONS
# ─────────────────────────────────────────────────────────────────────

async def _fill_card_questions(
    page: Page,
    request: ApplicationRequest,
) -> ApplicationResponse | None:
    """
    Fill Lever's custom card question sections.

    Lever doesn't expose a public questions API like Greenhouse,
    so we use a combination of:
      1. savedAnswers dict (keyed by field name)
      2. Heuristic label/placeholder matching for common patterns
      3. REQUIRES_REVIEW for unknown required questions
    """
    saved = request.savedAnswers or {}
    standard_names = {'name', 'email', 'phone', 'org', 'urls[LinkedIn]', 'urls[Portfolio]'}

    all_inputs = await page.query_selector_all(
        'input[type="text"], input[type="number"], textarea'
    )

    for input_el in all_inputs:
        name        = await input_el.get_attribute('name') or ''
        placeholder = await input_el.get_attribute('placeholder') or ''
        tag         = await input_el.evaluate('el => el.tagName.toLowerCase()')

        if name in standard_names:
            continue

        label_text   = await _get_card_label(page, input_el)
        label_lower  = label_text.lower()
        combined     = f"{label_lower} {placeholder.lower()}"

        log.debug(f"Card field: name='{name}' | label='{label_text}'")

        # ── Saved answer by field name ──
        if name in saved:
            await human_type_locator(page, page.locator(f'[name="{name}"]'), saved[name])
            continue

        # ── Heuristic fill from candidate profile ──
        filled = _heuristic_match(combined)
        if filled is not None:
            value = _resolve_heuristic_value(filled, request)
            if value:
                await human_type_locator(page, page.locator(input_el), value)
                continue

        # ── Unknown required field → review ──
        is_required = await _is_required(input_el)
        if is_required:
            log.warning(f"Lever: unknown required field: '{label_text}'")
            await safe_screenshot(page, 'screenshots/lever_unknown_question.png')
            return ApplicationResponse(
                status='REQUIRES_REVIEW',
                requiresReview=True,
                reviewReason='UNKNOWN_QUESTION',
                step='CARD_QUESTIONS',
                data={
                    'questionLabel': label_text,
                    'fieldName': name,
                    'screenshotUrl': 'screenshots/lever_unknown_question.png',
                },
            )

    return None


def _heuristic_match(combined: str) -> str | None:
    """
    Match combined label+placeholder text against known field patterns.
    Returns the profile field key if matched, None if unknown.
    """
    if any(kw in combined for kw in ['authorized', 'work in the us', 'legally authorized']):
        return 'work_auth'
    if any(kw in combined for kw in ['sponsor', 'visa sponsorship']):
        return 'sponsorship'
    if any(kw in combined for kw in ['salary', 'compensation', 'expected pay']):
        return 'salary'
    if 'linkedin' in combined:
        return 'linkedin'
    if any(kw in combined for kw in ['portfolio', 'website', 'github']):
        return 'portfolio'
    return None


def _resolve_heuristic_value(field_key: str, request: ApplicationRequest) -> str | None:
    """Map a heuristic field key to the actual candidate value."""
    mapping = {
        'work_auth':   'Yes' if request.workAuthorization else 'No',
        'sponsorship': 'Yes' if request.requiresSponsorship else 'No',
        'salary':      str(request.salaryExpectation) if request.salaryExpectation else None,
        'linkedin':    request.linkedIn,
        'portfolio':   request.portfolio,
    }
    return mapping.get(field_key)


async def _get_card_label(page: Page, element) -> str:
    """Get label text for a Lever card field."""
    try:
        aria_label = await element.get_attribute('aria-label')
        if aria_label:
            return aria_label.strip()

        placeholder = await element.get_attribute('placeholder')
        if placeholder:
            return placeholder.strip()

        label = await element.evaluate("""
            el => {
                let parent = el.closest('.application-question, .field, [class*="question"]');
                if (parent) {
                    let label = parent.querySelector('label, legend, h3, p');
                    return label ? label.innerText : '';
                }
                return '';
            }
        """)
        return label.strip() if label else ''
    except Exception:
        return ''


async def _is_required(element) -> bool:
    try:
        required = await element.get_attribute('required')
        aria_required = await element.get_attribute('aria-required')
        return required is not None or aria_required == 'true'
    except Exception:
        return False


def _ensure_apply_url(url: str) -> str:
    """Ensure the URL ends with /apply — Lever's application form lives there."""
    url = url.rstrip('/')
    if not url.endswith('/apply'):
        return f"{url}/apply"
    return url


def _cleanup_temp_files(temp_files: list[str]) -> None:
    for path in temp_files:
        try:
            if os.path.exists(path):
                os.remove(path)
                log.debug(f"Temp file deleted: {path}")
        except Exception as e:
            log.warning(f"Could not delete temp file {path}: {e}")