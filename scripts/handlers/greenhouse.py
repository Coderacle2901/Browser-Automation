"""
handlers/greenhouse.py
────────────────────────────────────────────────────────────────────
Greenhouse browser automation handler — LLM-driven dynamic form filling.
Updated: April 2026
────────────────────────────────────────────────────────────────────
"""

import os
import logging
import tempfile
import httpx
import certifi
from pathlib import Path
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from models import ApplicationRequest, ApplicationResponse
from browser_utils import (
    human_type, human_delay, safe_screenshot,
    scroll_to_element, select_react_option,
    upload_file, check_for_captcha,
    detect_success, collect_form_errors,
)

log = logging.getLogger(__name__)

# Signals that Greenhouse is asking for email verification after submit
# The application is already in their system at this point
VERIFICATION_SIGNALS = [
    'enter the code',
    'verification code',
    'check your email',
    'confirm your email',
    'sent you a code',
    '8-digit',
    'digit code',
    'we sent a',
    'verify your email',
]


async def run_greenhouse(page: Page, request: ApplicationRequest) -> ApplicationResponse:
    """
    Main entry point for Greenhouse automation.

    Flow:
      1. Navigate using domcontentloaded (faster than networkidle on Greenhouse)
      2. CAPTCHA check — if headless=False, waits for human to solve it
      3. Fill standard identity fields (first_name, last_name, email, phone)
      4. Download resume from Supabase URL → temp file → upload to #resume
      5. Dynamic question loop using LLM-mapped answers
      6. GDPR consent checkbox
      7. Submit and detect success or email verification prompt
    """
    questions = request.questions or []
    llm_answers = request.llmAnswers or {}

    log.info(
        f"Greenhouse handler | {request.email} | "
        f"{len(questions)} questions | {len(llm_answers)} LLM answers"
    )

    temp_files: list[str] = []

    try:
        # ── 1. Navigate ──────────────────────────────────────────────
        await page.goto(request.jobUrl, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(human_delay(2000, 3500))
        await safe_screenshot(page, 'screenshots/gh_00_loaded.png')

        # ── 2. CAPTCHA handling ───────────────────────────────────────
        if await check_for_captcha(page):
            log.warning("CAPTCHA detected!")

            if not request.headless:
                log.info("GUI mode: solve the CAPTCHA in the browser window now...")
                try:
                    await page.wait_for_selector(
                        'iframe[src*="recaptcha"]',
                        state='hidden',
                        timeout=120000,
                    )
                    log.info("CAPTCHA resolved — continuing form fill")
                except PlaywrightTimeoutError:
                    log.error("CAPTCHA not solved within 2 minutes")
                    return ApplicationResponse(
                        status='REQUIRES_REVIEW',
                        requiresReview=True,
                        reviewReason='CAPTCHA',
                        step='PAGE_LOAD',
                        data={'url': request.jobUrl, 'reason': 'CAPTCHA timeout'},
                    )
            else:
                log.error("CAPTCHA detected in headless mode — routing to review")
                return ApplicationResponse(
                    status='REQUIRES_REVIEW',
                    requiresReview=True,
                    reviewReason='CAPTCHA',
                    step='PAGE_LOAD',
                    data={'url': request.jobUrl},
                )

        # ── 3. Standard identity fields ───────────────────────────────
        await _fill_standard_fields(page, request)
        await safe_screenshot(page, 'screenshots/gh_01_identity.png')

        # ── 4. Resume — download from Supabase, upload to form ────────
        resume_path = await _resolve_resume(request, temp_files)
        if not resume_path:
            return ApplicationResponse(
                status='FAILED',
                error='Could not resolve resume — resumeUrl and cvFilePath both unavailable',
                step='RESUME_UPLOAD',
            )

        await upload_file(page, '#resume', resume_path)
        await page.wait_for_timeout(human_delay(800, 1500))
        await safe_screenshot(page, 'screenshots/gh_02_resume.png')

        # ── 5. Dynamic question loop ──────────────────────────────────
        review_result = await _fill_questions(page, questions, llm_answers, request, temp_files)
        if review_result:
            return review_result

        await safe_screenshot(page, 'screenshots/gh_03_questions.png')

        # ── 6. GDPR consent ───────────────────────────────────────────
        await _handle_gdpr(page)

        # ── 7. Submit ─────────────────────────────────────────────────
        await scroll_to_element(page, 'button[type="submit"]')
        await page.wait_for_timeout(human_delay(1500, 2500))

        if not request.headless:
            log.info("About to submit — final screenshot taken")

        await page.click('button[type="submit"]')

        # Wait longer than before — gives Greenhouse time to show either
        # the success page OR the email verification prompt
        await page.wait_for_timeout(8000)
        await safe_screenshot(page, 'screenshots/gh_04_submitted.png')

        # ── 8. Check for email verification prompt ────────────────────
        # Greenhouse sends an 8-digit code to verify email after submission.
        # The application is already received at this point — treat as SUCCESS.
        page_content = (await page.content()).lower()

        if any(signal in page_content for signal in VERIFICATION_SIGNALS):
            log.info("Email verification prompt detected — application submitted successfully")

            if not request.headless:
                # GUI mode — give user 3 minutes to enter the code manually
                log.info("GUI mode: please enter the verification code in the browser window")
                try:
                    # Wait for any success/thank-you text to appear after code entry
                    await page.wait_for_selector(
                        'text=thank you',
                        timeout=180000,
                    )
                    log.info("Email verification completed — submission fully confirmed")
                    await safe_screenshot(page, 'screenshots/gh_05_verified.png')
                except PlaywrightTimeoutError:
                    # User didn't enter code in time — but application was still submitted
                    log.warning("Email verification timed out — application was already submitted")

            return ApplicationResponse(
                status='SUBMITTED',
                confirmation=f'Application submitted for {request.firstName} {request.lastName} — email verification pending',
                screenshotUrl='screenshots/gh_04_submitted.png',
            )

        # ── 9. Detect standard success page ───────────────────────────
        if await detect_success(page):
            log.info("Greenhouse submission successful")
            return ApplicationResponse(
                status='SUBMITTED',
                confirmation=f'Application submitted for {request.firstName} {request.lastName}',
                screenshotUrl='screenshots/gh_04_submitted.png',
            )

        # ── 10. Check for form validation errors ──────────────────────
        errors = await collect_form_errors(page)
        if errors:
            log.error(f"Form validation errors: {errors}")
            await safe_screenshot(page, 'screenshots/gh_05_errors.png')
            return ApplicationResponse(
                status='FAILED',
                error=f'Form validation failed: {"; ".join(errors)}',
                step='SUBMIT',
            )

        return ApplicationResponse(
            status='FAILED',
            error='Submitted but success page not detected — check screenshots',
            step='SUBMIT',
        )

    except PlaywrightTimeoutError as e:
        await safe_screenshot(page, 'screenshots/gh_error_timeout.png')
        log.error(f"Timeout: {e}")
        return ApplicationResponse(status='FAILED', error=f'Timeout: {e}', step='TIMEOUT')

    except Exception as e:
        await safe_screenshot(page, 'screenshots/gh_error_general.png')
        log.error(f"Greenhouse Error: {e}", exc_info=True)
        return ApplicationResponse(status='FAILED', error=str(e), step='ERROR')

    finally:
        _cleanup_temp_files(temp_files)


# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────

STANDARD_FIELD_LABELS = {
    'first name', 'last name', 'email', 'phone', 'resume/cv', 'cover letter',
}
STANDARD_FIELD_NAMES = {
    'first_name', 'last_name', 'email', 'phone',
    'resume', 'resume_text', 'cover_letter', 'cover_letter_text',
}


# ─────────────────────────────────────────────────────────────────────
# DYNAMIC QUESTION LOOP
# ─────────────────────────────────────────────────────────────────────

async def _fill_questions(
    page: Page,
    questions: list[dict],
    llm_answers: dict[str, str | None],
    request: ApplicationRequest,
    temp_files: list[str],
) -> ApplicationResponse | None:
    for q in questions:
        label    = q.get('label', '')
        required = q.get('required', False)
        fields   = q.get('fields', [])

        if label.lower().strip() in STANDARD_FIELD_LABELS:
            continue

        for field in fields:
            name       = field.get('name', '')
            field_type = field.get('type', '')
            values     = field.get('values', [])

            if name.lower() in STANDARD_FIELD_NAMES or field_type == 'input_file':
                continue

            answer = llm_answers.get(name)

            if answer is None and required:
                log.warning(f"Required field unanswered by LLM: '{label}' ({name})")
                await safe_screenshot(page, f'screenshots/gh_unanswered_{name}.png')
                return ApplicationResponse(
                    status='REQUIRES_REVIEW',
                    requiresReview=True,
                    reviewReason='UNKNOWN_QUESTION',
                    step='QUESTION_FILL',
                    data={
                        'questionLabel': label,
                        'questionName':  name,
                        'questionType':  field_type,
                        'screenshotUrl': f'screenshots/gh_unanswered_{name}.png',
                    },
                )

            if answer is None:
                log.debug(f"Optional field skipped (no LLM answer): {name}")
                continue

            selector = f'#{name}'
            try:
                if field_type in ('input_text', 'textarea'):
                    await _fill_text(page, selector, answer)

                elif field_type in ('select', 'multi_value_single_select'):
                    await _fill_select(page, selector, answer, values)

                elif field_type == 'multi_value_multi_select':
                    for option in answer.split(','):
                        await _fill_select(page, selector, option.strip(), values)

                elif field_type == 'boolean':
                    await _fill_boolean(page, name, answer)

                else:
                    log.warning(f"Unknown field type '{field_type}' for {name} — attempting text fill")
                    await _fill_text(page, selector, answer)

            except Exception as e:
                log.warning(f"Failed to fill {name} ({field_type}): {e}")
                continue

    return None


# ─────────────────────────────────────────────────────────────────────
# FIELD TYPE HANDLERS
# ─────────────────────────────────────────────────────────────────────

async def _fill_text(page: Page, selector: str, value: str) -> None:
    if await page.query_selector(selector):
        await scroll_to_element(page, selector)
        await page.fill(selector, '')
        await human_type(page, selector, value)


async def _fill_select(page: Page, selector: str, answer: str, values: list) -> None:
    element = await page.query_selector(selector)
    if not element:
        return

    await scroll_to_element(page, selector)

    # Detect if this is a React Select (input with role="combobox")
    # vs a native <select> element
    tag  = await element.evaluate('el => el.tagName.toLowerCase()')
    role = await element.get_attribute('role') or ''

    if tag == 'input' or role == 'combobox':
        # ── React Select ─────────────────────────────────────────────
        # Click to open the dropdown, then click the matching option.
        # Typing is used as fallback if the option isn't immediately visible.
        await element.click()
        await page.wait_for_timeout(human_delay(400, 700))

        # Try direct option click first (fastest)
        try:
            option_sel = f'.select__option:has-text("{answer}")'
            await page.wait_for_selector(option_sel, timeout=3000)
            await page.click(option_sel)
            log.debug(f"React Select filled: {selector} → {answer}")
            return
        except Exception:
            pass

        # Fallback: type to filter, then click or press Enter
        try:
            await element.type(answer, delay=80)
            await page.wait_for_timeout(human_delay(300, 500))
            option_sel = f'.select__option:has-text("{answer}")'
            await page.wait_for_selector(option_sel, timeout=3000)
            await page.click(option_sel)
            log.debug(f"React Select filled (typed): {selector} → {answer}")
        except Exception:
            await page.keyboard.press('Enter')
            log.warning(f"React Select fallback Enter: {selector} → {answer}")
        return

    # ── Native <select> ───────────────────────────────────────────────
    matched_val = next(
        (v.get('value') for v in values
         if v.get('label', '').lower().strip() == answer.lower().strip()),
        None,
    )
    try:
        if matched_val is not None:
            await page.select_option(selector, value=str(matched_val))
        else:
            await page.select_option(selector, label=answer)
        log.debug(f"Native select filled: {selector} → {answer}")
    except Exception as e:
        log.warning(f"Select fill failed for {selector}: {e}")


async def _fill_boolean(page: Page, name: str, answer: str) -> None:
    ans    = answer.lower().strip()
    is_yes = ans in ('yes', 'true', '1')
    selectors = [f'#{name}_yes', f'#{name}_1'] if is_yes else [f'#{name}_no', f'#{name}_0']

    for sel in selectors:
        try:
            if await page.query_selector(sel):
                await scroll_to_element(page, sel)
                await page.click(sel, force=True)
                await page.wait_for_timeout(human_delay(300, 600))
                log.debug(f"Boolean field set: {sel}")
                return
        except Exception:
            continue

    log.warning(f"Could not find boolean radio for {name} with answer '{answer}'")


# ─────────────────────────────────────────────────────────────────────
# RESUME RESOLUTION
# ─────────────────────────────────────────────────────────────────────

async def _resolve_resume(
    request: ApplicationRequest,
    temp_files: list[str],
) -> str | None:
    if request.cvFilePath and os.path.exists(request.cvFilePath):
        log.debug(f"Using local resume: {request.cvFilePath}")
        return request.cvFilePath

    if request.resumeUrl:
        log.info(f"Downloading resume from: {request.resumeUrl[:80]}...")
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            tmp_path = tmp.name
            tmp.close()

            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30,
                verify=certifi.where(),
            ) as client:
                resp = await client.get(request.resumeUrl)
                resp.raise_for_status()
                with open(tmp_path, 'wb') as f:
                    f.write(resp.content)

            temp_files.append(tmp_path)
            log.info(f"Resume downloaded to temp: {tmp_path}")
            return tmp_path

        except Exception as e:
            log.error(f"Resume download failed: {e}", exc_info=True)
            return None

    log.error("No resume source — resumeUrl and cvFilePath both missing")
    return None


