import pytest

from pages.login import login
from pages.self_billing import self_billing


@pytest.mark.smoke
def test_self_billing(page):
    a = login(page)
    a.login_page()
    b = self_billing(page)
    b.self_billing_page()
