import pytest
from playwright.sync_api import sync_playwright
from config import BASE_URL
from pages.login import login


@pytest.fixture(scope="session")
def page():
    p = sync_playwright().start()
    browser=p.chromium.launch(headless=False)
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()

    page.goto(BASE_URL)
    page.wait_for_load_state("load")

    # Log in once for the whole session so ANY test file can be run on its own
    # (e.g. `pytest tests/test_reconciliation.py`) and still start authenticated.
    login(page).login_page()

    yield page

    context.close()
    browser.close()
    p.stop()