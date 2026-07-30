import allure
import pytest

from pages.driver_dashboard import driver_dashboard


@allure.feature("Driver Dashboard")
@allure.story("Driver Dashboard page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Driver Dashboard: summary tiles and tooltips, table columns, status / "
    "driver-type / date-range filters, search and empty state, column sorting, "
    "pagination, and the Map view (metric, display, legend, zoom)"
)
@pytest.mark.smoke
def test_driver_dashboard(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = driver_dashboard(page)
    b.driver_dashboard_page()
