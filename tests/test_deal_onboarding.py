import allure
import pytest

from pages.deal_onboarding import deal_onboarding


@allure.feature("Deal Onboarding")
@allure.story("Deal Onboarding page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Deal Onboarding: table structure, row expansion, search and empty state, "
    "CPO / deal-type / criticality filters (applied, combined and cleared), "
    "pagination, the deal page (header, records, site overview, edit form, "
    "Add Record dialog) and the Add Deal modal (HubSpot queue and manual form)"
)
@pytest.mark.smoke
def test_deal_onboarding(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = deal_onboarding(page)
    b.deal_onboarding_page()


@allure.feature("Deal Onboarding")
@allure.story("Deal Onboarding column sorting")
@allure.severity(allure.severity_level.NORMAL)
@allure.title(
    "Deal Onboarding: every sortable column reorders the table in both "
    "directions"
)
def test_deal_onboarding_sorting(page):
    # Split out from the main workflow because it currently fails on the
    # product, not on the automation: clicking a sortable header updates the
    # URL and the chevron but the rows never reorder, in either direction, for
    # any of the four sortable columns. Kept as a real assertion so the gap
    # stays visible and starts passing on its own once sorting is wired up.
    b = deal_onboarding(page)
    b.open_page()
    b.sort_columns()
