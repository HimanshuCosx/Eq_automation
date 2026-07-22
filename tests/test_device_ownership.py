import allure
import pytest

from pages.device_ownership import device_ownership


@allure.feature("Device Ownership")
@allure.story("Device Ownership page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("Device Ownership: search, filter, paginate and correct device ownership")
@pytest.mark.smoke
def test_device_ownership(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = device_ownership(page)
    b.device_ownership_page()
