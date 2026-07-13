import pytest

from pages.self_billing import self_billing


@pytest.mark.smoke
def test_self_billing(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = self_billing(page)
    b.self_billing_page()
