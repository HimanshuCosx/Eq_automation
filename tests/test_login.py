import pytest


@pytest.mark.smoke
def test_login(page):
    # Login is performed once by the session `page` fixture (see conftest.py).
    # Here we just confirm the session is authenticated (no longer on /login).
    assert "/login" not in page.url
