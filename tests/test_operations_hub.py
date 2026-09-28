import allure
import pytest

from pages.operations_hub import operations_hub


@allure.feature("Operations Hub")
@allure.story("Operations Hub page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Operations Hub: CPO list (columns, CPO/site search, sorting, pagination, "
    "CPO / Deal Type / Status / Criticality filters), in-place CPO -> site -> "
    "panel row expansion and Expand/Collapse all, site detail (Site Info / "
    "Tracker / Records categories), the CPO sites page (filters, row expansion) "
    "and its Maintenance panel (sub-tabs, plan cards, and the Create Plan / "
    "Edit Plan / Create Event forms, validated and never submitted), "
    "breadcrumb navigation, and the Map View (legend, markers, filters, zoom)"
)
@pytest.mark.smoke
def test_operations_hub(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = operations_hub(page)
    b.operations_hub_page()
