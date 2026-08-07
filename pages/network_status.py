import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.network_status")

# The CPO the filter checks are pinned to. Capurro Garage owns a single site on
# staging, so applying it collapses the list from 124 to 1 -- a change large
# enough to be unambiguous.
CPO = "Capurro Garage"

# The status the tile / dropdown cross-check is pinned to. Faulted is the
# smallest non-empty bucket, so it visibly narrows the table.
STATUS = "Faulted"


class network_status:
    """Network Status (/operations/network-status).

    The live health view of the estate: four socket-status tiles that double as
    filters, a searchable and sortable site table, three multi-select filters,
    and rows that expand three levels deep (site -> charging device -> socket).

    The workflow is entirely read-only -- the page creates, edits and deletes
    nothing -- so it always leaves staging exactly as it found it. It exercises
    every control: all four status tiles and their tooltips, the table's column
    set and its sortable and non-sortable columns, the search box and its empty
    state, each of the three multi-select filters (checked, applied and
    cleared), the full expand hierarchy and expand/collapse-all, the alert
    badge, and pagination with the page-size selector.
    """

    # The table's full column set. The first column holds the row expander and
    # the second carries a live site count, so both are matched loosely.
    COLUMNS = ["", "Sites", "CPO", "CD", "Access", "Availability",
               "Connectivity", "Admin Status", "Alerts", "Last Seen"]

    # Sortable columns and the `sort` value each sends. Everything else in the
    # header is deliberately not sortable -- see NOT_SORTABLE.
    SORTABLE = {
        "Sites": "name",
        "Availability": "availability",
        "Connectivity": "connectivity",
        "Alerts": "alerts",
    }
    NOT_SORTABLE = ["CPO", "CD", "Access", "Admin Status", "Last Seen"]

    # The four socket-status tiles, in render order, with the aria-label each
    # one exposes and the phrase its tooltip must contain.
    TILES = {
        "AVAILABLE": ("Filter by Available", "free to start a session"),
        "IN USE": ("Filter by In Use", "session currently in progress"),
        "FAULTED": ("Filter by Faulted", "hardware or OCPP fault"),
        "OFFLINE": ("Filter by Offline", "lost their backhaul connection"),
    }

    # The legend under the filters.
    CONNECTIVITY_STATES = ["Connected", "Partial", "Not Connected"]
    ADMIN_STATES = ["In Service", "Under Maintenance", "Out of Service",
                    "Unknown / Mixed"]

    # The three multi-select filters, mapped to the query parameter each drives.
    #
    # Keyed on the *label* each control carries rather than on its current
    # value: the button's accessible name is the two run together ("Sub-
    # Organisation All sub-organisations"), and the value half changes the
    # moment a filter is applied. Matching on the leading label is therefore
    # the only stable handle -- an exact match on the unfiltered value stops
    # resolving as soon as anything is selected.
    FILTERS = {
        "Sub-Organisation": "organization_id",
        "CPO": "cpo_id",
        "Availability": "availability",
    }

    # The availability dropdown's options -- the same four buckets as the tiles.
    AVAILABILITY_OPTIONS = ["Available", "In Use", "Faulted", "Offline"]

    PAGE_SIZES = ["10", "20", "50", "100"]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.ns_link = page.get_by_role("link", name="Network Status")
        self.heading = page.locator("//h1[normalize-space()='Network Status']")

        # Search
        self.search = page.get_by_placeholder(
            re.compile(r"^Search sites, charging devices")
        )

        # Table. Expanding a row injects a *nested* table inside the outer
        # table's body, so rows are taken as direct children only -- a plain
        # "table tbody tr" would also count the nested device and socket rows.
        self.table = page.locator("table").first
        self.rows = self.table.locator("> tbody > tr")
        self.all_rows = page.locator("table tbody tr")

        # Row expansion
        self.expand_row = page.get_by_role("button", name="Expand row")
        self.collapse_row = page.get_by_role("button", name="Collapse row")
        self.expand_all = page.get_by_role("button", name="Expand all rows")
        self.collapse_all = page.get_by_role("button", name="Collapse all rows")

        # Tiles + their info tooltips
        self.tooltip_buttons = page.get_by_role("button", name="More information")

        # Alerts badge on a row
        self.alert_badges = page.get_by_role(
            "button", name=re.compile(r"open alert")
        )

        # Empty state
        self.empty_state = page.get_by_text("No sites found", exact=True)
        self.empty_clear = page.get_by_role("button", name="Clear filters")

        # Pagination
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$")
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _poll(self, predicate, timeout_ms=15000, interval_ms=250):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        The table refetches on every filter, sort and page change, so state is
        polled until it settles rather than raced with a fixed sleep. The
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
        while hovered, which covers the table's first column -- the row
        expanders. Any click there is then intercepted by the nav and retries
        until it times out, so the pointer is parked on the right before
        touching anything near the left edge.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] - 40, size["height"] // 2)
        self.page.wait_for_timeout(700)

    def _site_count(self):
        """The count the table header advertises, from "Sites (124)"."""
        header = self.table.locator("thead th").nth(1).inner_text() or ""
        match = re.search(r"\((\d+)\)", header)
        assert match, f"the Sites header carries no count: {header!r}"
        return int(match.group(1))

    def _names(self):
        """The site name in each row, top to bottom."""
        try:
            return [
                (r.locator("td").nth(1).inner_text() or "").strip().split("\n")[0]
                for r in self.rows.all()
            ]
        except Exception:
            # The table re-renders as its refetch lands; treat a read that
            # catches it mid-repaint as "not settled yet".
            return []

    def _loaded(self):
        return self.rows.count() > 0 and all(self._names())

    def _settled_names(self, timeout_ms=15000):
        """The row order, once it has stopped changing.

        The table repaints a moment after its refetch resolves, so a baseline
        captured immediately after a previous action can still be the old order
        -- which then looks like the *next* click reordered the table. This
        waits for two consecutive identical reads before returning.
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

    def _header(self, col):
        """The header cell whose label starts with `col`."""
        return self.table.locator("thead th").filter(
            has_text=re.compile(rf"^{re.escape(col)}")
        ).first

    def _tile(self, label):
        return self.page.get_by_role("button", name=self.TILES[label][0])

    def _dismiss_tooltip(self):
        """Close an open tooltip and let it leave the DOM.

        Parked mid-header rather than in the corner: the corner sits over the
        sidebar, which then expands and blocks the next hover.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] // 2, 30)
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(250)

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        log.info("Opening Network Status")
        self.ns_link.click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-status"), timeout=20000
        )
        self.heading.wait_for(state="visible", timeout=20000)
        assert self._poll(self._loaded, timeout_ms=40000), (
            "the site table never loaded"
        )
        log.info("Network Status loaded with %s site(s) across %s row(s)",
                 self._site_count(), self.rows.count())

    # ----------------------------------------------------------------- #
    # Status tiles
    # ----------------------------------------------------------------- #
    def check_status_tiles(self):
        """All four socket-status tiles render a count and start unfiltered."""
        for label, (aria, _) in self.TILES.items():
            tile = self.page.get_by_role("button", name=aria)
            expect(tile).to_be_visible()
            text = (tile.inner_text() or "").replace("\n", " ")
            assert label in text, f"the {aria!r} tile is not labelled {label!r}"
            assert re.search(r"\d+", text), (
                f"the {label} tile shows no socket count: {text!r}"
            )
            assert "Socket" in text, (
                f"the {label} tile does not say what it counts: {text!r}"
            )
            assert tile.get_attribute("aria-pressed") == "false", (
                f"the {label} tile starts in a filtered state"
            )
            log.info("Tile %-10s -> %s", label, text)

    def check_tile_tooltips(self):
        """Each status tile carries an info tooltip explaining that status."""
        count = self.tooltip_buttons.count()
        assert count == len(self.TILES), (
            f"expected {len(self.TILES)} info icons, found {count}"
        )
        tooltip = self.page.get_by_role("tooltip")
        for index, (label, (_, phrase)) in enumerate(self.TILES.items()):
            # Wait for the previous tooltip to leave the DOM first: it lingers
            # while it fades, and reading through that window returns the
            # *previous* tile's text, silently shifting every result by one.
            assert self._poll(lambda: tooltip.count() == 0, timeout_ms=8000), (
                f"the previous tooltip never closed before hovering {label!r}"
            )
            self.tooltip_buttons.nth(index).hover()
            assert self._poll(lambda: tooltip.count() > 0, timeout_ms=8000), (
                f"the {label} info icon raised no tooltip"
            )
            text = (tooltip.first.inner_text() or "").strip()
            assert phrase.lower() in text.lower(), (
                f"the {label} tooltip should mention {phrase!r} but reads {text!r}"
            )
            log.info("Tooltip %-10s -> %s", label, text[:64])
            self._dismiss_tooltip()
        log.info("All %s tile tooltips explain the right status", count)

    # ----------------------------------------------------------------- #
    # Table structure + legend
    # ----------------------------------------------------------------- #
    def check_table_structure(self):
        """The table renders its full column set and a full page of sites."""
        headers = [
            (h.inner_text() or "").strip()
            for h in self.table.locator("thead th").all()
        ]
        log.info("Table columns: %s", headers)
        assert len(headers) == len(self.COLUMNS), (
            f"expected {len(self.COLUMNS)} columns, got {len(headers)}: {headers}"
        )
        for expected, actual in zip(self.COLUMNS, headers):
            assert actual.startswith(expected), (
                f"expected a {expected!r} column, found {actual!r}"
            )

        size = int((self.page_size.first.inner_text() or "0").strip())
        assert self.rows.count() == size, (
            f"expected {size} rows on page 1, got {self.rows.count()}"
        )
        # The header count is the whole estate, not just this page.
        assert self._site_count() >= self.rows.count(), (
            f"the header claims {self._site_count()} sites but the page shows "
            f"{self.rows.count()}"
        )
        log.info("Table shows %s of %s site(s)", self.rows.count(), self._site_count())

    def check_legend(self):
        """The connectivity and admin-status legends are both spelled out."""
        body = self.page.locator("body").inner_text() or ""
        for state in self.CONNECTIVITY_STATES:
            assert state in body, f"the connectivity legend is missing {state!r}"
        for state in self.ADMIN_STATES:
            assert state in body, f"the admin legend is missing {state!r}"
        log.info("Legend lists %s connectivity and %s admin states",
                 len(self.CONNECTIVITY_STATES), len(self.ADMIN_STATES))

    # ----------------------------------------------------------------- #
    # Status tile as a filter
    # ----------------------------------------------------------------- #
    def filter_by_status_tile(self):
        """A status tile toggles a filter on and back off.

        Asserted on the URL, the tile's own pressed state, the header count and
        the rows -- and every remaining row must actually carry that status, so
        a tile that filters to the wrong bucket is caught.
        """
        before_rows = self.rows.count()
        before_total = self._site_count()
        tile = self._tile(STATUS.upper())

        log.info("Filtering by the %s tile", STATUS)
        self._park_mouse()
        tile.click()
        self.page.wait_for_url(
            re.compile(rf"[?&]availability={STATUS.lower()}"), timeout=15000
        )
        assert self._poll(
            lambda: self._site_count() < before_total and self._loaded()
        ), (
            f"the {STATUS} tile did not narrow the list from {before_total} "
            f"(now {self._site_count()})"
        )
        expect(tile).to_have_attribute("aria-pressed", "true")
        assert self.rows.count() == self._site_count(), (
            f"the header says {self._site_count()} sites but {self.rows.count()} "
            f"rows are shown"
        )
        # Every surviving row really is in that state.
        self._assert_column("Availability", STATUS)
        log.info("%s tile -> %s site(s)", STATUS, self._site_count())

        log.info("Clicking the %s tile again to clear it", STATUS)
        self._park_mouse()
        tile.click()
        assert self._poll(lambda: self._site_count() == before_total), (
            f"expected {before_total} site(s) after clearing the tile, "
            f"got {self._site_count()}"
        )
        expect(tile).to_have_attribute("aria-pressed", "false")
        assert self._poll(lambda: self.rows.count() == before_rows)

    def _assert_column(self, col, expected):
        """Every row's `col` cell mentions `expected`."""
        index = [
            (h.inner_text() or "").strip()
            for h in self.table.locator("thead th").all()
        ]
        position = next(
            i for i, h in enumerate(index) if h.startswith(col)
        )
        offenders = []
        for row in self.rows.all():
            value = (row.locator("td").nth(position).inner_text() or "").strip()
            if expected.lower() not in value.lower():
                offenders.append(value)
        assert not offenders, (
            f"the {col} column should mention {expected!r} on every row, but "
            f"{len(offenders)} row(s) differ -> {offenders[:3]}"
        )

    # ----------------------------------------------------------------- #
    # Multi-select filters
    # ----------------------------------------------------------------- #
    def check_filter_options(self):
        """Each multi-select filter opens with a searchable option list."""
        for trigger in self.FILTERS:
            self.page.get_by_role(
                "button", name=re.compile(rf"^{re.escape(trigger)}\b")
            ).first.click()
            assert self._poll(
                lambda: self.page.get_by_role("option").count() > 0
            ), f"the {trigger!r} filter opened with no options"
            options = self.page.get_by_role("option").count()
            popover = self.page.get_by_role("dialog").last
            expect(popover.get_by_role("button", name="Apply")).to_be_visible()
            expect(popover.get_by_role("button", name="Clear")).to_be_visible()
            log.info("Filter %-22s -> %s option(s)", trigger, options)
            if trigger == "All availability":
                listed = [
                    o.inner_text().strip()
                    for o in self.page.get_by_role("option").all()
                ]
                assert listed == self.AVAILABILITY_OPTIONS, (
                    f"the availability filter offers {listed}, expected "
                    f"{self.AVAILABILITY_OPTIONS}"
                )
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(800)

    def filter_by_cpo(self):
        """Apply the CPO filter through its popover, then clear it.

        These are multi-select filters: ticking an option changes nothing until
        Apply is pressed, and dismissing the popover discards the tick. That is
        the whole point of this check -- a test that clicks the option and
        expects the table to move would pass on a broken Apply.
        """
        before = self._site_count()
        self._apply_filter("All CPOs", CPO, "cpo_id")
        expect(
            self.page.get_by_role("button", name=f"CPO: {CPO}")
        ).to_be_visible()
        assert self._poll(lambda: self._site_count() < before), (
            f"the CPO filter did not narrow the list from {before}"
        )
        self._assert_column("CPO", CPO)
        log.info("CPO filter %r -> %s site(s)", CPO, self._site_count())
        self._clear_filter(f"CPO: {CPO}", "cpo_id", before)

    def filter_by_availability_dropdown(self):
        """The availability dropdown drives the same filter as the tile.

        Applying "Faulted" here must also light up the Faulted tile -- they are
        two controls over one piece of state, and this is what proves they stay
        in step.
        """
        before = self._site_count()
        self._apply_filter("All availability", STATUS, "availability")
        expect(
            self.page.get_by_role("button", name=f"Availability: {STATUS}")
        ).to_be_visible()
        assert self._poll(lambda: self._site_count() < before), (
            f"the availability filter did not narrow the list from {before}"
        )
        # The cross-check: the matching tile is now pressed.
        expect(self._tile(STATUS.upper())).to_have_attribute(
            "aria-pressed", "true"
        )
        log.info("Availability dropdown %r -> %s site(s), and the %s tile is lit",
                 STATUS, self._site_count(), STATUS)
        self._clear_filter(f"Availability: {STATUS}", "availability", before)
        expect(self._tile(STATUS.upper())).to_have_attribute(
            "aria-pressed", "false"
        )

    def _apply_filter(self, trigger, value, param):
        """Open a multi-select filter, tick `value` and press Apply."""
        log.info("Opening the %r filter and ticking %r", trigger, value)
        self.page.get_by_role("button", name=trigger, exact=True).click()
        self.page.wait_for_timeout(1200)
        popover = self.page.get_by_role("dialog").last

        search = popover.locator("input[type=text]")
        if search.count():
            search.first.fill(value)
            self.page.wait_for_timeout(1200)

        option = self.page.get_by_role("option", name=value, exact=True)
        assert self._poll(lambda: option.count() == 1, timeout_ms=10000), (
            f"the {trigger!r} filter does not offer {value!r}"
        )
        option.click()
        expect(option).to_have_attribute("aria-selected", "true")

        # Nothing has changed yet -- the filter only takes effect on Apply.
        popover.get_by_role("button", name="Apply").click()
        self.page.wait_for_url(re.compile(rf"[?&]{param}="), timeout=15000)
        assert self._poll(self._loaded, timeout_ms=20000), (
            f"the table did not repopulate under the {trigger!r} filter"
        )

    def _clear_filter(self, trigger_label, param, expected_total):
        """Re-open a applied filter and press Clear."""
        log.info("Clearing the %r filter", trigger_label)
        self.page.get_by_role("button", name=trigger_label, exact=True).click()
        self.page.wait_for_timeout(1200)
        self.page.get_by_role("dialog").last.get_by_role(
            "button", name="Clear"
        ).click()
        assert self._poll(lambda: self._site_count() == expected_total), (
            f"expected {expected_total} site(s) after clearing, "
            f"got {self._site_count()}"
        )
        assert self._poll(lambda: param not in self.page.url), (
            f"{param} is still in the URL after clearing: {self.page.url}"
        )
        if self.page.get_by_role("dialog").count():
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(600)

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def search_sites(self):
        """Search narrows the table, and a no-match query shows the empty state."""
        before = self._site_count()

        log.info("Searching sites for %r", CPO)
        self.search.fill(CPO)
        self.page.wait_for_url(re.compile(r"[?&]search="), timeout=15000)
        assert self._poll(lambda: 0 < self._site_count() < before), (
            f"the search did not narrow the list from {before} "
            f"(now {self._site_count()})"
        )
        assert self.rows.count() == self._site_count()
        log.info("Search %r -> %s site(s)", CPO, self._site_count())

        log.info("Searching for a term that matches nothing")
        self.search.fill("zzzz-no-such-site")
        assert self._poll(lambda: self.empty_state.count() > 0, timeout_ms=15000), (
            "a no-match search did not show the empty state"
        )
        body = self.table.locator("tbody").first.inner_text() or ""
        assert "No sites match the current search or filters" in body, (
            f"the empty state does not explain itself: {body[:120]!r}"
        )
        log.info("Empty state shown, offering its own Clear filters control")

        log.info("Recovering with the empty state's Clear filters button")
        self.empty_clear.click()
        assert self._poll(lambda: self._site_count() == before, timeout_ms=20000), (
            f"expected {before} site(s) after clearing, got {self._site_count()}"
        )
        assert self._poll(self._loaded)

    # ----------------------------------------------------------------- #
    # Sorting
    # ----------------------------------------------------------------- #
    def sort_columns(self):
        """Sort every sortable column both ways, and prove the rest are not.

        Each sortable header must push its own `sort` and `sort_dir` into the
        URL, and the descending pass must reorder the rows. The ascending pass
        is not required to reorder: the table already opens sorted by name
        ascending, so clicking Sites simply re-applies what is active.
        """
        for col, param in self.SORTABLE.items():
            header = self._header(col)
            self._park_mouse()
            header.click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort={param}&sort_dir=asc"), timeout=15000
            )
            assert self._poll(self._loaded, timeout_ms=20000), (
                f"the table is empty after sorting by {col!r} ascending"
            )
            ascending = self._names()

            self._park_mouse()
            header.click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort={param}&sort_dir=desc"), timeout=15000
            )
            assert self._poll(
                lambda a=ascending: self._names() and self._names() != a,
                timeout_ms=20000,
            ), f"reversing the {col!r} sort did not reorder the table"
            log.info("Column %-13s sorts asc+desc (sort=%s)", col, param)

        for col in self.NOT_SORTABLE:
            # Wait for the previous action's repaint to finish before taking the
            # baseline, or its tail looks like this column reordering the table.
            before = self._settled_names()
            url = self.page.url
            self._park_mouse()
            self._header(col).click()
            # Give an (unexpected) sort a chance to happen, then assert it did not.
            self.page.wait_for_timeout(2000)
            assert self._names() == before, f"{col} should not be sortable"
            assert self.page.url == url, (
                f"clicking {col!r} changed the URL: {self.page.url}"
            )
        log.info("Columns %s are correctly not sortable", self.NOT_SORTABLE)

        # Drop the sort so the rest of the run sees the default order.
        self.page.goto(self.page.url.split("?")[0])
        assert self._poll(self._loaded, timeout_ms=30000)

    # ----------------------------------------------------------------- #
    # Row expansion
    # ----------------------------------------------------------------- #
    def expand_row_hierarchy(self):
        """Expand a site into its devices, then a device into its sockets.

        Three levels deep. Each expansion injects a nested table, so the check
        is that the total row count grows and the deepest table lists sockets.
        """
        base = self.all_rows.count()
        log.info("Expanding the first site row")
        self._park_mouse()
        self.expand_row.first.click()
        assert self._poll(lambda: self.all_rows.count() > base, timeout_ms=15000), (
            "expanding a site added no charging-device rows"
        )
        assert self._poll(lambda: self.page.locator("table").count() == 2), (
            "expanding a site did not inject its charging-device table"
        )
        expect(self.collapse_row.first).to_be_visible()
        devices = self.page.locator("table").nth(1)
        # The nested table paints skeleton rows first and fills them once its
        # own fetch lands, so wait for real content rather than reading the
        # placeholder (which is just tabs).
        assert self._poll(
            lambda: re.search(
                r"ID:\s*\S+",
                devices.locator("> tbody > tr").first.inner_text() or "",
            ),
            timeout_ms=20000,
        ), (
            "the charging-device rows never loaded: "
            f"{(devices.locator('> tbody > tr').first.inner_text() or '')[:80]!r}"
        )
        device_rows = devices.locator("> tbody > tr").count()
        log.info("Site expanded to %s charging device(s)", device_rows)

        log.info("Expanding the first charging device into its sockets")
        self._park_mouse()
        devices.get_by_role("button", name="Expand row").first.click()
        assert self._poll(
            lambda: self.page.locator("table").count() >= 3, timeout_ms=15000
        ), "expanding a charging device injected no socket table"
        deepest = self.page.locator("table").last
        assert self._poll(
            lambda: "Socket" in (
                deepest.locator("> tbody > tr").first.inner_text() or ""
            ),
            timeout_ms=20000,
        ), (
            "the socket rows never loaded: "
            f"{(deepest.locator('> tbody > tr').first.inner_text() or '')[:90]!r}"
        )
        socket_text = (deepest.locator("> tbody > tr").first.inner_text() or "")
        assert re.search(r"ID:\s*\S+", socket_text), (
            f"a socket row states no ID: {socket_text[:90]!r}"
        )
        log.info("Charging device expanded to %s socket(s): %r",
                 deepest.locator("> tbody > tr").count(),
                 socket_text.replace("\n", " ")[:60])

        log.info("Collapsing the site row again")
        self._park_mouse()
        self.collapse_row.first.click()
        assert self._poll(lambda: self.all_rows.count() == base, timeout_ms=15000), (
            f"expected {base} row(s) after collapsing, got {self.all_rows.count()}"
        )
        assert self.page.locator("table").count() == 1, (
            "collapsing the site left a nested table behind"
        )

    def expand_all_rows(self):
        """Expand every site at once, then collapse them all."""
        base = self.all_rows.count()
        sites = self.rows.count()
        log.info("Expanding all %s site rows", sites)
        self._park_mouse()
        self.expand_all.click()
        assert self._poll(
            lambda: self.page.locator("table").count() == sites + 1,
            timeout_ms=25000,
        ), (
            f"expected a nested table per site ({sites}), got "
            f"{self.page.locator('table').count() - 1}"
        )
        assert self.all_rows.count() > base
        log.info("All rows expanded -> %s row(s) across %s table(s)",
                 self.all_rows.count(), self.page.locator("table").count())

        log.info("Collapsing all rows")
        self._park_mouse()
        self.collapse_all.click()
        assert self._poll(lambda: self.all_rows.count() == base, timeout_ms=20000), (
            f"expected {base} row(s) after collapsing all, got {self.all_rows.count()}"
        )
        assert self.page.locator("table").count() == 1

    # ----------------------------------------------------------------- #
    # Alerts badge
    # ----------------------------------------------------------------- #
    def check_alert_badge(self):
        """A row's alert badge explains itself on click.

        Staging does not always have an alerting site on page 1, so this is
        skipped rather than failed when there is no badge to click -- and says
        so in the log instead of silently passing.
        """
        if not self.alert_badges.count():
            log.info("No site on this page has an open alert -- nothing to check")
            return

        badge = self.alert_badges.first
        label = badge.get_attribute("aria-label")
        log.info("Opening the alert badge (%s)", label)
        self._park_mouse()
        badge.click()
        assert self._poll(
            lambda: self.page.get_by_role("dialog").count() > 0, timeout_ms=10000
        ), "the alert badge opened nothing"
        text = (self.page.get_by_role("dialog").last.inner_text() or "").strip()
        assert "Alert" in text, f"the alert popover says nothing useful: {text!r}"
        assert "Expand the row" in text, (
            f"the alert popover does not point at the row detail: {text!r}"
        )
        log.info("Alert popover: %r", text.replace("\n", " ")[:80])
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(700)

    # ----------------------------------------------------------------- #
    # Pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        """Step, jump and resize through the site list."""
        first_page = self._names()
        assert self.next_page.is_enabled(), "expected more than one page of sites"

        log.info("Paging forward and back")
        self._park_mouse()
        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2"), timeout=15000)
        assert self._poll(
            lambda: self._names() and self._names() != first_page
        ), "page 2 shows the same sites as page 1"

        self._park_mouse()
        self.prev_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=15000)
        assert self._poll(lambda: self._names() == first_page), (
            "going back did not restore page 1"
        )

        # A direct page jump. Matched exactly -- "Go to page 1" is a prefix of
        # "Go to page 13" and a substring match would resolve to both.
        page_3 = self.page.get_by_role("button", name="Go to page 3", exact=True)
        if page_3.count():
            log.info("Jumping straight to page 3")
            self._park_mouse()
            page_3.click()
            self.page.wait_for_url(re.compile(r"[?&]page=3"), timeout=15000)
            assert self._poll(
                lambda: self._names() and self._names() != first_page
            ), "page 3 shows the same sites as page 1"
            self._park_mouse()
            self.page.get_by_role("button", name="Go to page 1", exact=True).click()
            self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=15000)
            assert self._poll(lambda: self._names() == first_page)

        current = (self.page_size.first.inner_text() or "").strip()
        before = self.rows.count()
        target = "50" if current != "50" else "100"
        log.info("Switching the page size from %s to %s", current, target)
        self._park_mouse()
        self.page_size.first.click()
        self.page.wait_for_timeout(900)
        listed = [o.inner_text().strip() for o in self.page.get_by_role("option").all()]
        assert listed == self.PAGE_SIZES, (
            f"the page-size selector offers {listed}, expected {self.PAGE_SIZES}"
        )
        self.page.get_by_role("option", name=target, exact=True).click()
        assert self._poll(lambda: self.rows.count() > before, timeout_ms=25000), (
            f"expected more than {before} rows at page size {target}, "
            f"got {self.rows.count()}"
        )
        log.info("Page size %s shows %s site(s)", target, self.rows.count())

        log.info("Restoring the page size to %s", current)
        self._park_mouse()
        self.page_size.first.click()
        self.page.wait_for_timeout(900)
        self.page.get_by_role("option", name=current, exact=True).click()
        assert self._poll(lambda: self.rows.count() == before, timeout_ms=25000), (
            f"expected {before} rows after restoring page size {current}, "
            f"got {self.rows.count()}"
        )

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def network_status_page(self):
        self.open_page()
        self.check_status_tiles()
        self.check_tile_tooltips()
        self.check_table_structure()
        self.check_legend()
        self.filter_by_status_tile()
        self.check_filter_options()
        self.filter_by_cpo()
        self.filter_by_availability_dropdown()
        self.search_sites()
        self.sort_columns()
        self.expand_row_hierarchy()
        self.expand_all_rows()
        self.check_alert_badge()
        self.paginate()
        log.info("Network Status workflow completed")
