"""
browser_utils.py
────────────────────────────────────────────────────────────────────
Shared Playwright utilities used by all ATS handlers.

These helpers exist to:
  - Mimic human interaction patterns (random delays, smooth scrolling)
  - Make automation more resilient to timing issues
  - Keep ATS handler code clean — handlers focus on WHAT to fill,
    utils handle HOW to interact with the page
────────────────────────────────────────────────────────────────────
"""

import random
import logging
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

log = logging.getLogger(__name__)


def human_delay(min_ms: int = 300, max_ms: int = 800) -> int:
    """Random ms delay to mimic human pause between actions."""
    return random.randint(min_ms, max_ms)


async def safe_screenshot(page: Page, path: str) -> None:
    """
    Take a screenshot safely — never let a screenshot failure
    crash the automation. Evidence capture is best-effort.
    """
    try:
        await page.screenshot(path=path, full_page=True)
        log.debug(f"Screenshot saved: {path}")
    except Exception as e:
        log.warning(f"Screenshot failed (non-fatal): {e}")


async def scroll_to_element(page: Page, selector: str) -> None:
    """
    Scroll an element into the center of the viewport smoothly
    before interacting with it. Prevents clicks on hidden elements.
    """
    try:
        await page.eval_on_selector(
            selector,
            "el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })"
        )
        await page.wait_for_timeout(human_delay(400, 700))
    except Exception:
        pass


async def human_type(page: Page, selector: str, text: str) -> None:
    """
    Scroll field into view → click → type character by character
    with random per-keystroke delays to mimic human typing speed.

    More resilient than page.fill() on React/Vue controlled inputs
    because it fires real keyboard events the framework listens to.
    """
    if not text:
        return

    await scroll_to_element(page, selector)
    await page.click(selector)
    await page.wait_for_timeout(human_delay(200, 400))

    for char in text:
        await page.type(selector, char, delay=random.randint(60, 180))

    await page.wait_for_timeout(human_delay(200, 400))


async def human_type_locator(page: Page, locator, text: str) -> None:
    """
    Same as human_type but accepts a Playwright Locator instead of a CSS selector.
    Used when element is identified by role, label, or other locator strategies.
    """
    if not text:
        return

    await locator.scroll_into_view_if_needed()
    await locator.click()
    await page.wait_for_timeout(human_delay(200, 400))

    for char in text:
        await locator.type(char, delay=random.randint(60, 180))

    await page.wait_for_timeout(human_delay(200, 400))


async def select_react_option(page: Page, input_selector: str, option_text: str) -> None:
    """
    Interact with a React Select dropdown:
      1. Scroll into view and click to open
      2. Type to filter options
      3. Click the matching option or press Enter as fallback
    """
    await scroll_to_element(page, input_selector)
    await page.click(input_selector)
    await page.wait_for_timeout(human_delay(300, 600))

    for char in option_text:
        await page.type(input_selector, char, delay=random.randint(60, 150))

    await page.wait_for_timeout(human_delay(400, 700))

    try:
        await page.click(f'.select__option:has-text("{option_text}")', timeout=4000)
    except PlaywrightTimeoutError:
        log.warning(f"React Select option '{option_text}' not found by text — pressing Enter")
        await page.keyboard.press('Enter')

    await page.wait_for_timeout(human_delay(300, 500))


async def upload_file(page: Page, selector: str, file_path: str) -> None:
    """
    Scroll a file input into view and set the file.
    Waits briefly after upload to allow the page to process it.
    """
    await scroll_to_element(page, selector)
    await page.set_input_files(selector, file_path)
    await page.wait_for_timeout(human_delay(800, 1400))


