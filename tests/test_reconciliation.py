import allure
import pytest

from pages.reconciliation import reconciliation


@allure.feature("Reconciliation")
@allure.story("Reconciliation page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("Reconciliation: filter, search and drill into CPO sessions")
@pytest.mark.smoke
def test_reconciliation(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = reconciliation(page)
    b.reconciliation_page()
