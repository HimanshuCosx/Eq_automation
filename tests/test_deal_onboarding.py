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
    # Kept apart from the main workflow so a sorting regression is reported
    # on its own. This used to be a strict xfail: sorting was accepted (URL
    # and chevron updated) but the rows never reordered. Staging now applies
    # it, so the marker was removed and it runs as a plain test.
    b = deal_onboarding(page)
    b.open_page()
    b.sort_columns()
