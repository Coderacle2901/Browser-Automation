import os
import logging
from playwright.async_api import async_playwright

from models import ApplicationRequest, ApplicationResponse
from handlers.greenhouse import run_greenhouse
from handlers.lever import run_lever
from handlers.generic import run_generic

log = logging.getLogger(__name__)

SCREENSHOT_DIR = 'screenshots'
USER_DATA_DIR = os.path.join(os.getcwd(), 'browser_profile')

# Stealth script applied to every page via context.add_init_script
# Patches the key navigator properties that headless detection relies on
STEALTH_SCRIPT = """
    () => {
        // Hide webdriver flag — the most obvious automation tell
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });

        // Fake a real Chrome runtime object
        window.chrome = {
            runtime: {
                id: undefined,
                connect: () => {},
                sendMessage: () => {},
            },
        };

        // Fake plugins array (headless has 0 plugins)
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [1, 2, 3, 4, 5];
                arr.__proto__ = navigator.__proto__;
                return arr;
            },
        });

        // Fake language array
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });

        // Fix permissions API (headless returns 'denied' for notifications)
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);

        // Fake WebGL vendor/renderer to look like a real GPU
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        };
    }
"""


async def run_automation(request: ApplicationRequest) -> ApplicationResponse:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    os.makedirs(USER_DATA_DIR, exist_ok=True)

    log.info(
        f"Automation starting | platform={request.platform} | "
        f"email={request.email} | url={request.jobUrl}"
    )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=request.headless,
            slow_mo=100,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--start-maximized',
            ],
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
           viewport=None,
        )

        # Apply stealth patches at context level — inherited by every page
        await context.add_init_script(STEALTH_SCRIPT)

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            result = await _dispatch(page, request)
            log.info(f"Automation complete | status={result.status}")
            return result

        except Exception as e:
            log.error(f"Top-level automation error: {e}", exc_info=True)
            return ApplicationResponse(
                status='FAILED',
                error=f'Unexpected automation error: {str(e)}',
                step='DISPATCH',
            )

        finally:
            await context.close()


async def _dispatch(page, request: ApplicationRequest) -> ApplicationResponse:
    platform = (request.platform or '').lower().strip()

    if platform == 'greenhouse':
        log.info("Routing to Greenhouse handler")
        return await run_greenhouse(page, request)

    if platform == 'lever':
        log.info("Routing to Lever handler")
        return await run_lever(page, request)

    log.info(f"Routing to Generic handler (platform='{platform}')")
    return await run_generic(page, request)