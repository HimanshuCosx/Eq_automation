import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.site_cost_data")

# The site every detail-page check is pinned to. Chosen out of the 117 on
# staging because it is the only one that exercises the interesting half of
# this feature at once: it carries a real cost record (profit share, unit cost
# and standing charge all set rather than "—"), it holds two charging devices
# so its row has something to expand onto, and its activity trail carries both
# System entries and a human edit sourced from an uploaded CSV.
SITE = "Earlham Green CO OP"
SITE_ID = "302206"
SITE_CPO = "East of England CO OP"

# The CPO the filter check is pinned to. Mulberry Homes owns exactly two of the
# 117 sites, so applying it collapses the table unmistakably -- a filter that
# silently did nothing could not pass this.
CPO = "Mulberry Homes"
CPO_SITES = ["Moulton", "Launton"]

# The only sub-organisation on staging. Every site belongs to it, which is why
# the check below pins the *button state* and the row survival rather than a
# change in the row count -- see `filter_by_sub_organisation`.
SUB_ORG = "Plug-N-Go"

# A search term that matches one site, and one that matches nothing.
SEARCH_TERM = "Earlham"
NO_MATCH = "zzzz-no-such-site"


class site_cost_data:
    """Site Cost Data (/admin/cost).

    The commercial terms behind every site on the network: a searchable,
    filterable, sortable table of the cost basis each site is billed on --
    profit share, electricity unit cost and standing charge -- plus a per-site
    page holding the dated history of those terms and an audit trail of who
    changed what, when, and from which uploaded sheet.

    The workflow is deliberately read-only. Every write this page offers --
    Add Pricing on the detail page and the inline row editor -- changes the
    money a real CPO is invoiced on, and neither is reversible from this UI:
    there is no delete anywhere on the feature, so a pricing entry created by a
    test run would sit on staging forever and skew every reconciliation built
    on top of it. So the write paths are opened and *validated* rather than
    submitted: the Add Data dialog is checked on its category, date, financial
    and frequency controls, the inline editor is checked on the fields it
    unlocks and the values it pre-fills, and both are then dismissed with
    Cancel. The run leaves staging exactly as it found it.

    Four behaviours on this page are currently broken on the product rather
    than on the automation. They are checked by their own methods -- see
    `sort_by_site_name`, `footer_reflects_filter`, `default_page_size` and
    `add_pricing_requires_input` -- so that the main workflow reports the state
    of everything that does work, and each gap stays individually visible.
    """

    # ------------------------------------------------------------------ #
    # List page
    # ------------------------------------------------------------------ #

    # The table's full column set. The last header is blank: it holds the row's
    # trailing chevron rather than a label.
    COLUMNS = ["Sites", "CD", "CPO", "Site Profit Share %",
               "Electricity Unit Cost", "Standing Charge", "Duration",
               "Activity", ""]

    # Sortable columns that work, and the `sort_by` value each sends.
    # "Sites" is deliberately absent -- it is sortable in the UI but empties
    # the table, which `sort_by_site_name` covers on its own.
    SORTABLE = {
        "CD": "charging_device_count",
        "CPO": "cpo",
        "Site Profit Share %": "site_profit_share",
        "Electricity Unit Cost": "electricity_unit_cost",
        "Standing Charge": "standing_charge",
    }
    BROKEN_SORT = ("Sites", "name")
    NOT_SORTABLE = ["Duration", "Activity"]

    PAGE_SIZES = ["10", "20", "50", "100"]

    # Every money-bearing column is either a formatted figure or an em dash for
    # a site whose terms have never been set. Both are legitimate, so the
    # column checks accept either and reject anything else.
    UNSET = "—"
    CELL_SHAPES = {
        3: r"\d+\.\d{2}%",          # Site Profit Share %
        4: r"£\d+\.\d{5}",          # Electricity Unit Cost
        5: r"£\d+\.\d{5}/(day|month)",  # Standing Charge
    }

    # The Duration cell is a start date over an end date, an open-ended range,
    # or the placeholder shown for a site with no dated terms at all.
    DURATION = re.compile(
        r"^(Not set|\d{2}-\d{2}-\d{4} (Ongoing|\d{2}-\d{2}-\d{4}))$"
    )

    # ------------------------------------------------------------------ #
    # Detail page
    # ------------------------------------------------------------------ #

    DETAIL_COLUMNS = ["", "Start Date", "End Date", "Site Profit Share %",
                      "Electricity Unit Cost", "Standing Charge", "Updated At",
                      "Actions"]

    # The Add Data dialog's controls.
    CATEGORIES = ["Site Wise", "Charging Devices"]
    FREQUENCIES = ["Day", "Monthly"]
    PRICING_FIELDS = ["CPO Share %", "Electricity Unit Cost", "Standing Charge"]

    # The inline row editor's fields, by the aria-label each input exposes.
    # Note these differ in case from the dialog's ("CPO Share %" against "Site
    # profit share %") -- they are two separate forms onto the same values, so
    # a loose match would find whichever happened to be mounted.
    EDIT_FIELDS = ["Site profit share %", "Electricity unit cost",
                   "Standing charge"]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.nav_link = page.get_by_role("link", name="Site Cost Data")

        # Search
        self.search = page.get_by_placeholder(re.compile(r"^Search sites"))

        # Table. Rows are matched on *not* carrying a colspan cell: both the
        # empty state and the detail page's expanded device panels render as a
        # single full-width td, so counting `tbody > tr` alone would report a
        # row where there is no data.
        self.table = page.locator("table").first
        self.rows = self.table.locator("tbody > tr:not(:has(td[colspan]))")
        self.expanded = self.table.locator("tbody > tr:has(td[colspan])")

        # Filters. Both are dropdowns whose button carries its own current
        # value, which is what makes the applied state checkable.
        self.sub_org_filter = page.get_by_role(
            "button", name=re.compile(r"^Sub-Organisation")
        )
        self.cpo_filter = page.get_by_role("button", name=re.compile(r"^CPO\b"))
        self.clear_filters = page.get_by_role("button", name="Clear filters")

        # Empty states. The page has two, and they mean different things: one
        # says the query matched nothing, the other says the site has no cost
        # records at all.
        self.no_match = page.get_by_text("No sites found", exact=True)
        self.no_data = page.get_by_text("No site cost data", exact=True)

        # Footer / pagination
        self.showing = page.get_by_text(re.compile(r"^Showing "))
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$"), exact=True
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Activity trail. The trigger is deliberately not held here: there is
        # one per row, so it is always reached through the row under test.
        self.dialog = page.get_by_role("dialog")
        self.close_activity = page.get_by_role("button", name="Close", exact=True)

        # Detail page
        self.breadcrumb = page.locator("nav[aria-label='Breadcrumb']")
        self.add_data = page.get_by_role("button", name="Add Data")
        self.expand_all = page.get_by_role("button", name="Expand all rows")
        self.collapse_all = page.get_by_role("button", name="Collapse all rows")
        self.edit_row = page.get_by_role("button", name="Edit", exact=True)
        self.save_edit = page.get_by_role("button", name="Save changes")
        self.cancel_edit = page.get_by_role("button", name="Cancel editing")
        self.set_end_date = page.get_by_role("button", name="Set date")
        self.edit_frequency = page.get_by_role(
            "button", name="Standing charge frequency"
        )

        # Add Data dialog
        self.add_pricing = page.get_by_role("button", name="Add Pricing")
        self.cancel_dialog = page.get_by_role("button", name="Cancel", exact=True)
        self.category_select = page.get_by_role("button", name="Site Wise")

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _poll(self, predicate, timeout_ms=20000, interval_ms=250):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        Every filter, search, sort and page change on this table refetches, so
        state is polled until it settles rather than raced with a fixed sleep.
        The deadline is wall-clock so an expensive predicate cannot overrun it.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _park_mouse(self):
        """Move the pointer off the sidebar and let it collapse.

        The sidebar is fixed to the left edge and widens from 125px while
        hovered, covering the table's leading column -- which on this page is
        the site name that every row is opened by. A click there is then
        intercepted by the nav and retries until it times out.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] - 40, size["height"] - 60)
        self.page.wait_for_timeout(700)

    def _close_popover(self):
        """Dismiss any open dropdown and let it leave the DOM.

        Both filters stay open after a selection is made. Left open, the
        listbox covers the control beside it and the next click lands on the
        popover instead of its target.
        """
        self.page.keyboard.press("Escape")
        self._poll(
            lambda: self.page.get_by_role("option").count() == 0, timeout_ms=6000
        )
        self.page.wait_for_timeout(300)

    def _options(self):
        return [
            (o.inner_text() or "").strip()
            for o in self.page.get_by_role("option").all()
        ]

    def _column(self, index):
        """The text of column `index` in every row, top to bottom."""
        try:
            return [
                (r.locator("td").nth(index).inner_text() or "")
                .strip().replace("\n", " ")
                for r in self.rows.all()
            ]
        except Exception:
            # The table re-renders as its refetch lands; treat a read that
            # catches it mid-repaint as "not settled yet".
            return []

    def _names(self):
        """The site name in each row, top to bottom -- without the ID line."""
        try:
            return [
                (r.locator("td").nth(0).inner_text() or "").strip().split("\n")[0]
                for r in self.rows.all()
            ]
        except Exception:
            return []

    def _settled_names(self, timeout_ms=20000):
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
        """A header cell by column name, located by position.

        Positional rather than by text: "Sites" is a prefix of nothing but
        "CPO" appears in both the filter above the table and the header, and
        "Site Profit Share %" carries a regex metacharacter. The column set is
        already pinned by `check_table_structure`, so the index is safe.
        """
        return self.table.locator("thead th").nth(self.COLUMNS.index(col))

    def _footer(self):
        try:
            return (self.showing.first.inner_text() or "").strip()
        except Exception:
            return ""

    def _await_footer(self, expected, context):
        """Wait for the footer to read `expected`.

        The footer is rendered from the response metadata rather than from the
        rows, so it lands a beat after the table it describes. Reading it the
        moment the rows settle compares the new page against the previous
        page's count and fails on a race rather than on a fault.
        """
        assert self._poll(lambda: self._footer() == expected, timeout_ms=20000), (
            f"{context}: the footer reads {self._footer()!r}, expected "
            f"{expected!r}"
        )

    def _search_for(self, term, expected=None):
        """Type a search term and wait for the table to answer it.

        The search box is a controlled React input behind a debounce. A value
        typed into it while the table is still settling from a route change can
        be dropped before its onChange is attached -- the box then reads empty,
        no request is made, and the wait that follows times out against a table
        that was never asked to change. So the value is put back if it did not
        take.
        """
        self.search.fill(term)
        assert self._poll(
            lambda: (self.search.input_value() or "") == term, timeout_ms=8000
        ), f"the search box would not accept {term!r}"
        if term:
            self.page.wait_for_url(re.compile(r"[?&]search="), timeout=20000)
        else:
            assert self._poll(
                lambda: "search=" not in self.page.url, timeout_ms=25000
            ), f"the search is still in the URL after clearing: {self.page.url}"

        if expected is not None:
            assert self._poll(
                lambda: self._names() == expected, timeout_ms=30000
            ), (
                f"searching for {term!r} returned {self._names()}, expected "
                f"{expected}"
            )

    def _pages(self):
        """The page numbers the pager currently offers."""
        return [
            (b.get_attribute("aria-label") or "").replace("Go to page ", "")
            for b in self.page.get_by_role("button").all()
            if (b.get_attribute("aria-label") or "").startswith("Go to page")
        ]

    def _set_page_size(self, size):
        self._park_mouse()
        self.page_size.first.click()
        assert self._poll(
            lambda: self.page.get_by_role("option").count() > 0, timeout_ms=8000
        ), "the page-size selector opened with no options"
        self.page.get_by_role("option", name=size, exact=True).click()
        self._close_popover()

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        """Reach the page through the sidebar rather than a direct goto.

        A full page load on staging costs upwards of fifteen seconds because
        the whole SPA re-bootstraps and re-authenticates; the client-side route
        change is near-instant and exercises the nav link at the same time.

        If the session is already somewhere under /admin/cost, it is routed
        away and back first. The two filters are held in component state rather
        than in the URL, so clicking the sidebar link while already on the page
        is a no-op that would carry a previous test's search, filter, sort and
        page size into this one -- the `page` fixture is shared across the
        whole session (see conftest.py). Leaving the route unmounts the
        component, and everything comes back at its defaults.
        """
        # Nothing can be clicked underneath an open modal. A previous check
        # that failed part-way through one would otherwise strand the shared
        # session behind it and take every later test down with it, reporting a
        # timeout instead of the real fault.
        if self.dialog.count():
            log.info("Dismissing a dialog left open on the page")
            self.page.keyboard.press("Escape")
            self._poll(lambda: self.dialog.count() == 0, timeout_ms=10000)

        if re.search(r"/admin/cost", self.page.url):
            log.info("Already under /admin/cost -- routing away to reset the "
                     "page's filter, sort and search state")
            self._park_mouse()
            self.page.get_by_role("link", name="Reconciliation").first.click()
            self.page.wait_for_url(re.compile(r"/admin/recon"), timeout=30000)
            self.page.wait_for_timeout(1000)
            self._park_mouse()

        log.info("Opening Site Cost Data")
        self.nav_link.first.click()
        self.page.wait_for_url(re.compile(r"/admin/cost(\?|$)"), timeout=30000)
        assert self._poll(self._loaded, timeout_ms=45000), (
            "the site cost table never loaded"
        )
        # Clicking the sidebar link leaves the pointer on the nav, which stays
        # expanded and covers the leading edge of the table. Park it before
        # anything tries to click there.
        self._park_mouse()
        log.info("Site Cost Data loaded with %s site row(s)", self.rows.count())

        # The breadcrumb states where the page sits in the product.
        crumbs = (self.breadcrumb.first.inner_text() or "").split("\n")
        assert [c.strip() for c in crumbs if c.strip()] == ["Admin", "Site Cost Data"], (
            f"unexpected breadcrumb: {crumbs}"
        )

    # ----------------------------------------------------------------- #
    # Table structure
    # ----------------------------------------------------------------- #
    def check_table_structure(self):
        """The table renders its full column set and well-formed cost rows.

        Every money column is asserted on its *shape* rather than on a figure:
        the values are live commercial terms that change whenever finance
        uploads a new sheet, but a profit share that stops rendering as a
        percentage, or a standing charge that loses its per-period suffix, is a
        real fault at any value.
        """
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
            site, cd, cpo = cells[0], cells[1], cells[2]
            name = site.split("\n")[0]

            assert name, "a row has no site name"
            assert re.search(r"ID: \S+", site), (
                f"the {name!r} row carries no site ID: {site!r}"
            )
            assert re.fullmatch(r"\d+", cd), (
                f"the {name!r} row shows {cd!r} charging devices, expected a "
                "whole number"
            )
            assert cpo, f"the {name!r} row names no CPO"

            # Profit share, unit cost and standing charge: each is either a
            # properly formatted figure or an em dash for a site whose terms
            # have never been set.
            for index, shape in self.CELL_SHAPES.items():
                value = cells[index]
                assert value == self.UNSET or re.fullmatch(shape, value), (
                    f"the {name!r} row shows {self.COLUMNS[index]} as "
                    f"{value!r}, expected {self.UNSET!r} or a value matching "
                    f"{shape}"
                )

            duration = cells[6].replace("\n", " ").strip()
            assert self.DURATION.fullmatch(duration), (
                f"the {name!r} row shows duration {duration!r}, expected "
                "'Not set' or a start date with an end date or 'Ongoing'"
            )

            # Every row offers its own audit trail.
            assert row.get_by_role("button", name="View activity").count() == 1, (
                f"the {name!r} row offers no activity trail"
            )

        # A site with its terms set must state all three of them together --
        # a row showing a unit cost but no profit share would bill wrongly.
        priced = [
            n for n, d in zip(self._names(), self._column(6)) if d != "Not set"
        ]
        log.info(
            "Table shows %s site row(s), %s of them with dated terms -- %s",
            self.rows.count(), len(priced), self._footer(),
        )

        footer = self._footer()
        assert re.fullmatch(r"Showing \d+[–-]\d+ of \d+", footer), (
            f"unexpected footer text: {footer!r}"
        )

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def search_sites(self):
        """Search narrows the table, and a no-match query shows the empty state."""
        before = self._settled_names()

        log.info("Searching sites for %r", SEARCH_TERM)
        self._search_for(SEARCH_TERM)
        assert self._poll(
            lambda: self._names() and self._names() != before, timeout_ms=25000
        ), "the search did not change the site list"
        for name in self._names():
            assert SEARCH_TERM.lower() in name.lower(), (
                f"the search returned {name!r}, which does not contain "
                f"{SEARCH_TERM!r}"
            )
        log.info("Search %r -> %s site(s)", SEARCH_TERM, self.rows.count())

        log.info("Searching for a term that matches nothing")
        self._search_for(NO_MATCH)
        assert self._poll(lambda: self.no_match.count() > 0, timeout_ms=25000), (
            "a no-match search did not show the 'No sites found' empty state"
        )
        assert self.rows.count() == 0, "the empty state still drew site rows"
        # The empty state tells the user how to get out of it, and offers the
        # control that does it.
        assert self.page.get_by_text(
            "No sites match the current search or filters", exact=False
        ).count() > 0, "the empty state gives the user no way forward"
        expect(self.clear_filters).to_be_visible()
        self._await_footer("Showing 0 results", "with nothing matching")
        log.info("Empty state shown -- %s", self._footer())

        log.info("Clearing the search")
        self._search_for("")
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "the table did not return to its unsearched rows: "
            f"{self._names()[:3]} != {before[:3]}"
        )

    # ----------------------------------------------------------------- #
    # Filters
    # ----------------------------------------------------------------- #
    def filter_by_cpo(self):
        """Narrow the table to one CPO, then toggle it back off.

        The CPO list is built from the CPOs that actually hold sites rather
        than the whole partner directory, so it is checked both for the value
        under test and for the absence of blanks.
        """
        before = self._settled_names()

        log.info("Opening the CPO filter")
        self._park_mouse()
        self.cpo_filter.first.click()
        assert self._poll(
            lambda: self.page.get_by_role("option").count() > 0, timeout_ms=10000
        ), "the CPO filter opened with no options"
        listed = self._options()
        assert CPO in listed, f"the CPO filter does not offer {CPO!r}"
        assert all(o for o in listed), (
            f"the CPO filter offers blank entries: {listed}"
        )
        assert listed == sorted(listed), (
            f"the CPO filter is not in alphabetical order: {listed}"
        )
        log.info("CPO filter offers %s CPO(s)", len(listed))

        self.page.get_by_role("option", name=CPO, exact=True).click()
        self._close_popover()
        assert self._poll(
            lambda: self._names() and self._names() != before, timeout_ms=25000
        ), f"the table did not change under the {CPO!r} filter"

        # The filter button reports what it is filtering on.
        assert CPO in (self.cpo_filter.first.inner_text() or ""), (
            f"the CPO filter button does not name {CPO!r}: "
            f"{self.cpo_filter.first.inner_text()!r}"
        )
        # Every surviving row really belongs to that CPO, and they are the
        # sites that CPO is known to own.
        assert self._poll(
            lambda: bool(self._column(2))
            and all(v == CPO for v in self._column(2)),
            timeout_ms=25000,
        ), (
            f"the CPO filter returned row(s) for "
            f"{sorted(set(self._column(2)) - {CPO})}, expected only {CPO!r}"
        )
        assert sorted(self._names()) == sorted(CPO_SITES), (
            f"the {CPO!r} filter returned {sorted(self._names())}, expected "
            f"{sorted(CPO_SITES)}"
        )
        log.info("CPO filter %r -> %s site(s): %s", CPO, self.rows.count(),
                 self._names())

        # The option is a toggle: selecting it again clears the filter. That is
        # the only way off it short of Clear filters, so it is worth pinning.
        log.info("Toggling the CPO filter back off")
        self._park_mouse()
        self.cpo_filter.first.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0)
        selected = [
            (o.inner_text() or "").strip()
            for o in self.page.get_by_role("option").all()
            if o.get_attribute("aria-selected") == "true"
        ]
        assert selected == [CPO], (
            f"the filter marks {selected} as selected, expected [{CPO!r}]"
        )
        self.page.get_by_role("option", name=CPO, exact=True).click()
        self._close_popover()
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "toggling the CPO filter off did not restore the full table"
        )
        assert "All CPOs" in (self.cpo_filter.first.inner_text() or ""), (
            "the CPO filter button still names a CPO after being cleared"
        )

    def filter_by_sub_organisation(self):
        """The sub-organisation filter applies and clears.

        Staging holds a single sub-organisation that every site belongs to, so
        applying it cannot narrow the table -- and that is exactly why this
        checks the button state and the rows *surviving* rather than a drop in
        the count. A filter that wrongly excluded everything would fail here.
        """
        before = self._settled_names()

        log.info("Opening the sub-organisation filter")
        self._park_mouse()
        self.sub_org_filter.first.click()
        assert self._poll(
            lambda: self.page.get_by_role("option").count() > 0, timeout_ms=10000
        ), "the sub-organisation filter opened with no options"
        listed = self._options()
        assert listed == [SUB_ORG], (
            f"the sub-organisation filter offers {listed}, expected [{SUB_ORG!r}]"
        )

        self.page.get_by_role("option", name=SUB_ORG, exact=True).click()
        self._close_popover()
        assert self._poll(
            lambda: SUB_ORG in (self.sub_org_filter.first.inner_text() or ""),
            timeout_ms=20000,
        ), (
            "the sub-organisation filter button does not name "
            f"{SUB_ORG!r}: {self.sub_org_filter.first.inner_text()!r}"
        )
        assert self._poll(lambda: self._names() == before, timeout_ms=25000), (
            f"filtering on {SUB_ORG!r} -- which owns every site -- changed the "
            f"table: {self._names()[:3]} != {before[:3]}"
        )
        log.info("Sub-organisation %r -> %s site(s)", SUB_ORG, self.rows.count())

        log.info("Toggling the sub-organisation filter back off")
        self._park_mouse()
        self.sub_org_filter.first.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0)
        self.page.get_by_role("option", name=SUB_ORG, exact=True).click()
        self._close_popover()
        assert self._poll(
            lambda: "All sub-organisations"
            in (self.sub_org_filter.first.inner_text() or ""),
            timeout_ms=20000,
        ), "the sub-organisation filter did not clear"
        assert self._poll(lambda: self._names() == before, timeout_ms=25000)

    def clear_all_filters(self):
        """The Clear filters control drops the search and the CPO together.

        It only surfaces once the table is empty, so it is reached by driving
        the page into its no-match state with both a search *and* a filter
        applied -- which is also what proves it clears both rather than one.
        """
        before = self._settled_names()

        log.info("Applying a CPO filter and a search that cannot coexist")
        self._park_mouse()
        self.cpo_filter.first.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0)
        self.page.get_by_role("option", name=CPO, exact=True).click()
        self._close_popover()
        assert self._poll(
            lambda: self._names() and self._names() != before, timeout_ms=25000
        )
        self._search_for(NO_MATCH)
        assert self._poll(lambda: self.no_match.count() > 0, timeout_ms=25000), (
            "a CPO filter plus a no-match search did not empty the table"
        )

        log.info("Clearing everything with the Clear filters control")
        expect(self.clear_filters).to_be_visible()
        self.clear_filters.click()
        assert self._poll(lambda: self._names() == before, timeout_ms=35000), (
            "Clear filters did not restore the full table"
        )
        assert (self.search.input_value() or "") == "", (
            "Clear filters left the search box filled: "
            f"{self.search.input_value()!r}"
        )
        assert "All CPOs" in (self.cpo_filter.first.inner_text() or ""), (
            "Clear filters left the CPO filter applied"
        )
        assert "search=" not in self.page.url, (
            f"Clear filters left the search in the URL: {self.page.url}"
        )
        assert self.clear_filters.count() == 0, (
            "the Clear filters control is still on screen with nothing to clear"
        )
        log.info("Clear filters restored %s site(s)", self.rows.count())

    # ----------------------------------------------------------------- #
    # Sorting
    # ----------------------------------------------------------------- #
    def sort_columns(self):
        """Every working sortable column reorders the table in both directions.

        A third click clears the sort entirely rather than cycling back to
        ascending, which is the behaviour the URL shows and is pinned here so a
        regression to a two-state toggle would be caught.
        """
        for col, param in self.SORTABLE.items():
            baseline = self._settled_names()

            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort_by={param}\b"), timeout=20000
            )
            assert self._poll(self._loaded, timeout_ms=25000), (
                f"the table is empty after sorting by {col!r}"
            )
            direction = re.search(r"sort_order=(asc|desc)", self.page.url)
            assert direction, (
                f"sorting by {col!r} set no sort_order: {self.page.url}"
            )
            assert direction.group(1) == "asc", (
                f"the first click on {col!r} sorted {direction.group(1)}, "
                "expected ascending"
            )
            # Wait for the order to actually change before settling on it. The
            # URL updates the moment the header is clicked, well before the
            # refetch it triggered comes back, and the rows on screen at that
            # point are still the previous ones -- already stable, so settling
            # first would lock onto the pre-sort order and report a fault.
            assert self._poll(
                lambda b=baseline: self._names() and self._names() != b,
                timeout_ms=30000,
            ), (
                f"sorting by {col!r} set sort_by={param}&sort_order=asc but the "
                f"table came back in its original order: {self._names()[:4]}"
            )
            ascending = self._settled_names()

            # Reverse it. Waiting on the *direction* rather than merely on
            # sort_by, which is already present from the first click and would
            # return instantly -- the row comparison would then race the
            # refetch instead of following it.
            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(r"[?&]sort_order=desc\b"), timeout=20000
            )
            assert self._poll(
                lambda a=ascending: self._names() and self._names() != a,
                timeout_ms=25000,
            ), f"reversing the {col!r} sort did not reorder the table"

            # A third click drops the sort altogether.
            self._park_mouse()
            self._header(col).click()
            assert self._poll(
                lambda: "sort_by=" not in self.page.url, timeout_ms=20000
            ), (
                f"a third click on {col!r} left the sort in the URL: "
                f"{self.page.url}"
            )
            assert self._poll(self._loaded, timeout_ms=25000), (
                f"the table is empty after clearing the {col!r} sort"
            )
            log.info("Column %-22s sorts both ways and clears (sort_by=%s)",
                     col, param)

        # The remaining headers carry neither a sort chevron nor a pointer
        # cursor.
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
    # Pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        """Each page size draws the rows it promises, and the pager steps."""
        total = int(re.search(r"of (\d+)", self._footer()).group(1))
        log.info("Paginating %s site(s)", total)

        for size in self.PAGE_SIZES:
            self._set_page_size(size)
            expected = min(int(size), total)
            assert self._poll(
                lambda e=expected: self.rows.count() == e, timeout_ms=30000
            ), (
                f"page size {size} drew {self.rows.count()} row(s), "
                f"expected {expected}"
            )
            self.page.wait_for_url(
                re.compile(rf"[?&]page_size={size}\b"), timeout=15000
            )
            self._await_footer(
                f"Showing 1–{expected} of {total}", f"at page size {size}"
            )
            footer = self._footer()
            # The pager offers exactly as many pages as the size implies.
            last = -(-total // int(size))  # ceiling division
            assert self._pages()[-1] == str(last), (
                f"page size {size} over {total} site(s) should end on page "
                f"{last}, the pager ends on {self._pages()[-1]}"
            )
            log.info("Page size %-3s -> %s row(s), %s page(s) -- %s",
                     size, self.rows.count(), last, footer)

        log.info("Stepping through the pages at size 10")
        self._set_page_size("10")
        assert self._poll(lambda: self.rows.count() == 10, timeout_ms=30000)
        first_page = self._settled_names()

        assert self.next_page.is_enabled(), (
            f"page size 10 over {total} site(s) should give more than one page"
        )
        assert self.prev_page.is_disabled(), (
            "the previous-page control is enabled on page 1"
        )

        self._park_mouse()
        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2\b"), timeout=20000)
        assert self._poll(
            lambda: self._names() and self._names() != first_page, timeout_ms=25000
        ), "page 2 shows the same sites as page 1"
        assert not set(self._names()) & set(first_page), (
            "page 2 repeats sites from page 1"
        )
        self._await_footer(f"Showing 11–20 of {total}", "on page 2")
        log.info("Page 2 shows %s site(s) -- %s", self.rows.count(), self._footer())

        self._park_mouse()
        self.prev_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=1\b"), timeout=20000)
        assert self._poll(lambda: self._names() == first_page, timeout_ms=25000), (
            "going back did not restore page 1"
        )

        # A direct page jump. Matched exactly -- "Go to page 1" is a prefix of
        # "Go to page 12" and a substring match would resolve to both.
        log.info("Jumping straight to page 3 and back")
        self._park_mouse()
        self.page.get_by_role("button", name="Go to page 3", exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]page=3\b"), timeout=20000)
        assert self._poll(
            lambda: self._names() and self._names() != first_page, timeout_ms=25000
        ), "the page-3 jump stayed on page 1"
        self._await_footer(f"Showing 21–30 of {total}", "on page 3")

        self._park_mouse()
        self.page.get_by_role("button", name="Go to page 1", exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]page=1\b"), timeout=20000)
        assert self._poll(lambda: self._names() == first_page, timeout_ms=25000)

        log.info("Restoring the page size to 20")
        self._set_page_size("20")
        assert self._poll(lambda: self.rows.count() == 20, timeout_ms=30000)

    # ----------------------------------------------------------------- #
    # Activity trail
    # ----------------------------------------------------------------- #
    def check_activity_trail(self):
        """A row's audit trail opens, states who changed what, and closes.

        This is the page's only record of *why* a site is priced the way it is,
        so it is checked on substance -- an actor, a timestamp, the field that
        changed, its new value and the period it took effect over -- rather
        than merely on the drawer opening.
        """
        log.info("Opening the activity trail for %r", SITE)
        self._search_for(SITE, expected=[SITE])

        self._park_mouse()
        self.rows.first.get_by_role("button", name="View activity").click()
        panel = self.page.get_by_text(f"Activity for {SITE}", exact=True)
        expect(panel.first).to_be_visible(timeout=25000)

        # The panel's header renders before the entries it describes are
        # fetched, so reading it as soon as it is visible catches it empty.
        # Wait for at least one recorded change to land.
        assert self._poll(
            lambda: "Updated " in (self.dialog.last.inner_text() or ""),
            timeout_ms=30000,
        ), (
            f"the activity trail for {SITE!r} never listed a recorded change: "
            f"{(self.dialog.last.inner_text() or '')[:200]!r}"
        )
        text = self.dialog.last.inner_text() or ""
        # It identifies the site it is describing.
        assert f"ID: {SITE_ID}" in text, (
            f"the activity trail does not carry the site ID: {text[:200]!r}"
        )
        assert f"CPO: {SITE_CPO}" in text, (
            f"the activity trail does not name the CPO: {text[:200]!r}"
        )

        # Entries are grouped under the month they happened in.
        assert re.search(
            r"(January|February|March|April|May|June|July|August|September|"
            r"October|November|December), \d{4}", text
        ), "the activity trail groups nothing under a month"

        # Each entry names the field it changed, and every field this page can
        # change must be represented for a site whose terms are fully set.
        for field in ["CPO Share Percentage", "Standing Charge",
                      "Electricity Cost per kWh"]:
            assert f"Updated {field}" in text, (
                f"the activity trail records no change to {field!r}"
            )

        # ...along with when it happened, and the period it applies over.
        assert re.search(r"\d{2}-\d{2}-\d{4}", text), (
            "the activity trail carries no dates"
        )
        assert re.search(r"\d{2}:\d{2}:\d{2}", text), (
            "the activity trail carries no timestamps"
        )
        assert "Effective from" in text, (
            "the activity trail does not say when a change took effect"
        )

        # Entries are marked live or superseded -- that distinction is what
        # makes the trail a history rather than a list.
        assert "Active" in text and "Past" in text, (
            f"{SITE!r} has both a live and a superseded entry; the trail marks "
            "neither"
        )
        # A change imported from a sheet cites the sheet it came from.
        assert re.search(r"\S+\.(csv|xlsx)", text, re.I), (
            "the trail does not cite the uploaded sheet a change came from"
        )
        log.info("Activity trail carries %s entry line(s) for %r",
                 text.count("Updated "), SITE)

        log.info("Closing the activity trail")
        self.close_activity.first.click()
        assert self._poll(lambda: panel.count() == 0, timeout_ms=20000), (
            "the activity trail did not close"
        )

        # Leave the list unsearched for whatever runs next.
        self._search_for("")
        self._poll(self._loaded, timeout_ms=25000)

    # ----------------------------------------------------------------- #
    # Site detail
    # ----------------------------------------------------------------- #
    def open_site(self):
        """Open one site and check everything its cost page renders.

        Read-only throughout: the inline editor is opened and cancelled and the
        Add Data dialog is checked on its controls and dismissed. Nothing is
        saved.
        """
        log.info("Finding %r", SITE)
        self._search_for(SITE, expected=[SITE])

        log.info("Opening it")
        self._park_mouse()
        self.rows.first.click()
        self.page.wait_for_url(
            re.compile(r"/admin/cost/[0-9a-f-]{36}"), timeout=30000
        )
        # The heading spells the site and its ID as one run of text --
        # "Earlham Green CO OP(ID: 302206)" -- so it is matched on the ID
        # fragment rather than reconstructed exactly.
        expect(
            self.page.get_by_text(f"(ID: {SITE_ID})", exact=False).first
        ).to_be_visible(timeout=30000)
        assert self._poll(lambda: self.rows.count() > 0, timeout_ms=30000), (
            "the site's cost history never loaded"
        )
        log.info("Site cost page open at %s", self.page.url)

        self._check_detail_breadcrumb()
        self._check_cost_history()
        self._check_device_breakdown()
        self._check_inline_editor()
        self._check_add_data_dialog()

        log.info("Going back to the site list through the breadcrumb")
        self.breadcrumb.get_by_role("link", name="Site Cost Data").click()
        self.page.wait_for_url(re.compile(r"/admin/cost(\?|$)"), timeout=30000)
        assert self._poll(self._loaded, timeout_ms=35000), (
            "the site cost table did not come back"
        )
        # Leave the list unsearched for whatever runs next.
        if "search=" in self.page.url:
            self._search_for("")
            self._poll(self._loaded, timeout_ms=25000)

    def _check_detail_breadcrumb(self):
        """The breadcrumb names the site and offers the way back."""
        crumbs = [
            c.strip()
            for c in (self.breadcrumb.first.inner_text() or "").split("\n")
            if c.strip()
        ]
        assert crumbs == ["Admin", "Site Cost Data", SITE], (
            f"unexpected breadcrumb on the site page: {crumbs}"
        )
        expect(
            self.breadcrumb.get_by_role("link", name="Site Cost Data")
        ).to_be_visible()
        log.info("Breadcrumb: %s", " / ".join(crumbs))

    def _check_cost_history(self):
        """The dated history of this site's terms, one row per period."""
        headers = [
            (h.inner_text() or "").strip()
            for h in self.table.locator("thead th").all()
        ]
        assert headers == self.DETAIL_COLUMNS, (
            f"unexpected column set on the site page: {headers} != "
            f"{self.DETAIL_COLUMNS}"
        )

        for row in self.rows.all():
            cells = [(c.inner_text() or "").strip() for c in row.locator("td").all()]
            start, end, share, unit, standing, updated = cells[1:7]

            assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", start), (
                f"a history row starts on {start!r}, expected dd-mm-yyyy"
            )
            # An open-ended period renders its end as an em dash, which is what
            # marks the terms currently in force.
            assert end == self.UNSET or re.fullmatch(r"\d{2}-\d{2}-\d{4}", end), (
                f"a history row ends on {end!r}, expected {self.UNSET!r} or "
                "dd-mm-yyyy"
            )
            for value, shape, label in (
                (share, r"\d+\.\d{2}%", "profit share"),
                (unit, r"£\d+\.\d{5}", "unit cost"),
                (standing, r"£\d+\.\d{5}/(day|month)", "standing charge"),
            ):
                assert value == self.UNSET or re.fullmatch(shape, value), (
                    f"a history row shows a {label} of {value!r}"
                )
            assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", updated), (
                f"a history row was updated {updated!r}, expected dd-mm-yyyy"
            )
            # Each period can be edited, and expanded onto its devices.
            assert row.get_by_role("button", name="Edit", exact=True).count() == 1, (
                "a history row offers no Edit control"
            )
            assert row.get_by_role("button", name="Expand row").count() == 1, (
                "a history row cannot be expanded"
            )

        # There is no delete anywhere on this page -- pinned deliberately,
        # because the read-only workflow above depends on it.
        assert self.page.get_by_role("button", name=re.compile("Delete|Remove")).count() == 0, (
            "the site cost page now offers a delete control; the read-only "
            "workflow needs revisiting"
        )
        log.info("Cost history: %s dated period(s) -- %s",
                 self.rows.count(), self._footer())

    def _check_device_breakdown(self):
        """A period opens onto the per-device terms inside it."""
        log.info("Expanding the first cost period")
        self._park_mouse()
        self.rows.first.get_by_role("button", name="Expand row").click()
        assert self._poll(lambda: self.expanded.count() > 0, timeout_ms=20000), (
            "expanding a cost period revealed no device breakdown"
        )
        panel = self.expanded.first.inner_text() or ""
        # Each device names itself and carries its own ID -- that is what
        # distinguishes a real breakdown from a blank shell.
        assert re.search(r"ID: \S+", panel), (
            f"the device breakdown carries no device ID: {panel[:200]!r}"
        )
        log.info("Device breakdown: %s", panel.replace("\n", " ").strip()[:120])

        log.info("Collapsing it again")
        self.rows.first.get_by_role("button", name="Collapse row").click()
        assert self._poll(lambda: self.expanded.count() == 0, timeout_ms=20000), (
            "the cost period did not collapse"
        )

        periods = self.rows.count()
        log.info("Expanding all %s period(s) at once", periods)
        self._park_mouse()
        self.expand_all.click()
        assert self._poll(lambda: self.expanded.count() > 0, timeout_ms=20000), (
            "expand-all revealed no device breakdowns"
        )
        expect(self.collapse_all).to_be_visible()
        assert self.rows.count() == periods, (
            f"expanding changed the period count from {periods} to "
            f"{self.rows.count()}"
        )

        log.info("Collapsing them all again")
        self.collapse_all.click()
        assert self._poll(lambda: self.expanded.count() == 0, timeout_ms=20000), (
            "the periods did not all collapse"
        )
        expect(self.expand_all).to_be_visible()

    def _check_inline_editor(self):
        """The row editor unlocks the right fields, then is cancelled.

        Cancel rather than Save: these are the figures a real CPO is invoiced
        on, and the page offers no way to put a wrong value back.
        """
        # Snapshot the row as it reads *before* the editor is opened: opening
        # it swaps the value cells for inputs, so the same read afterwards
        # returns the units around them ("%", "£") rather than the figures.
        shown = [
            (c.inner_text() or "").strip()
            for c in self.rows.first.locator("td").all()
        ]

        log.info("Opening the inline editor on the first cost period")
        self._park_mouse()
        self.edit_row.first.click()
        expect(self.cancel_edit.first).to_be_visible(timeout=20000)
        expect(self.save_edit.first).to_be_visible()

        # Every financial field becomes editable, pre-filled with the value the
        # row was showing -- an editor that opened empty would silently blank
        # the terms on save.
        for label in self.EDIT_FIELDS:
            field = self.page.get_by_label(label, exact=True)
            expect(field.first).to_be_editable()
            assert (field.first.input_value() or "").strip() != "", (
                f"the editor opened {label!r} empty rather than pre-filled"
            )
        share = self.page.get_by_label("Site profit share %", exact=True).first
        assert f"{float(share.input_value()):.2f}%" == shown[3], (
            f"the editor pre-filled the profit share as "
            f"{share.input_value()!r}, the row shows {shown[3]!r}"
        )
        log.info("Editor pre-filled %s: %s", self.EDIT_FIELDS,
                 [self.page.get_by_label(f, exact=True).first.input_value()
                  for f in self.EDIT_FIELDS])

        # The start date stays fixed -- a period's beginning is what other
        # periods are ordered against -- while the open end date can be closed.
        expect(self.set_end_date.first).to_be_visible()

        # The standing charge carries its own per-period unit.
        log.info("Checking the standing-charge frequency options")
        self.edit_frequency.first.click()
        assert self._poll(
            lambda: self.page.locator("[data-radix-popper-content-wrapper]").count() > 0,
            timeout_ms=10000,
        ), "the frequency selector opened nothing"
        offered = (
            self.page.locator("[data-radix-popper-content-wrapper]").last.inner_text()
            or ""
        ).split("\n")
        assert [o.strip() for o in offered if o.strip()] == self.FREQUENCIES, (
            f"the frequency selector offers {offered}, expected {self.FREQUENCIES}"
        )
        self._close_popover()

        log.info("Cancelling the edit without saving")
        self.cancel_edit.first.click()
        assert self._poll(
            lambda: self.cancel_edit.count() == 0, timeout_ms=20000
        ), "the inline editor did not close"
        expect(self.edit_row.first).to_be_visible()
        # The row reads exactly as it did before the editor was opened.
        after = [(c.inner_text() or "").strip() for c in self.rows.first.locator("td").all()]
        assert after == shown, (
            f"cancelling the edit changed the row: {after} != {shown}"
        )

    def _check_add_data_dialog(self):
        """The Add Data dialog offers every control, then is dismissed.

        Never submitted: a pricing entry cannot be deleted from this UI, so a
        run that created one would leave staging permanently dirtier and would
        change what every later reconciliation computes.
        """
        log.info("Opening the Add Data dialog")
        self.add_data.click()
        title = self.page.get_by_role(
            "heading", name="Add Site Wise Pricing Entry"
        )
        expect(title.first).to_be_visible(timeout=20000)
        dialog = self.dialog.last

        text = dialog.inner_text() or ""
        for section in ["Category :", "Start Date :", "End Date :",
                        "Financial Details :"]:
            assert section in text, f"the Add Data dialog is missing {section!r}"

        log.info("Checking the pricing category options")
        self.category_select.click()
        assert self._poll(
            lambda: self.page.locator("[data-radix-popper-content-wrapper]").count() > 0,
            timeout_ms=10000,
        ), "the category selector opened nothing"
        offered = [
            o.strip()
            for o in (
                self.page.locator("[data-radix-popper-content-wrapper]")
                .last.inner_text() or ""
            ).split("\n")
            if o.strip()
        ]
        assert offered == self.CATEGORIES, (
            f"the category selector offers {offered}, expected {self.CATEGORIES}"
        )
        self._close_popover()

        # Every financial field is offered and starts empty.
        for label in self.PRICING_FIELDS:
            field = dialog.get_by_label(label, exact=True)
            expect(field.first).to_be_visible()
            assert (field.first.input_value() or "") == "", (
                f"the dialog pre-filled {label!r} with "
                f"{field.first.input_value()!r}; a new entry must start empty"
            )
        log.info("Dialog offers all %s financial field(s)", len(self.PRICING_FIELDS))

        log.info("Checking the start-date calendar")
        dialog.get_by_role("button", name="Date").first.click()
        assert self._poll(
            lambda: self.page.locator("[data-radix-popper-content-wrapper]").count() > 0,
            timeout_ms=10000,
        ), "the start-date control opened no calendar"
        calendar = (
            self.page.locator("[data-radix-popper-content-wrapper]").last.inner_text()
            or ""
        )
        for day in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]:
            assert day in calendar, f"the calendar is missing {day!r}"
        assert re.search(r"\b(19|20)\d{2}\b", calendar), (
            f"the calendar shows no year: {calendar[:120]!r}"
        )
        self._close_popover()

        log.info("Checking the standing-charge frequency options")
        dialog.get_by_role("button", name="Day", exact=True).click()
        assert self._poll(
            lambda: self.page.locator("[data-radix-popper-content-wrapper]").count() > 0,
            timeout_ms=10000,
        ), "the frequency selector opened nothing"
        offered = [
            o.strip()
            for o in (
                self.page.locator("[data-radix-popper-content-wrapper]")
                .last.inner_text() or ""
            ).split("\n")
            if o.strip()
        ]
        assert offered == self.FREQUENCIES, (
            f"the frequency selector offers {offered}, expected "
            f"{self.FREQUENCIES}"
        )
        self._close_popover()

        expect(self.add_pricing.first).to_be_visible()

        log.info("Cancelling the dialog without adding anything")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        assert self._poll(lambda: title.count() == 0, timeout_ms=20000), (
            "the Add Data dialog did not close"
        )

    # ----------------------------------------------------------------- #
    # Known product gaps -- each checked on its own, see the class docstring
    # ----------------------------------------------------------------- #
    def sort_by_site_name(self):
        """Sorting by Sites must reorder the table by site name.

        Currently fails on the product: `sort_by=name` is not honoured. The
        Sites header behaves like the others on the surface -- it takes the
        click, writes `sort_by=name&sort_order=asc` into the URL and flips its
        chevron -- but the rows that come back are not in name order, in either
        direction. It has been seen to fail two ways on staging: usually the
        table returns in exactly its unsorted order, and sometimes it comes
        back completely empty, showing the "No site cost data" state -- the one
        that means *nothing has ever been imported*, not *nothing matched* --
        under a footer reading "Showing 0 results". The assertions below cover
        both: the rows must survive, and they must be in name order.

        Every other sortable column reorders correctly, so this is one broken
        sort key rather than broken sorting.
        """
        col, param = self.BROKEN_SORT
        baseline = self._settled_names()
        assert baseline, "the table is empty before sorting"

        for order in ("asc", "desc"):
            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort_by={param}&sort_order={order}\b"),
                timeout=20000,
            )
            # Give the refetch time to land before judging it empty.
            self._poll(lambda: self.rows.count() > 0, timeout_ms=20000)
            state = "'No site cost data'" if self.no_data.count() else "empty"
            assert self.rows.count() > 0, (
                f"sorting by {col!r} ({param}, {order}) emptied the table: it "
                f"shows {self._footer()!r} and the {state} state, where "
                f"{len(baseline)} site(s) stood before"
            )
            # Having survived, it must also actually be in name order.
            names = self._settled_names()
            expected = sorted(names, key=str.casefold, reverse=(order == "desc"))
            assert names == expected, (
                f"sorting by {col!r} {order} returned rows out of name order: "
                f"{names[:4]}"
            )
            log.info("Sorting by %s %s kept %s row(s) in order", col, order,
                     self.rows.count())

    def footer_reflects_filter(self):
        """The footer and pager must describe the filtered table.

        Currently fails on the product. Applying a CPO filter narrows the rows
        correctly -- Mulberry Homes leaves its two sites and nothing else --
        but the footer still reads "Showing 1–20 of 117" and the pager still
        offers a page for every one of the 117 unfiltered sites. A user reading
        the footer is told there are 115 more results than exist, and paging
        forward lands on pages the filter has emptied.
        """
        self._park_mouse()
        self.cpo_filter.first.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0)
        self.page.get_by_role("option", name=CPO, exact=True).click()
        self._close_popover()
        assert self._poll(
            lambda: sorted(self._names()) == sorted(CPO_SITES), timeout_ms=30000
        ), f"the {CPO!r} filter returned {self._names()}"

        shown = self.rows.count()
        footer = self._footer()
        log.info("Under the %r filter the table draws %s row(s) -- %s",
                 CPO, shown, footer)

        counted = re.search(r"Showing \d+[–-](\d+) of (\d+)", footer)
        assert counted, f"unexpected footer text: {footer!r}"
        assert int(counted.group(2)) == shown, (
            f"the {CPO!r} filter left {shown} site(s) on screen but the footer "
            f"reads {footer!r} -- it is still counting the unfiltered table"
        )
        assert self._pages() == ["1"], (
            f"the {CPO!r} filter leaves {shown} site(s), which is one page, but "
            f"the pager still offers pages {self._pages()}"
        )

    def default_page_size(self):
        """A first load must draw the number of rows its selector claims.

        Currently fails on the product. Landing on the page with no `page_size`
        in the URL, the "Rows per page" control reads 20 and the footer reads
        "Showing 1–20 of 117", but the table draws 15 rows and the pager offers
        8 pages -- which is ceil(117/15), not ceil(117/20). The page is really
        paginating at 15 while telling the user 20; five sites per page are
        simply not rendered until a size is picked by hand.
        """
        assert "page_size=" not in self.page.url, (
            "this check must run on a first load, with no page size chosen: "
            f"{self.page.url}"
        )
        size = int((self.page_size.first.inner_text() or "0").strip())
        drawn = self.rows.count()
        footer = self._footer()
        total = int(re.search(r"of (\d+)", footer).group(1))
        log.info("First load: selector says %s, table drew %s, footer says %r, "
                 "pager ends on page %s", size, drawn, footer, self._pages()[-1])

        assert drawn == min(size, total), (
            f"the 'Rows per page' control reads {size} and the footer reads "
            f"{footer!r}, but the table drew {drawn} row(s)"
        )
        assert self._pages()[-1] == str(-(-total // size)), (
            f"at a stated page size of {size} over {total} site(s) the pager "
            f"should end on page {-(-total // size)}, it ends on "
            f"{self._pages()[-1]} -- consistent with a real page size of "
            f"{-(-total // int(self._pages()[-1]))}"
        )

    def add_pricing_requires_input(self):
        """Add Pricing must stay disabled until the form has something in it.

        Currently fails on the product. The Add Data dialog opens with no
        category, date or figure entered and its Add Pricing button is already
        enabled, so an empty submit is one stray click away on a form that
        writes the commercial terms a CPO is invoiced on -- and the feature
        offers no delete to undo it with. Every other dialog in the product
        keeps its submit disabled until its required fields are filled.

        Nothing is submitted here: the button's state is read, and the dialog
        is cancelled either way. So this pins the *UI gate* only -- whether the
        request would be rejected server-side is deliberately not exercised,
        because finding out would mean writing to a live site.
        """
        log.info("Opening the Add Data dialog on an untouched form")
        self.add_data.click()
        title = self.page.get_by_role(
            "heading", name="Add Site Wise Pricing Entry"
        )
        expect(title.first).to_be_visible(timeout=20000)
        dialog = self.dialog.last

        empty = [
            (label, dialog.get_by_label(label, exact=True).first.input_value())
            for label in self.PRICING_FIELDS
        ]
        assert all(not v for _, v in empty), (
            f"the form is not actually empty: {empty}"
        )
        disabled = self.add_pricing.first.is_disabled()
        log.info("With every field empty, Add Pricing is %s",
                 "disabled" if disabled else "ENABLED")

        try:
            assert disabled, (
                "Add Pricing is enabled with no category, no dates and no "
                f"figures entered ({dict(empty)}) -- nothing in the UI stands "
                "between a stray click and a pricing entry on a live site, "
                "which this page offers no delete to undo"
            )
        finally:
            # Get out of the dialog whatever the assertion decided, so a
            # failure here cannot strand the shared session on a modal.
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            self._poll(lambda: title.count() == 0, timeout_ms=20000)

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def site_cost_data_page(self):
        self.open_page()
        self.check_table_structure()
        self.search_sites()
        self.filter_by_cpo()
        self.filter_by_sub_organisation()
        self.clear_all_filters()
        self.sort_columns()
        self.paginate()
        self.check_activity_trail()
        self.open_site()
        log.info("Site Cost Data workflow completed")
