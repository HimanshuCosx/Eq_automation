import allure
import pytest

from pages.repository import repository


@allure.feature("Repository")
@allure.story("Repository page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Repository: search, category tabs, list-view table (columns, sorting, "
    "in-table filtering), sub-org filter, details panel, download and pagination"
)
@pytest.mark.smoke
def test_repository(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = repository(page)
    b.repository_page()
