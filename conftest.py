import os
import pytest
from playwright.sync_api import sync_playwright
from config import BASE_URL

# Chrome is visible by default (for you). Set HEADLESS=1 to hide it.
HEADLESS = os.getenv("HEADLESS", "0") == "1"


@pytest.fixture(scope="function")
def page(request):
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=HEADLESS)
    # browser=p.firefox.launch(headless=False)
    # browser=p.webkit.launch(headless=False)
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()

    page.goto(BASE_URL)
    page.wait_for_load_state("load")
    
    
    yield page

    context.close()
    browser.close()
    p.stop()
