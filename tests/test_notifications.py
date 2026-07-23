import allure
import pytest

from pages.notifications import notifications


@allure.feature("Notifications")
@allure.story("Notifications page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Notifications: tabs, search, category and date filters, item navigation, "
    "pagination and mark as read"
)
@pytest.mark.smoke
def test_notifications(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = notifications(page)
    b.notifications_page()
