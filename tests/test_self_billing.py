import allure
import pytest

from pages.self_billing import self_billing


@allure.feature("Self Billing")
@allure.story("Self Billing page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("Self Billing: period picker, search and pagination")
@pytest.mark.smoke
def test_self_billing(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = self_billing(page)
    b.self_billing_page()
