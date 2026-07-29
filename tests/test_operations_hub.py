import allure
import pytest

from pages.operations_hub import operations_hub


@allure.feature("Operations Hub")
@allure.story("Operations Hub page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Operations Hub: CPO and Sites list views (columns, search, sorting, "
    "pagination), CPO drill-down with filters and row expansion, site detail "
    "(Site Info / Tracker / Records categories / Maintenance: sub-tabs, plan "
    "cards, creating a maintenance plan and an event, and an edit-and-restore "
    "round trip), breadcrumb navigation, and the Map View (legend, markers, "
    "filters, zoom)"
)
@pytest.mark.smoke
def test_operations_hub(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = operations_hub(page)
    b.operations_hub_page()
