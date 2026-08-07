import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.deal_onboarding")

# The deal the search check is pinned to. It is a substring of three real deal
# names on staging, which is what makes it useful -- it proves the box matches
# on a fragment rather than only on a whole name.
SEARCH_TERM = "East of England"

# The deal the detail check opens. Chosen over the other twelve because it is
# the only one on staging that exercises the interesting half of that page: it
# carries uploaded records *and* it is part-onboarded, so the site list shows
# both an "Onboarded" and an "In progress" site.
DETAIL_DEAL = "East of England CO OP New Sites"

# The values the three filter checks are pinned to.
CPO = "East of England CO OP"
DEAL_TYPE = "Operated and Maintained - Just Onboarding"
CRITICALITY = "High"


class deal_onboarding:
    """Deal Onboarding (/operations/import-deal).

    The pipeline of deals being brought onto the network: a searchable,
    filterable deal table whose rows expand to show the sites inside each deal,
    and a per-deal page carrying the contract records and a site-by-site
    onboarding form.

    The workflow is deliberately read-only. Every write on this page --
    Create Deal, Import Deal from HubSpot, Add Record, Save, Onboard Site --
    is one-way: the UI offers no delete, so anything a run created would sit on
    staging forever, and the HubSpot import consumes a real deal from the
    handover pipeline. So the write paths are opened and *validated* rather
    than submitted: the dialogs are checked on their fields, options and
    disabled submit buttons, then dismissed with Cancel / Clear / Escape. The
    run leaves staging exactly as it found it.

    Column sorting is checked separately by `sort_columns` -- see the note
    there.
    """

    # The table's full column set. The first header is blank: it holds the
    # expand-all chevron rather than a label.
    COLUMNS = ["", "Deal Name", "CPO", "Deal Type", "Total Sites", "Progress",
               "Deal Date", "Criticality"]

    # Sortable columns and the `sortColumn` value each sends.
    SORTABLE = {
        "Total Sites": "totalSites",
        "Progress": "progress",
        "Deal Date": "dealDate",
        "Criticality": "criticality",
    }
    NOT_SORTABLE = ["Deal Name", "CPO", "Deal Type"]

    # Filter options. The deal-type filter lists the long-form names; the table
    # abbreviates the same values to "O&M with Install" / "O&M Onboarding".
    DEAL_TYPES = [
        "Operated and Maintained - With Install",
        "Operated and Maintained - Just Onboarding",
        "Fully Funded - Install Required",
    ]
    CRITICALITIES = ["Low", "Medium", "High"]

    PAGE_SIZES = ["10", "20", "50", "100"]

    # The categories a record can be filed under.
    RECORD_CATEGORIES = ["Survey", "Legal", "Finance", "Installation",
                         "M-PPM", "M-Reaction", "Removal", "Others"]

    # The detail page's fact strip, and the site form's fields by placeholder.
    DETAIL_FACTS = ["CPO", "TYPE", "EXTERNAL ID", "CRITICALITY", "CLOSURE DATE"]
    SITE_FIELDS = ["Enter site address", "Enter city", "Enter postal code",
                   "Enter contact person name", "Enter contact number",
                   "Enter latitude", "Enter longitude"]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.nav_link = page.get_by_role("link", name="Deal Onboarding")

        # Search
        self.search = page.get_by_placeholder(re.compile(r"^Search deals"))
        self.search_clear = page.locator(
            "input[placeholder^='Search deals'] ~ button"
        )

        # Table. Deal rows are matched on having a full set of cells: an
        # expanded row is a single td with colspan=8, so counting `tbody > tr`
        # alone would double the row count the moment anything is expanded.
        self.table = page.locator("table").first
        self.rows = self.table.locator("tbody > tr:has(td:nth-child(8))")
        self.expanded = self.table.locator("tbody > tr:has(td[colspan])")

        # Filters
        self.cpo_filter = page.get_by_role("button", name="All CPOs", exact=True)
        self.type_filter = page.get_by_role("button", name="All types", exact=True)
        self.criticality_filter = page.get_by_role(
            "button", name="All criticalities", exact=True
        )
        self.clear_filters = page.get_by_role("button", name="Clear all filters")

        # Row expansion
        self.expand_all = page.get_by_role("button", name="Expand all rows")
        self.collapse_all = page.get_by_role("button", name="Collapse all rows")

        # Empty state / footer
        self.empty_state = page.get_by_text(
            "No deals found matching your criteria", exact=True
        )
        self.showing = page.get_by_text(re.compile(r"^Showing "))

        # Pagination
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$")
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Deal detail
        self.back = page.get_by_role("button", name="Back to records")
        self.edit_deal = page.get_by_role("button", name="Edit deal")
        self.copy_id = page.get_by_role("button", name="Copy full ID")
        self.add_records = page.get_by_role("button", name="Add Records")
        self.site_overview = page.get_by_text("Site Overview", exact=True)
        self.onboard_site = page.get_by_role("button", name="Onboard Site")
        # Scoped to the first match: opening the deal's edit form puts a second
        # "Save" in the header, and an unscoped locator would then be ambiguous.
        self.save_site = page.get_by_role("button", name="Save", exact=True).first
        self.site_step = page.get_by_text(re.compile(r"^\d+ of \d+$"))

        # Add Deal modal
        self.add_deal = page.get_by_role("button", name="Add Deal", exact=True)
        self.dialog = page.get_by_role("dialog")
        self.hubspot_tab = page.get_by_role("button", name="Sync from HubSpot")
        self.manual_tab = page.get_by_role("button", name="Manual Entry")
        self.deal_name_input = page.get_by_placeholder("Enter Deal name")
        self.partner_select = page.get_by_role("button", name="Select Partner")
        self.criticality_select = page.get_by_role("button", name="Add Criticality")
        # By text rather than by role: the control renders a calendar glyph
        # alongside its prompt, which keeps it out of reach of an accessible
        # name match.
        self.closure_date = page.get_by_text("Select Deal Closure Date")
        self.decrease = page.get_by_role("button", name="Decrease")
        self.increase = page.get_by_role("button", name="Increase")
        self.create_deal = page.get_by_role("button", name="Create Deal")
        self.clear_form = page.get_by_role("button", name="Clear", exact=True)

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _poll(self, predicate, timeout_ms=15000, interval_ms=250):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        The table refetches on every filter, search and page change, so state
        is polled until it settles rather than raced with a fixed sleep. The
        deadline is wall-clock so an expensive predicate cannot overrun it.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _park_mouse(self):
        """Move the pointer off the sidebar and let it collapse.

        The sidebar is fixed to the left edge and widens from 125px to 294px
        while hovered, covering the table's leading columns -- including the
        expand chevron and the deal-name link. A click there is then
        intercepted by the nav and retries until it times out.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] - 40, size["height"] - 60)
        self.page.wait_for_timeout(700)

    def _close_popover(self):
        """Dismiss any open listbox and let it leave the DOM.

        Every filter on this page is a popover that stays open after a
        selection. Left open it covers the control beside it, so the next
        click lands on the popover instead.
        """
        self.page.keyboard.press("Escape")
        self._poll(
            lambda: self.page.get_by_role("option").count() == 0, timeout_ms=5000
        )
        self.page.wait_for_timeout(300)

    def _names(self):
        """The deal name in each row, top to bottom."""
        try:
            return [
                (r.locator("td").nth(1).inner_text() or "").strip().replace("\n", " ")
                for r in self.rows.all()
            ]
        except Exception:
            # The table re-renders as its refetch lands; treat a read that
            # catches it mid-repaint as "not settled yet".
            return []

    def _settled_names(self, timeout_ms=15000):
        """The row order once it has stopped changing.

        A baseline captured immediately after a previous action can still be
        the old order, which then looks like the *next* click changed the
        table. This waits for two consecutive identical reads.
        """
        previous = None
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            current = self._names()
            if current and current == previous:
                return current
            previous = current
            self.page.wait_for_timeout(400)
        return self._names()

    def _loaded(self):
        return self.rows.count() > 0 and all(self._names())

    def _header(self, col):
        return self.table.locator("thead th").filter(
            has_text=re.compile(rf"^{re.escape(col)}")
        ).first

    def _column(self, index):
        """The text of column `index` in every row, top to bottom."""
        try:
            return [
                (r.locator("td").nth(index).inner_text() or "").strip().replace("\n", " ")
                for r in self.rows.all()
            ]
        except Exception:
            return []

    def _await_column(self, index, expected, label):
        """Wait until every row carries `expected` in column `index`.

        The filter lands in the URL before the refetch it triggered comes
        back, so the rows on screen at that moment are still the *previous*
        result. Reading them straight away compares the new filter against the
        old table and fails on a race rather than on a fault.
        """
        assert self._poll(
            lambda: bool(self._column(index))
            and all(v == expected for v in self._column(index)),
            timeout_ms=25000,
        ), (
            f"the {label} filter returned row(s) showing "
            f"{sorted(set(self._column(index)) - {expected})}, expected only "
            f"{expected!r}"
        )

    def _options(self):
        return [
            (o.inner_text() or "").strip()
            for o in self.page.get_by_role("option").all()
        ]

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        """Reach the page through the sidebar rather than a direct goto.

        A full page load on staging costs upwards of fifteen seconds because
        the whole SPA re-bootstraps and re-authenticates; the client-side route
        change is near-instant and exercises the nav link at the same time.
        """
        log.info("Opening Deal Onboarding")
        self.nav_link.click()
        self.page.wait_for_url(
            re.compile(r"/operations/import-deal(\?|$)"), timeout=30000
        )
        assert self._poll(self._loaded, timeout_ms=40000), (
            "the deal table never loaded"
        )
        # Clicking the sidebar link leaves the pointer on the nav, which stays
        # expanded and covers the leading edge of the table. Park it before
        # anything tries to click there.
        self._park_mouse()
        log.info("Deal Onboarding loaded with %s deal(s)", self.rows.count())

    # ----------------------------------------------------------------- #
    # Table structure
    # ----------------------------------------------------------------- #
    def check_table_structure(self):
        """The table renders its full column set and well-formed deal rows."""
        headers = [
            (h.inner_text() or "").strip()
            for h in self.table.locator("thead th").all()
        ]
        log.info("Table columns: %s", headers)
        assert headers == self.COLUMNS, (
            f"unexpected column set: {headers} != {self.COLUMNS}"
        )

        for row in self.rows.all():
            cells = [(c.inner_text() or "").strip() for c in row.locator("td").all()]
            name, cpo, deal_type, sites, progress, date, crit = cells[1:8]
            assert name, "a row has no deal name"
            assert cpo, f"the {name!r} row names no CPO"
            assert deal_type, f"the {name!r} row states no deal type"
            assert re.fullmatch(r"\d+", sites), (
                f"the {name!r} row shows {sites!r} sites, expected a whole number"
            )
            assert re.search(r"\d+%", progress), (
                f"the {name!r} row shows progress {progress!r}, expected a percentage"
            )
            assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", date), (
                f"the {name!r} row shows date {date!r}, expected dd-mm-yyyy"
            )
            assert crit in self.CRITICALITIES, (
                f"the {name!r} row shows criticality {crit!r}, "
                f"expected one of {self.CRITICALITIES}"
            )

        # The footer counts the same rows the table drew.
        footer = (self.showing.first.inner_text() or "").strip()
        shown = re.search(r"Showing\s+\d+[–-](\d+)\s+of\s+(\d+)", footer)
        assert shown, f"unexpected footer text: {footer!r}"
        assert int(shown.group(1)) == self.rows.count(), (
            f"the footer says {footer!r} but the table drew {self.rows.count()} rows"
        )
        log.info("Table shows %s deal row(s) -- %s", self.rows.count(), footer)

    # ----------------------------------------------------------------- #
    # Row expansion
    # ----------------------------------------------------------------- #
    def expand_rows(self):
        """A row opens onto the sites inside that deal, and all rows at once."""
        first = self._names()[0]
        sites = int((self.rows.first.locator("td").nth(4).inner_text() or "0").strip())

        log.info("Expanding the %r row (%s site(s))", first, sites)
        self._park_mouse()
        self.rows.first.get_by_role("button", name="Expand row").click()
        assert self._poll(lambda: self.expanded.count() == 1, timeout_ms=15000), (
            f"expanding the {first!r} row revealed no site panel"
        )
        # The panel lists one entry per site the row counted, each carrying the
        # site's own ID -- that is what distinguishes it from a blank shell.
        # The sites are fetched when the row opens rather than shipped with it,
        # so the panel exists before it has anything in it.
        def _sites_listed():
            return (self.expanded.first.inner_text() or "").count("ID:") == sites

        assert self._poll(_sites_listed, timeout_ms=25000), (
            f"the {first!r} row counts {sites} site(s) but its panel lists "
            f"{(self.expanded.first.inner_text() or '').count('ID:')}"
        )
        panel = (self.expanded.first.inner_text() or "").strip()
        log.info("Site panel: %s", panel.replace("\n", " ")[:100])

        log.info("Collapsing it again")
        self.rows.first.get_by_role("button", name="Collapse row").click()
        assert self._poll(lambda: self.expanded.count() == 0, timeout_ms=15000), (
            f"the {first!r} row did not collapse"
        )

        total = self.rows.count()
        log.info("Expanding all %s row(s) at once", total)
        self._park_mouse()
        self.expand_all.click()
        assert self._poll(lambda: self.expanded.count() == total, timeout_ms=20000), (
            f"expected {total} site panel(s), got {self.expanded.count()}"
        )
        expect(self.collapse_all).to_be_visible()

        log.info("Collapsing them all again")
        self.collapse_all.click()
        assert self._poll(lambda: self.expanded.count() == 0, timeout_ms=20000), (
            "the rows did not all collapse"
        )
        expect(self.expand_all).to_be_visible()

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def search_deals(self):
        """Search narrows the table, and a no-match query shows the empty state."""
        before = self._settled_names()

        log.info("Searching deals for %r", SEARCH_TERM)
        self.search.fill(SEARCH_TERM)
        self.page.wait_for_url(re.compile(r"[?&]search="), timeout=15000)
        assert self._poll(
            lambda: self._names() and self._names() != before, timeout_ms=20000
        ), "the search did not change the deal list"
        # Note the box matches on the deal name only, despite its placeholder
        # also offering CPOs and external IDs: "East of England" returns the
        # three deals *named* for that CPO but not the fourth deal that merely
        # belongs to it.
        for name in self._names():
            assert SEARCH_TERM.lower() in name.lower(), (
                f"the search returned {name!r}, which does not contain "
                f"{SEARCH_TERM!r}"
            )
        log.info("Search %r -> %s deal(s)", SEARCH_TERM, self.rows.count())

        log.info("Searching for a term that matches nothing")
        self.search.fill("zzzz-no-such-deal")
        assert self._poll(lambda: self.empty_state.count() > 0, timeout_ms=20000), (
            "a no-match search did not show the empty state"
        )
        assert self.rows.count() == 0, "the empty state still drew deal rows"
        footer = (self.showing.first.inner_text() or "").strip()
        assert "0" in footer, f"the footer should report no results, reads {footer!r}"
        log.info("Empty state shown -- %s", footer)

        log.info("Clearing the search with its own clear button")
        self.search_clear.click()
        assert self._poll(lambda: "search=" not in self.page.url, timeout_ms=20000), (
            f"the search is still in the URL after clearing: {self.page.url}"
        )
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "the table did not return to its unfiltered rows: "
            f"{self._names()[:3]} != {before[:3]}"
        )

    # ----------------------------------------------------------------- #
    # Filters
    # ----------------------------------------------------------------- #
    def filter_by_cpo(self):
        """Narrow the table to one CPO, then clear it.

        The CPO list is the full partner directory rather than only the
        partners that hold deals, so its contents are checked for the value
        under test rather than compared to a fixed list.
        """
        before = self._settled_names()

        log.info("Opening the CPO filter")
        self._park_mouse()
        self.cpo_filter.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0), (
            "the CPO filter opened with no options"
        )
        listed = self._options()
        assert CPO in listed, f"the CPO filter does not offer {CPO!r}"
        assert len(listed) > len(before), (
            "the CPO filter should list every partner, not only those with "
            f"deals, but offers just {len(listed)}"
        )
        log.info("CPO filter offers %s partner(s)", len(listed))

        self.page.get_by_role("option", name=CPO, exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]cpo="), timeout=15000)
        self._close_popover()
        assert self._poll(self._loaded, timeout_ms=20000), (
            f"the table did not repopulate under the {CPO!r} filter"
        )
        expect(
            self.page.get_by_role("button", name=f"CPO: {CPO}", exact=True)
        ).to_be_visible()
        # Every surviving row really belongs to that CPO.
        self._await_column(2, CPO, "CPO")
        log.info("CPO filter %r -> %s deal(s)", CPO, self.rows.count())

        self._clear_all(before)

    def filter_by_deal_type(self):
        """Narrow the table to one deal type, then clear it."""
        before = self._settled_names()

        log.info("Opening the deal-type filter")
        self._park_mouse()
        self.type_filter.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0), (
            "the deal-type filter opened with no options"
        )
        listed = self._options()
        assert listed == self.DEAL_TYPES, (
            f"the deal-type filter offers {listed}, expected {self.DEAL_TYPES}"
        )

        self.page.get_by_role("option", name=DEAL_TYPE, exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]dealType="), timeout=15000)
        self._close_popover()
        assert self._poll(self._loaded, timeout_ms=20000), (
            f"the table did not repopulate under the {DEAL_TYPE!r} filter"
        )
        expect(
            self.page.get_by_role(
                "button", name=f"Deal Type: {DEAL_TYPE}", exact=True
            )
        ).to_be_visible()
        # The table abbreviates the type it was filtered on, so the rows are
        # checked against the short form the column actually renders.
        self._await_column(3, "O&M Onboarding", "deal-type")
        log.info("Deal-type filter %r -> %s deal(s)", DEAL_TYPE, self.rows.count())

        self._clear_all(before)

    def filter_by_criticality(self):
        """Narrow the table to one criticality, then clear it."""
        before = self._settled_names()

        log.info("Opening the criticality filter")
        self._park_mouse()
        self.criticality_filter.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0), (
            "the criticality filter opened with no options"
        )
        listed = self._options()
        assert listed == self.CRITICALITIES, (
            f"the criticality filter offers {listed}, expected {self.CRITICALITIES}"
        )

        self.page.get_by_role("option", name=CRITICALITY, exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]criticality="), timeout=15000)
        self._close_popover()
        assert self._poll(self._loaded, timeout_ms=20000), (
            f"the table did not repopulate under the {CRITICALITY!r} filter"
        )
        expect(
            self.page.get_by_role(
                "button", name=f"Criticality: {CRITICALITY}", exact=True
            )
        ).to_be_visible()
        self._await_column(7, CRITICALITY, "criticality")
        log.info(
            "Criticality filter %r -> %s deal(s)", CRITICALITY, self.rows.count()
        )

        self._clear_all(before)

    def combine_filters(self):
        """Two filters narrow together rather than replacing one another."""
        before = self._settled_names()

        log.info("Applying the deal-type and criticality filters together")
        self._park_mouse()
        self.type_filter.click()
        self._poll(lambda: self.page.get_by_role("option").count() > 0)
        self.page.get_by_role("option", name=DEAL_TYPE, exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]dealType="), timeout=15000)
        self._close_popover()
        assert self._poll(self._loaded, timeout_ms=20000)
        narrowed_once = self.rows.count()

        self.criticality_filter.click()
        self._poll(lambda: self.page.get_by_role("option").count() > 0)
        self.page.get_by_role("option", name=CRITICALITY, exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]criticality="), timeout=15000)
        self._close_popover()
        assert self._poll(self._loaded, timeout_ms=20000)

        # Both are in the URL at once, and the second genuinely narrowed the
        # first rather than swapping it out.
        assert "dealType=" in self.page.url and "criticality=" in self.page.url, (
            f"the two filters did not compose in the URL: {self.page.url}"
        )
        self._await_column(3, "O&M Onboarding", "combined deal-type")
        self._await_column(7, CRITICALITY, "combined criticality")
        assert self.rows.count() <= narrowed_once, (
            f"adding the criticality filter widened the table from "
            f"{narrowed_once} to {self.rows.count()} row(s)"
        )
        log.info(
            "Combined filters -> %s deal(s) (down from %s)",
            self.rows.count(), narrowed_once,
        )

        self._clear_all(before)

    def _clear_all(self, before):
        """Drop every filter with the clear-all control and prove it restored.

        The control only appears once something is filtered, which is itself
        worth pinning -- so it is checked visible before it is used and gone
        afterwards.
        """
        log.info("Clearing every filter")
        expect(self.clear_filters).to_be_visible()
        self.clear_filters.click()
        assert self._poll(
            lambda: not re.search(r"[?&](cpo|dealType|criticality)=", self.page.url),
            timeout_ms=15000,
        ), f"a filter is still in the URL after clearing: {self.page.url}"
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "the table did not return to its unfiltered rows"
        )
        assert self.clear_filters.count() == 0, (
            "the clear-all control is still on screen with nothing to clear"
        )
        expect(self.cpo_filter).to_be_visible()
        expect(self.type_filter).to_be_visible()
        expect(self.criticality_filter).to_be_visible()

    # ----------------------------------------------------------------- #
    # Pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        """Resize the page, then step through it.

        Staging holds fewer deals than the default page size, so there is only
        one page to begin with. The size is dropped to its smallest option
        first -- that is what creates a second page to step onto.
        """
        total = self.rows.count()
        original = (self.page_size.first.inner_text() or "").strip()

        log.info("Switching the page size from %s to 10", original)
        self._park_mouse()
        self.page_size.first.click()
        self.page.wait_for_timeout(1000)
        listed = self._options()
        assert listed == self.PAGE_SIZES, (
            f"the page-size selector offers {listed}, expected {self.PAGE_SIZES}"
        )
        self.page.get_by_role("option", name="10", exact=True).click()
        self._close_popover()
        assert self._poll(lambda: self.rows.count() == 10, timeout_ms=25000), (
            f"expected 10 rows at page size 10, got {self.rows.count()}"
        )
        first_page = self._settled_names()

        assert self.next_page.is_enabled(), (
            f"page size 10 over {total} deals should give more than one page"
        )
        log.info("Paging forward and back")
        self._park_mouse()
        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2"), timeout=15000)
        assert self._poll(
            lambda: self._names() and self._names() != first_page
        ), "page 2 shows the same deals as page 1"
        # No deal appears on both pages.
        assert not set(self._names()) & set(first_page), (
            "page 2 repeats deals from page 1"
        )
        log.info("Page 2 shows %s deal(s)", self.rows.count())

        self._park_mouse()
        self.prev_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=15000)
        assert self._poll(lambda: self._names() == first_page), (
            "going back did not restore page 1"
        )

        # A direct page jump. Matched exactly -- "Go to page 1" is a prefix of
        # "Go to page 10" and a substring match would resolve to both.
        page_2 = self.page.get_by_role("button", name="Go to page 2", exact=True)
        if page_2.count():
            log.info("Jumping straight to page 2")
            self._park_mouse()
            page_2.click()
            self.page.wait_for_url(re.compile(r"[?&]page=2"), timeout=15000)
            assert self._poll(
                lambda: self._names() and self._names() != first_page
            ), "the page-2 jump stayed on page 1"
            self._park_mouse()
            self.page.get_by_role("button", name="Go to page 1", exact=True).click()
            self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=15000)
            assert self._poll(lambda: self._names() == first_page)

        log.info("Restoring the page size to %s", original)
        self._park_mouse()
        self.page_size.first.click()
        self.page.wait_for_timeout(1000)
        self.page.get_by_role("option", name=original, exact=True).click()
        self._close_popover()
        assert self._poll(lambda: self.rows.count() == total, timeout_ms=25000), (
            f"expected {total} rows after restoring page size {original}, "
            f"got {self.rows.count()}"
        )

    # ----------------------------------------------------------------- #
    # Deal detail
    # ----------------------------------------------------------------- #
    def open_deal(self):
        """Open one deal and check everything its page renders.

        Read-only throughout: the edit form is opened and cancelled, and the
        Add Record dialog is checked on its fields and dismissed. Nothing is
        saved, uploaded or onboarded.
        """
        log.info("Finding %r", DETAIL_DEAL)
        self.search.fill(DETAIL_DEAL)
        self.page.wait_for_url(re.compile(r"[?&]search="), timeout=15000)
        assert self._poll(
            lambda: self._names() == [DETAIL_DEAL], timeout_ms=25000
        ), f"searching for {DETAIL_DEAL!r} returned {self._names()}"

        log.info("Opening it")
        self._park_mouse()
        self.rows.first.get_by_role("button", name=DETAIL_DEAL).click()
        self.page.wait_for_url(
            re.compile(r"/operations/import-deal/[0-9a-f-]{36}"), timeout=25000
        )
        expect(
            self.page.get_by_text(f"Deal : {DETAIL_DEAL}")
        ).to_be_visible(timeout=25000)
        log.info("Deal page open at %s", self.page.url)

        self._check_deal_header()
        self._check_records()
        self._check_site_overview()
        self._check_edit_form()
        self._check_add_record_dialog()

        log.info("Going back to the deal list")
        self.back.click()
        self.page.wait_for_url(
            re.compile(r"/operations/import-deal(\?|$)"), timeout=25000
        )
        assert self._poll(self._loaded, timeout_ms=30000), (
            "the deal table did not come back"
        )
        # Leave the list unfiltered for whatever runs next.
        if "search=" in self.page.url:
            self.search.fill("")
            self._poll(lambda: "search=" not in self.page.url, timeout_ms=15000)
            self._poll(self._loaded, timeout_ms=25000)

    def _check_deal_header(self):
        """The fact strip states every field, and the ID is copyable."""
        body = self.page.locator("body").inner_text() or ""
        for label in self.DETAIL_FACTS:
            assert label in body, f"the deal header is missing {label!r}"
        # The header repeats the values the list row carried.
        assert CPO in body, f"the deal header does not name its CPO ({CPO!r})"
        assert re.search(r"\d{2}-\d{2}-\d{4}", body), (
            "the deal header shows no closure date"
        )
        # The external ID is elided in the strip, so the copy control is the
        # only way to get the whole thing -- check it is offered.
        expect(self.copy_id).to_be_visible()
        log.info("Deal header states all %s facts", len(self.DETAIL_FACTS))

    def _check_records(self):
        """The Records section lists the deal's documents, and collapses."""
        heading = self.page.get_by_text("Records", exact=True).first
        expect(heading).to_be_visible()

        section = self.page.locator("div").filter(has=heading).last
        text = (section.inner_text() or "")
        count = re.search(r"(\d+) documents?", text)
        assert count, (
            f"the Records section states no document count: {text[:120]!r}"
        )
        # Each document names itself, its category and its size.
        for line in text.split("\n"):
            if re.search(r"\d+(\.\d+)?\s*(KB|MB)", line):
                category = line.split("•")[0].strip()
                assert category in self.RECORD_CATEGORIES, (
                    f"a record is filed under {category!r}, which is not one "
                    f"of {self.RECORD_CATEGORIES}"
                )
        log.info("Records section lists %s document(s)", count.group(1))

        expect(self.add_records).to_be_visible()

    def _check_site_overview(self):
        """The site list, its onboarding tally, and the per-site form."""
        expect(self.site_overview).to_be_visible()

        body = self.page.locator("body").inner_text() or ""
        tally = re.search(r"(\d+)\s*of\s*(\d+) onboarded", body)
        assert tally, "the Site Overview states no onboarding tally"
        done, total = int(tally.group(1)), int(tally.group(2))
        assert 0 <= done <= total, f"nonsensical tally: {tally.group(0)!r}"
        log.info("Site Overview: %s of %s onboarded", done, total)

        # This deal is pinned precisely because it is part-onboarded, so the
        # list carries both states.
        assert "Onboarded" in body and "In progress" in body, (
            f"{DETAIL_DEAL!r} should show both an onboarded and an in-progress "
            "site; the site list shows neither pair"
        )

        # The form for the selected site offers every field.
        for placeholder in self.SITE_FIELDS:
            expect(self.page.get_by_placeholder(placeholder)).to_be_visible()
        expect(self.page.get_by_text("Site name")).to_be_visible()
        log.info("Site form offers all %s fields", len(self.SITE_FIELDS) + 1)

        # Step to the next site with the form's own pager. This only changes
        # which site is displayed -- it saves nothing.
        step = (self.site_step.first.inner_text() or "").strip()
        assert re.fullmatch(rf"1 of {total}", step), (
            f"the site pager opens on {step!r}, expected '1 of {total}'"
        )
        if total > 1:
            log.info("Stepping to the next site")
            self.page.locator("button:right-of(:text('1 of'))").first.click()
            assert self._poll(
                lambda: (self.site_step.first.inner_text() or "").strip()
                == f"2 of {total}",
                timeout_ms=15000,
            ), "the site pager did not advance to site 2"
            log.info("Site pager advanced to 2 of %s", total)

        # Both write controls are present but neither is pressed: Save would
        # overwrite the site and Onboard Site is one-way.
        expect(self.save_site).to_be_visible()
        expect(self.onboard_site).to_be_visible()

    def _check_edit_form(self):
        """The edit form opens, protects the fields it should, and cancels.

        Cancel rather than Save: the deal must come back off this check
        unchanged.
        """
        log.info("Opening the deal edit form")
        self.edit_deal.click()
        cancel = self.page.get_by_role("button", name="Cancel", exact=True)
        expect(cancel).to_be_visible(timeout=15000)
        expect(
            self.page.get_by_role("button", name="Save", exact=True).first
        ).to_be_visible()

        # Note the edit form's own placeholder is "Enter deal name" -- lower
        # case d -- while the Add Deal modal spells the same field "Enter Deal
        # name". They are two different inputs; matching loosely would find
        # whichever happened to be mounted.
        deal_name = self.page.get_by_placeholder("Enter deal name")
        expect(deal_name).to_be_editable()
        assert (deal_name.input_value() or "").strip() == DETAIL_DEAL, (
            f"the edit form opened on {deal_name.input_value()!r}, "
            f"expected {DETAIL_DEAL!r}"
        )

        # The CPO and the external ID are fixed once a deal exists: both are
        # rendered as locked inputs carrying the deal's current values.
        locked = self.page.locator("input[placeholder='Placeholder']")
        assert locked.count() == 2, (
            f"expected the CPO and external ID to be locked, found "
            f"{locked.count()} locked field(s)"
        )
        assert (locked.nth(0).input_value() or "").strip() == CPO, (
            f"the locked CPO field reads {locked.nth(0).input_value()!r}"
        )
        assert self.page.url.endswith(
            (locked.nth(1).input_value() or "").strip()
        ), "the locked external ID does not match the deal in the URL"
        for index in (0, 1):
            assert locked.nth(index).is_disabled(), (
                "the CPO and external ID must not be editable"
            )
        log.info("Edit form: deal name editable, CPO and external ID locked")

        log.info("Cancelling the edit without saving")
        cancel.click()
        expect(self.edit_deal).to_be_visible(timeout=15000)
        expect(self.page.get_by_text(f"Deal : {DETAIL_DEAL}")).to_be_visible()

    def _check_add_record_dialog(self):
        """The upload dialog offers its fields and refuses an empty submit."""
        log.info("Opening the Add Record dialog")
        self.add_records.click()
        # By heading rather than by text: the dialog's submit button carries
        # the same words, so a text match resolves to both.
        title = self.page.get_by_role("heading", name="Add Record", exact=True)
        expect(title).to_be_visible(timeout=15000)

        body = (self.dialog.last.inner_text() or "")
        assert "Choose file or drag to upload" in body, (
            "the Add Record dialog offers no upload target"
        )
        for fmt in ["PDF", "CSV", "XLSX", "PNG"]:
            assert fmt in body, f"the dialog does not accept {fmt}"

        log.info("Checking the record categories")
        self.page.get_by_role("button", name="Select category").click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0), (
            "the category selector opened with no options"
        )
        listed = self._options()
        assert listed == self.RECORD_CATEGORIES, (
            f"the category selector offers {listed}, "
            f"expected {self.RECORD_CATEGORIES}"
        )
        self._close_popover()

        # Nothing has been chosen, so the dialog must not let anything through.
        submit = self.page.get_by_role("button", name="Add Record", exact=True)
        assert submit.is_disabled(), (
            "Add Record is enabled with no file and no category chosen"
        )
        log.info("Add Record stays disabled until a file is chosen")

        log.info("Cancelling the dialog without uploading")
        self.page.get_by_role("button", name="Cancel", exact=True).click()
        assert self._poll(lambda: title.count() == 0, timeout_ms=15000), (
            "the Add Record dialog did not close"
        )

    # ----------------------------------------------------------------- #
    # Add Deal
    # ----------------------------------------------------------------- #
    def check_add_deal_modal(self):
        """Both routes into a new deal, validated but never submitted.

        Neither Import Deal nor Create Deal is pressed. An import consumes a
        real deal out of the HubSpot handover pipeline, and a created deal
        cannot be removed again from this UI -- every run would leave one
        behind. So the modal is checked on its structure, its options and its
        disabled submit, then dismissed.
        """
        log.info("Opening the Add Deal modal")
        self.add_deal.click()
        modal_title = self.page.get_by_role("heading", name="Add New Deal")
        expect(modal_title).to_be_visible(timeout=20000)
        expect(self.hubspot_tab).to_be_visible()
        expect(self.manual_tab).to_be_visible()

        self._check_hubspot_tab()
        self._check_manual_entry_tab()

        log.info("Closing the modal without creating anything")
        self.page.keyboard.press("Escape")
        assert self._poll(lambda: modal_title.count() == 0, timeout_ms=15000), (
            "the Add Deal modal did not close"
        )

    def _check_hubspot_tab(self):
        """The handover-ready queue lists importable deals -- read only."""
        log.info("Checking the HubSpot handover queue")
        modal = self.dialog.last
        assert self._poll(
            lambda: "Handover Ready" in (modal.inner_text() or ""), timeout_ms=25000
        ), "the HubSpot tab never listed the handover-ready queue"

        text = modal.inner_text() or ""
        found = re.search(r"(\d+) Found", text)
        assert found, f"the queue states no count: {text[:120]!r}"
        expected = int(found.group(1))

        imports = modal.get_by_role("button", name="Import Deal")
        assert imports.count() == expected, (
            f"the queue says {expected} found but offers {imports.count()} "
            "Import Deal button(s)"
        )
        # Every entry identifies the HubSpot deal behind it.
        ids = re.findall(r"HubSpot Deal ID: (HS-\d+)", text)
        assert len(ids) == expected, (
            f"{expected} queued deal(s) but {len(ids)} HubSpot ID(s)"
        )
        log.info("HubSpot queue: %s deal(s) ready to import (not imported)", expected)

    def _check_manual_entry_tab(self):
        """The manual form offers every field and refuses an empty submit."""
        log.info("Switching to Manual Entry")
        self.manual_tab.click()
        expect(self.deal_name_input).to_be_visible(timeout=20000)

        modal = self.dialog.last
        text = modal.inner_text() or ""
        for section in ["Type of Deal", "Deal Name", "CPO Name", "Deal Purpose",
                        "Criticality", "Deal Closure Date", "Number of Sites"]:
            assert section in text, f"the manual form is missing {section!r}"

        # The three deal types, with the first selected by default.
        for deal_type in self.DEAL_TYPES:
            assert deal_type in text, f"the form does not offer {deal_type!r}"
        for purpose in ["Onboard new sites", "Link existing site"]:
            assert purpose in text, f"the form does not offer {purpose!r}"
        expect(self.closure_date).to_be_visible()

        log.info("Checking the partner and criticality selectors")
        self.partner_select.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0), (
            "the partner selector opened with no options"
        )
        partners = self._options()
        # Checked on shape rather than on a pinned partner: this selector pages
        # its list in as it is scrolled -- it opens on 20 of the 63 the filter
        # on the list page offers -- so which partners are present depends on
        # how far it has been scrolled.
        assert partners and all(p.strip() for p in partners), (
            f"the partner selector offers blank entries: {partners[:5]}"
        )
        log.info("Partner selector opens on %s partner(s)", len(partners))
        self._close_popover()

        self.criticality_select.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0), (
            "the criticality selector opened with no options"
        )
        listed = self._options()
        assert listed == self.CRITICALITIES, (
            f"the criticality selector offers {listed}, "
            f"expected {self.CRITICALITIES}"
        )
        self._close_popover()

        log.info("Stepping the site count")
        sites = self.page.locator(
            "//*[normalize-space()='Number of Sites']/following::div[1]"
        ).first
        before = (sites.inner_text() or "").strip()
        self.increase.click()
        assert self._poll(
            lambda: (sites.inner_text() or "").strip() != before, timeout_ms=8000
        ), "the site-count stepper did not go up"
        after = (sites.inner_text() or "").strip()
        self.decrease.click()
        assert self._poll(
            lambda: (sites.inner_text() or "").strip() == before, timeout_ms=8000
        ), "the site-count stepper did not come back down"
        log.info("Site count steps %s -> %s -> %s", before, after, before)

        # Half-filled, the form must still refuse to submit.
        self.deal_name_input.fill("Automation check -- not submitted")
        self.page.wait_for_timeout(500)
        assert self.create_deal.is_disabled(), (
            "Create Deal is enabled with only a name filled in -- the CPO, "
            "criticality and closure date are all still empty"
        )
        log.info("Create Deal stays disabled while the form is incomplete")

        log.info("Clearing the form")
        self.clear_form.click()
        assert self._poll(
            lambda: (self.deal_name_input.input_value() or "") == "",
            timeout_ms=10000,
        ), "Clear did not empty the deal name"
        assert self.create_deal.is_disabled(), (
            "Create Deal is enabled after the form was cleared"
        )

    # ----------------------------------------------------------------- #
    # Sorting -- see the note below
    # ----------------------------------------------------------------- #
    def sort_columns(self):
        """Sort every sortable column and prove the rows actually reorder.

        This is kept out of `deal_onboarding_page` because it currently fails,
        and it fails on the product rather than on the automation. Clicking a
        sortable header does update the URL (`sortColumn` / `sortDirection`)
        and does flip the header's chevron, but the table never reorders --
        the same rows come back in the same order in both directions, for all
        four sortable columns. The assertion below is written for the
        behaviour the control advertises, so it will start passing on its own
        once sorting is wired up.
        """
        for col, param in self.SORTABLE.items():
            baseline = self._settled_names()

            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sortColumn={param}\b"), timeout=15000
            )
            assert self._poll(self._loaded, timeout_ms=20000), (
                f"the table is empty after sorting by {col!r}"
            )
            direction = re.search(r"sortDirection=(asc|desc)", self.page.url)
            assert direction, (
                f"sorting by {col!r} set no sortDirection: {self.page.url}"
            )
            ascending = self._settled_names()
            assert ascending != baseline, (
                f"sorting by {col!r} set sortColumn={param}&"
                f"sortDirection={direction.group(1)} but the table came back in "
                f"its original order: {ascending[:4]}"
            )

            opposite = "desc" if direction.group(1) == "asc" else "asc"
            self._park_mouse()
            self._header(col).click()
            # Wait for the *direction* to flip, not merely for sortColumn to be
            # present -- it already is from the first click, so waiting on that
            # returns instantly and the row comparison below then races the
            # refetch instead of following it.
            self.page.wait_for_url(
                re.compile(rf"[?&]sortDirection={opposite}\b"), timeout=15000
            )
            assert self._poll(
                lambda a=ascending: self._names() and self._names() != a,
                timeout_ms=20000,
            ), f"reversing the {col!r} sort did not reorder the table"
            log.info("Column %-12s sorts both ways (sortColumn=%s)", col, param)

        # The remaining headers carry neither a sort chevron nor a pointer
        # cursor, and clicking one leaves the URL alone.
        for col in self.NOT_SORTABLE:
            header = self._header(col)
            classes = header.get_attribute("class") or ""
            assert "cursor-pointer" not in classes, (
                f"{col} presents itself as sortable but is not in SORTABLE"
            )
            assert header.locator("svg").count() == 0, (
                f"{col} carries a sort indicator but is not in SORTABLE"
            )
        log.info("Columns %s are correctly not sortable", self.NOT_SORTABLE)

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def deal_onboarding_page(self):
        self.open_page()
        self.check_table_structure()
        self.expand_rows()
        self.search_deals()
        self.filter_by_cpo()
        self.filter_by_deal_type()
        self.filter_by_criticality()
        self.combine_filters()
        self.paginate()
        self.open_deal()
        self.check_add_deal_modal()
        log.info("Deal Onboarding workflow completed")