# ─────────────────────────────────────────────────────────────────────
# STANDARD FIELDS
# ─────────────────────────────────────────────────────────────────────

async def _fill_standard_fields(page: Page, request: ApplicationRequest) -> None:
    fields = [
        ('#first_name', request.firstName),
        ('#last_name',  request.lastName),
        ('#email',      request.email),
        ('#phone',      request.phone or ''),
    ]
    for selector, value in fields:
        if value and await page.query_selector(selector):
            await human_type(page, selector, value)


async def _handle_gdpr(page: Page) -> None:
    for selector in ['#gdpr_consent_given_1', '[id*="gdpr"]', '[name*="gdpr"]']:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                await scroll_to_element(page, selector)
                await page.check(selector, force=True)
                await page.wait_for_timeout(human_delay(300, 500))
                log.info(f"GDPR checked: {selector}")
                return
        except Exception:
            continue


# ─────────────────────────────────────────────────────────────────────
# CLEANUP
# ─────────────────────────────────────────────────────────────────────

def _cleanup_temp_files(temp_files: list[str]) -> None:
    for path in temp_files:
        try:
            if os.path.exists(path):
                os.remove(path)
                log.debug(f"Temp file deleted: {path}")
        except Exception as e:
            log.warning(f"Could not delete temp file {path}: {e}")