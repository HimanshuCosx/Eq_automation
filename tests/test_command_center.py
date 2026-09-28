import allure
import pytest

from pages.command_center import command_center


@allure.feature("Command Center")
@allure.story("Command Center page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Command Center: hero KPI tiles and every panel tooltip, criticality "
    "triage, fault age, availability and OCPP panels, the Ownership / CPO / "
    "time-window filters, the geographic distribution map, the Needs action "
    "feed with its tabs and drill-outs, and the onboarding pipeline"
)
@pytest.mark.smoke
def test_command_center(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = command_center(page)
    b.command_center_page()


@allure.feature("Command Center")
@allure.story("Hero KPI tiles")
@allure.severity(allure.severity_level.NORMAL)
@allure.title(
    "Command Center: the Live Sites 'Across N CPOs' caption follows the "
    "Ownership filter"
)
def test_command_center_live_sites_caption_follows_ownership(page):
    # Known product bug -- see `check_live_sites_caption_under_ownership`.
    b = command_center(page)
    b.live_sites_caption_page()
