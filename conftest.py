import base64
import logging
import os
import time

import allure
import pytest
from playwright.sync_api import sync_playwright

from config import BASE_URL
from pages.login import login

log = logging.getLogger("eq_automation")

# How long to give the app shell to render, and how many full reloads to spend
# before declaring the environment unusable. See `_wait_for_app_shell`.
SHELL_TIMEOUT_S = 40
SHELL_RELOADS = 2


def _shell_ready(page):
    """True once the sidebar navigation has rendered.

    The sidebar is the first thing every page object reaches for, so its
    presence is the honest definition of "the app is usable" -- a URL alone is
    not, because the app sits on its landing route showing "Loading your
    dashboard" for as long as its bootstrap call is outstanding.
    """
    try:
        nav = page.get_by_role("link", name="Command Center")
        return nav.count() > 0 and nav.first.is_visible()
    except Exception:
        # The page can be mid-navigation; treat that as "not ready yet".
        return False


def _wait_for_app_shell(page):
    """Block until the app has actually booted, reloading if it stalls.

    Staging's `/api/config` intermittently returns a 504 after ~60s. The app
    does not retry it: it simply renders "Loading your dashboard" forever, with
    no error and no console output. Because the browser session is shared
    across the whole suite (see the `page` fixture), one stalled boot used to
    take every test down with it -- each failing on its own opaque 30s
    `Locator.click` timeout, roughly six wasted minutes and no clue as to why.

    A fresh page load re-requests the config, and the failure is intermittent,
    so a couple of reloads recovers it in practice. If it still has not booted
    after that, the run stops here with a message naming the actual cause
    rather than letting every test rediscover it one timeout at a time.
    """
    for attempt in range(SHELL_RELOADS + 1):
        deadline = time.monotonic() + SHELL_TIMEOUT_S
        while time.monotonic() < deadline:
            if _shell_ready(page):
                log.info("App shell ready at: %s", page.url)
                return
            page.wait_for_timeout(500)

        if attempt < SHELL_RELOADS:
            log.warning(
                "The app did not finish booting within %ss (still showing %r "
                "at %s) -- reloading (%s of %s)",
                SHELL_TIMEOUT_S,
                (page.locator("body").inner_text() or "")[:40].strip(),
                page.url, attempt + 1, SHELL_RELOADS,
            )
            try:
                page.reload(wait_until="load")
            except Exception as exc:
                log.warning("Reload failed: %s", exc)

    pytest.fail(
        "The application never finished booting, so no test could have run.\n"
        f"After {SHELL_RELOADS + 1} attempt(s) of up to {SHELL_TIMEOUT_S}s "
        f"each, {BASE_URL} is still showing "
        f"{(page.locator('body').inner_text() or '')[:60].strip()!r} with no "
        "sidebar.\n"
        "This is an environment fault rather than a test fault: staging's "
        "/api/config intermittently returns a 504 and the app waits on it "
        "indefinitely. Re-run once staging is healthy.",
        pytrace=False,
    )


@pytest.fixture(scope="session")
def page():
    # Headed locally by default; set HEADLESS=true (e.g. in CI) to run headless.
    headless = os.getenv("HEADLESS", "false").lower() == "true"
    log.info("Starting Playwright and launching Chromium (headless=%s)", headless)
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=headless)

    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()

    log.info("Opening application: %s", BASE_URL)
    page.goto(BASE_URL)
    page.wait_for_load_state("load")

    # Log in once for the whole session so ANY test file can be run on its own
    # (e.g. `pytest tests/test_reconciliation.py`) and still start authenticated.
    log.info("Logging in for the shared session")
    login(page).login_page()
    log.info("Login complete, now at: %s", page.url)

    # Leaving /login only means the credentials were accepted -- the app shell
    # is fetched separately and can stall indefinitely. Every test drives the
    # sidebar, so the session is not "ready" until that exists.
    _wait_for_app_shell(page)
    log.info("Session ready at: %s", page.url)

    yield page

    log.info("Tearing down browser session")
    context.close()
    browser.close()
    p.stop()


@pytest.fixture(autouse=True)
def _app_shell(page):
    """Make sure the app is still standing before each test starts.

    Staging does not only fail at start-up: it drops out mid-run, and when it
    does the whole SPA unmounts -- the sidebar disappears, and because the
    browser session is shared every remaining test fails on
    `waiting for get_by_role("link", ...)` with no indication that the cause is
    environmental rather than a broken locator.

    Re-checking here costs one locator lookup when the app is healthy, and
    recovers it with a reload when it is not, so a blip during test three no
    longer condemns tests four onwards. A test that genuinely cannot get a
    working app still fails, but with the reason spelled out.
    """
    if not _shell_ready(page):
        log.warning("The app shell is missing before this test -- recovering")
        _wait_for_app_shell(page)
    yield


# --------------------------------------------------------------------------- #
# Reporting hooks (HTML + Allure)
# --------------------------------------------------------------------------- #

def pytest_html_report_title(report):
    """Title shown at the top of the pytest-html report."""
    report.title = "Equidria OS – Automation Test Report"


def pytest_configure(config):
    """Add environment info to the HTML report header (shown to reviewers)."""
    metadata = getattr(config, "_metadata", None)
    if metadata is not None:
        metadata["Application"] = "Equidria OS (Staging)"
        metadata["Base URL"] = BASE_URL
        metadata["Browser"] = "Chromium (headed)"


def _attach_screenshot(item, when):
    """Grab a screenshot from the shared page and attach it to both reports."""
    page = item.funcargs.get("page")
    if page is None:
        return None
    try:
        image = page.screenshot(full_page=True)
    except Exception:
        return None

    # Allure: rich attachment in the step timeline.
    allure.attach(
        image,
        name=f"{item.name}-{when}",
        attachment_type=allure.attachment_type.PNG,
    )
    # pytest-html: return base64 so it embeds in the self-contained HTML file.
    return base64.b64encode(image).decode("ascii")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Capture a screenshot on failure and embed it in the HTML report."""
    outcome = yield
    report = outcome.get_result()

    if report.when != "call":
        return

    if report.failed:
        image_b64 = _attach_screenshot(item, "failure")
        if image_b64:
            try:
                from pytest_html import extras

                report.extras = getattr(report, "extras", [])
                report.extras.append(
                    extras.image(image_b64, mime_type="image/png")
                )
            except Exception:
                pass
