import allure
import pytest

from pages.device_ownership import device_ownership


@allure.feature("Device Ownership")
@allure.story("Device Ownership page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Device Ownership: table columns, search (name / CPMS ID / empty state), "
    "column sorting, CPO / Site / verification filters, pagination, edit-dialog "
    "validation and a real ownership correction with revert"
)
@pytest.mark.smoke
def test_device_ownership(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = device_ownership(page)
    b.device_ownership_page()
