import allure
import pytest

from pages.tracker_templates import tracker_templates


@allure.feature("Tracker Templates")
@allure.story("Tracker Templates page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("Tracker Templates: search, filter, create, edit and delete a template")
@pytest.mark.smoke
def test_tracker_templates(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = tracker_templates(page)
    b.tracker_templates_page()
