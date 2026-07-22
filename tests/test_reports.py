import allure
import pytest

from pages.reports import reports


@allure.feature("Reports")
@allure.story("Reports page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("Reports: search, filter, browse tabs, view, create, verify and delete a report")
@pytest.mark.smoke
def test_reports(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = reports(page)
    b.reports_page()
