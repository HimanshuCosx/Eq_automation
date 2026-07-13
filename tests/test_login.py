import allure
import pytest


@allure.feature("Authentication")
@allure.story("Login")
@allure.severity(allure.severity_level.BLOCKER)
@allure.title("User can log in and reach the dashboard")
@pytest.mark.smoke
def test_login(page):
    # Login is performed once by the session `page` fixture (see conftest.py).
    # Here we just confirm the session is authenticated (no longer on /login).
    with allure.step("Verify the session is authenticated"):
        assert "/login" not in page.url
