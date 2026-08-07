import allure
import pytest

from pages.maintenance import maintenance


@allure.feature("Maintenance")
@allure.story("Maintenance page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Maintenance: event table structure and status-driven row actions, all "
    "four headline tiles (counts, tooltips, filtering and release), search and "
    "empty state, the category / status / deal-type filters (applied via "
    "Apply, multi-selected, combined with a tile and cleared), sorting, all "
    "four page sizes and the pager, the Add event / Edit event / Schedule / "
    "Mark complete panels (validated, never submitted), the month-week-day "
    "calendar and its visit dialog, and the plans tab (cards, cadence, "
    "overflow menu, search, plan editor and drill-through to events)"
)
@pytest.mark.smoke
def test_maintenance(page):
    # `page` is already logged in via the shared session (see conftest.py).
    #
    # Read-only throughout. Every row on this page carries a Delete control and
    # the page also offers Schedule, Mark complete, Edit event, Edit plan,
    # Pause plan and Cancel plan -- all one-way from this UI. Delete is never
    # clicked at all; the rest are opened, checked on their fields and guards,
    # and dismissed with Cancel. See the class docstring in
    # pages/maintenance.py for the full rationale.
    b = maintenance(page)
    b.maintenance_page()


# --------------------------------------------------------------------------- #
# Split-out checks
#
# These are separated from the main workflow only because each drives the page
# into a state the workflow would otherwise have to unwind -- a sort applied
# across several columns, and a walk through the plans pager. Both pass; they
# are ordinary coverage, not known gaps.
# --------------------------------------------------------------------------- #


@allure.feature("Maintenance")
@allure.story("Maintenance event sorting")
@allure.severity(allure.severity_level.NORMAL)
@allure.title(
    "Maintenance: the Due Date column really reorders the rows in both "
    "directions, not merely the URL"
)
def test_maintenance_sorting_reorders(page):
    # `sort_columns` in the main workflow pins the sorting *contract* -- which
    # columns sort, what each sends, and that a third click clears it. This
    # goes further and checks the rows actually come back in the order the
    # column was sorted on.
    #
    # Due Date only: the Status column's displayed label is derived at render
    # time rather than stored, so correct sorting legitimately produces an
    # interleaved-looking column. See sort_columns_reorder in
    # pages/maintenance.py for the full reasoning.
    b = maintenance(page)
    b.open_page()
    b.sort_columns_reorder()


@allure.feature("Maintenance")
@allure.story("Maintenance plans pagination")
@allure.severity(allure.severity_level.NORMAL)
@allure.title(
    "Maintenance: the plans tab pages through every plan it counts, with no "
    "plan repeated between pages"
)
def test_maintenance_plans_pagination(page):
    b = maintenance(page)
    b.open_page()
    b._park_mouse()
    b.plans_tab.first.click()
    assert b._poll(lambda: "tab=plans" in b.page.url, timeout_ms=25000), (
        f"the plans tab did not reach the URL: {b.page.url}"
    )
    b.plans_tab_pagination()
