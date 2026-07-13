import pytest

from pages.reconciliation import reconciliation


@pytest.mark.smoke
def test_reconciliation(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = reconciliation(page)
    b.reconciliation_page()
