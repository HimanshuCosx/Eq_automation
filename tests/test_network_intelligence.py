import allure
import pytest

from pages.network_intelligence import network_intelligence


@allure.feature("Network Intelligence")
@allure.story("Network Intelligence page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Network Intelligence: the refresh control, the eight KPI tiles with "
    "their period deltas and tooltips, the trend and uptime-distribution "
    "charts, the four reliability panels, the at-risk watchlist with its "
    "score explainer, expand toggle and charger links, the site coverage "
    "table with its scope selector, search and empty state, the "
    "sub-organisation filter, the date-range presets and custom calendar, row "
    "expansion and pagination, the alert history's filters, empty state, "
    "evidence and pager, and the site and charger pages reached from the table"
)
@pytest.mark.smoke
def test_network_intelligence(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = network_intelligence(page)
    b.network_intelligence_page()


@allure.feature("Network Intelligence")
@allure.story("Alert history")
@allure.severity(allure.severity_level.NORMAL)
@allure.title(
    "Network Intelligence: no alert episode is marked Resolved at a time "
    "that has not happened yet"
)
def test_network_intelligence_alert_resolution_times(page):
    # Known product bug -- see `check_alert_resolution_times`.
    b = network_intelligence(page)
    b.alert_resolution_times_page()
