import pytest

from pages.login import login
from pages.reconciliation import reconciliation

@pytest.mark.smoke
def test_reconciliation(page):
    a=login(page)
    a.login_page()
    b=reconciliation(page)
    b.reconciliation_page()
