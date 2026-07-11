import os
import pytest
from playwright.sync_api import sync_playwright
from config import BASE_URL


@pytest.fixture(scope="function")
def page(request):
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
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





# HEADLESS = os.getenv("HEADLESS", "0") == "1"

# # Which browser engines to run every test against.
# # Override with e.g. BROWSERS="chromium,firefox" to narrow the run.
# BROWSERS = [b.strip() for b in os.getenv("BROWSERS", "chromium,firefox,webkit").split(",") if b.strip()]


# @pytest.fixture(scope="function", params=BROWSERS, ids=lambda b: b)
# def page(request):
#     browser_name = request.param

#     p = sync_playwright().start()
#     browser_type = getattr(p, browser_name)
#     browser = browser_type.launch(headless=HEADLESS)
#     context = browser.new_context(ignore_https_errors=True)
#     page = context.new_page()

#     page.goto(BASE_URL)
#     page.wait_for_load_state("load")

#     yield page

#     context.close()
#     browser.close()
#     p.stop()