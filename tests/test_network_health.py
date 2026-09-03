import allure
import pytest

from pages.network_health import network_health


@allure.feature("Network Health")
@allure.story("Network Health page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Network Health: the eight KPI tiles and their tooltips, the trend and "
    "uptime-distribution charts, the four reliability panels, the at-risk "
    "watchlist with its per-charger score explainer and expand toggle, the "
    "site coverage table with search and its empty state, the "
    "sub-organisation filter and the date-range presets, row expansion and "
    "pagination, the alert history filters and empty state, and the site and "
    "charger pages reached from the table"
)
@pytest.mark.smoke
def test_network_health(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = network_health(page)
    b.network_health_page()
