import pytest

from pages.login import login

@pytest.mark.smoke
def test_login(page):
    a=login(page)
    a.login_page()