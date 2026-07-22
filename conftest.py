import base64
import logging

import allure
import pytest
from playwright.sync_api import sync_playwright

from config import BASE_URL
from pages.login import login

log = logging.getLogger("eq_automation")


@pytest.fixture(scope="session")
def page():
    log.info("Starting Playwright and launching Chromium (headed)")
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    # browser = p.chromium.launch(headless=True)

    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()

    log.info("Opening application: %s", BASE_URL)
    page.goto(BASE_URL)
    page.wait_for_load_state("load")

    # Log in once for the whole session so ANY test file can be run on its own
    # (e.g. `pytest tests/test_reconciliation.py`) and still start authenticated.
    log.info("Logging in for the shared session")
    login(page).login_page()
    log.info("Login complete, session ready at: %s", page.url)

    yield page

    log.info("Tearing down browser session")
    context.close()
    browser.close()
    p.stop()


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
