import re

import allure
import pytest

from pages.site_cost_data import SITE, site_cost_data


@allure.feature("Site Cost Data")
@allure.story("Site Cost Data page workflow")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Site Cost Data: table structure and cost-value formats, search and empty "
    "state, CPO and sub-organisation filters (applied, toggled off and cleared "
    "together), sorting on every working column, all four page sizes and the "
    "pager, the per-site activity trail, and the site cost page (breadcrumb, "
    "dated cost history, per-device breakdown, inline editor and Add Data "
    "dialog)"
)
@pytest.mark.smoke
def test_site_cost_data(page):
    # `page` is already logged in via the shared session (see conftest.py).
    b = site_cost_data(page)
    b.site_cost_data_page()


# --------------------------------------------------------------------------- #
# Known product gaps
#
# Each of these is a real defect in the application rather than in the
# automation, confirmed by hand against staging. Each is kept as a genuine
# assertion -- written for the behaviour the UI advertises, not for the
# behaviour it currently has -- so the gap stays described in the report rather
# than being quietly normalised into the expected result.
#
# They are marked `xfail(strict=True)` so a run is green while they are
# outstanding. Strict matters: the moment the product is fixed the test starts
# passing, pytest reports XPASS *as a failure*, and whoever sees it removes the
# marker. A plain xfail would let a fix land silently and leave the page
# permanently unguarded against the bug coming back.
#
# See the matching methods in pages/site_cost_data.py for the full description
# of each, including how it presents on screen.
# --------------------------------------------------------------------------- #


@allure.feature("Site Cost Data")
@allure.story("Known gap: sorting by site name")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Site Cost Data: sorting by the Sites column does not order the table by "
    "name -- sort_by=name is accepted and written to the URL but the rows come "
    "back unsorted (and sometimes empty), where every other sortable column "
    "reorders correctly"
)
@pytest.mark.xfail(
    strict=True,
    reason="Product bug: sort_by=name is not honoured. The Sites header takes "
           "the click, writes sort_by=name&sort_order=asc to the URL and flips "
           "its chevron, but the rows return in their unsorted order; it has "
           "also been seen to return the table completely empty under the 'No "
           "site cost data' state. Every other sortable column works.",
)
def test_site_cost_data_sort_by_name(page):
    b = site_cost_data(page)
    b.open_page()
    b.sort_by_site_name()


@allure.feature("Site Cost Data")
@allure.story("Known gap: footer and pager ignore the filters")
@allure.severity(allure.severity_level.NORMAL)
@allure.title(
    "Site Cost Data: applying a CPO filter narrows the rows but the footer "
    "still counts the whole unfiltered table and the pager still offers its "
    "pages"
)
@pytest.mark.xfail(
    strict=True,
    reason="Product bug: the footer and pager are built from the unfiltered "
           "result set. With the 'Mulberry Homes' CPO filter applied the table "
           "correctly draws its 2 sites, but the footer still reads 'Showing "
           "1-20 of 117' and the pager still offers a page for every one of "
           "the 117 -- so the user is told there are 115 more results than "
           "exist, and paging forward lands on pages the filter has emptied.",
)
def test_site_cost_data_footer_reflects_filter(page):
    b = site_cost_data(page)
    b.open_page()
    b.footer_reflects_filter()


@allure.feature("Site Cost Data")
@allure.story("Known gap: default page size")
@allure.severity(allure.severity_level.NORMAL)
@allure.title(
    "Site Cost Data: a first load draws 15 rows while its 'Rows per page' "
    "control and footer both claim 20"
)
@pytest.mark.xfail(
    strict=True,
    reason="Product bug: on a first load, with no page_size in the URL, the "
           "'Rows per page' control reads 20 and the footer reads 'Showing "
           "1-20 of 117', but the table draws 15 rows and the pager offers 8 "
           "pages -- ceil(117/15), not ceil(117/20). The page really "
           "paginates at 15 while telling the user 20, so five sites per page "
           "go unrendered until a size is picked by hand. Choosing any size "
           "explicitly behaves correctly.",
)
def test_site_cost_data_default_page_size(page):
    b = site_cost_data(page)
    b.open_page()
    b.default_page_size()


@allure.feature("Site Cost Data")
@allure.story("Known gap: Add Pricing accepts an empty form")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title(
    "Site Cost Data: the Add Data dialog enables Add Pricing with no category, "
    "dates or figures entered, on a form that writes live commercial terms "
    "with no way to delete them again"
)
@pytest.mark.xfail(
    strict=True,
    reason="Product bug: the Add Data dialog opens with every field empty and "
           "its Add Pricing submit already enabled, so an empty submit is one "
           "stray click away on a form that writes the commercial terms a CPO "
           "is invoiced on -- and the feature offers no delete to undo it. "
           "Every other dialog in the product keeps its submit disabled until "
           "its required fields are filled. Note the button is never pressed, "
           "so whether the submit is rejected server-side is unverified; the "
           "finding is that the UI does not gate it.",
)
def test_site_cost_data_add_pricing_validation(page):
    b = site_cost_data(page)
    b.open_page()
    # Reach a site page, where the Add Data dialog lives, without touching any
    # of the write paths on the way.
    b._search_for(SITE, expected=[SITE])
    b._park_mouse()
    b.rows.first.click()
    b.page.wait_for_url(re.compile(r"/admin/cost/[0-9a-f-]{36}"), timeout=30000)
    assert b._poll(lambda: b.rows.count() > 0, timeout_ms=30000), (
        "the site's cost history never loaded"
    )
    b.add_pricing_requires_input()