async def check_for_captcha(page: Page) -> bool:
    """
    Detect CAPTCHA challenges that are actually visible and require interaction.
    Returns True only for real challenges — NOT the reCAPTCHA v3 badge.

    The reCAPTCHA v3 badge is a small floating icon (~70px wide) that shows
    "Protected by reCAPTCHA" on hover. It is purely decorative and never
    requires user interaction. We filter it out using a bounding box size check:
    a real reCAPTCHA checkbox widget is ~300px wide, the badge is ~70px.

    Checks for:
      - reCAPTCHA challenge iframe (bframe = actual challenge popup)
      - reCAPTCHA checkbox iframe that is visible AND large enough
      - hCaptcha iframe that is visible AND large enough
      - Cloudflare Turnstile that is visible AND large enough
    """

    # ── Step 1: Hide the reCAPTCHA v3/enterprise floating badge ─────────────
    # The badge (.grecaptcha-badge) is a 256px wide div positioned at right:-186px
    # so only ~70px is visible as a floating tab. It is purely decorative —
    # reCAPTCHA invisible/enterprise runs silently with NO user interaction.
    # Hiding it before selector checks ensures none of our iframe selectors
    # accidentally match the anchor iframe inside it.
    try:
        await page.evaluate("""
            const badge = document.querySelector('.grecaptcha-badge');
            if (badge) badge.style.display = 'none';
        """)
    except Exception:
        pass

    # ── Step 2: Check for real challenge iframes ──────────────────────────
    # bframe only appears during an actual challenge popup — never for the badge
    challenge_selectors = [
        'iframe[src*="recaptcha/api2/bframe"]',
        'iframe[src*="recaptcha/enterprise/bframe"]',
        'iframe[src*="hcaptcha.com/captcha"]',
    ]

    for selector in challenge_selectors:
        try:
            element = await page.query_selector(selector)
            if element:
                log.warning(f"CAPTCHA challenge detected via selector: {selector}")
                return True
        except Exception:
            continue

    # ── Step 3: Check visible CAPTCHA widgets ─────────────────────────────
    # These are real checkbox widgets — not background scoring iframes.
    # The badge has already been hidden so these selectors won't match it.
    visible_selectors = [
        ('iframe[src*="recaptcha/api2/anchor"]', 'reCAPTCHA checkbox'),
        ('iframe[src*="recaptcha/enterprise/anchor"]', 'reCAPTCHA enterprise checkbox'),
        ('iframe[src*="hcaptcha"]', 'hCaptcha'),
        ('iframe[src*="turnstile"]', 'Cloudflare Turnstile'),
        ('.g-recaptcha', 'reCAPTCHA widget'),
        ('.h-captcha', 'hCaptcha widget'),
    ]

    for selector, name in visible_selectors:
        try:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                box = await element.bounding_box()
                if box and box['width'] > 100:
                    log.warning(f"CAPTCHA detected (visible, width={box['width']}px): {name}")
                    return True
        except Exception:
            continue

    # NOTE: [data-sitekey] check removed — Greenhouse embeds this in hidden
    # script tags that appear visible to the DOM but require no interaction

    return False


async def detect_success(page: Page) -> bool:
    """
    Check if the current page state indicates a successful submission.
    Checks both URL patterns and page content for confirmation signals.
    """
    try:
        current_url = page.url.lower()
        page_content = (await page.content()).lower()

        url_signals = ['confirmation', 'success', 'thank-you', 'thankyou', 'applied']
        content_signals = [
            'thank you', 'application submitted', 'successfully submitted',
            'we received your application', 'application complete',
        ]

        return (
            any(s in current_url for s in url_signals) or
            any(s in page_content for s in content_signals)
        )
    except Exception:
        return False


async def collect_form_errors(page: Page) -> list[str]:
    """
    Scrape visible form validation errors from the page.
    Used to construct meaningful failure reasons.
    """
    errors = []

    error_selectors = [
        '.helper-text--error',
        '.error-message',
        '.field-error',
        '[aria-invalid="true"]',
        '.validation-error',
        '[class*="error"]',
    ]

    for selector in error_selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                text = await el.inner_text()
                text = text.strip()
                if text and text not in errors:
                    errors.append(text)
        except Exception:
            continue

    return errors