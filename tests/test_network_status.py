import allure
import pytest

from pages.network_status import network_status


@allure.feature("Network Status")
@allure.story("Network Status page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Network Status: socket-status tiles and tooltips, table columns, search "
    "and empty state, column sorting, the sub-org / CPO / availability "
    "multi-select filters, the site -> device -> socket expand hierarchy, "
    "alert badges and pagination"
)
@pytest.mark.smoke
def test_network_status(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = network_status(page)
    b.network_status_page()
