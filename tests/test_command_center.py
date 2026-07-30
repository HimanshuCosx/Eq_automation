import allure
import pytest

from pages.command_center import command_center


@allure.feature("Command Center")
@allure.story("Command Center page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Command Center: hero KPI tiles and tooltips, criticality triage, "
    "availability and OCPP panels, Refresh, the Ownership / CPO / time-window "
    "filters, the Network Command Map, and the alert feed drill-outs"
)
@pytest.mark.smoke
def test_command_center(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = command_center(page)
    b.command_center_page()
