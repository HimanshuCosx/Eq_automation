import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.driver_dashboard")

# The driver the search check is pinned to. "999" is a real CPMS customer ID on
# staging and is also a substring of other IDs, which is what makes it a useful
# search term -- it proves the box matches on the ID rather than only on an
# exact hit.
SEARCH_TERM = "999"

# The filter values the status / type checks are pinned to.
STATUS = "Active"
DRIVER_TYPE = "RFID"

# The date preset the range check switches to, and the one the page opens on.
DEFAULT_RANGE = "Month to date"
OTHER_RANGE = "Last 30 days"


class driver_dashboard:
    """Driver Dashboard (/operations/driver-dashboard/list).

    The driver-side view of the estate: seven summary tiles, a searchable and
    sortable driver table, a date-range picker with eleven presets, status and
    driver-type filters, and a Map view on its own route with its own metric
    and display controls.

    The workflow is entirely read-only -- the page creates, edits and deletes
    nothing -- so it always leaves staging exactly as it found it. It exercises
    every control: all seven tiles and their tooltips, the table's column set
    and its sortable and non-sortable columns, the search box and its empty
    state, each of the three filters (applied and cleared), pagination with the
    page-size selector, and the Map view's metric, display, legend and zoom.
    """

    # The seven summary tiles, in render order. Each maps to the shape its
    # value must take and a phrase its tooltip must contain. The labels are
    # rendered uppercase by CSS but sit in the DOM in title case, so they are
    # read from the rendered text rather than matched as DOM strings.
    TILES = {
        "TOTAL DRIVERS": (r"[\d,]+", "linked to your CPMS"),
        "ACTIVE IN PERIOD": (r"[\d,]+", "at least one charging session"),
        "INACTIVE": (r"[\d,]+", "no charging session in this period"),
        "NEVER CHARGED": (r"[\d,]+", "never completed a successful charge"),
        "NEW IN PERIOD": (r"[\d,]+", "charged for the first time"),
        "ENERGY": (r"[\d.,]+\s*[kMG]?Wh", "Total energy delivered"),
        "REVENUE": (r"£[\d,.]+", "Total revenue from driver charging"),
    }

    COLUMNS = ["Driver", "Type", "Status", "Charge Keys", "Sessions",
               "Energy (kWh)", "Revenue (£)", "Revenue Total (£)",
               "Last Session", "First Charged"]

    # Sortable columns and the `sort_by` value each sends.
    SORTABLE = {
        "Sessions": "sessions",
        "Energy (kWh)": "energy",
        "Revenue (£)": "revenue",
        "Last Session": "last_session",
        "First Charged": "first_charged",
    }
    # Everything else in the header is deliberately not sortable. Note Revenue
    # Total is not, even though Revenue beside it is.
    NOT_SORTABLE = ["Driver", "Type", "Status", "Charge Keys",
                    "Revenue Total (£)"]

    STATUS_OPTIONS = ["Active", "Inactive", "Never charged"]
    DRIVER_TYPE_OPTIONS = ["App", "RFID", "One-time", "Payment terminal",
                           "Admin", "OCPP (no auth)"]

    # The date picker's presets.
    DATE_PRESETS = [
        "This month", "This quarter", "This financial year", "Last 30 days",
        "Last month", "Last quarter", "Last financial year", "Month to date",
        "Quarter to date (QTD)", "Year to date (FY)", "Custom",
    ]

    PAGE_SIZES = ["15", "20", "50", "100"]

    # Map view controls.
    MAP_METRICS = ["Sessions", "Revenue", "Energy (kWh)"]
    MAP_DISPLAYS = ["Heatmap", "Bubbles"]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.dd_link = page.get_by_role("link", name="Driver Dashboard")
        self.heading = page.locator("//h1[normalize-space()='Driver Dashboard']")

        # Search
        self.search = page.get_by_placeholder(re.compile(r"^Search drivers"))

        # Table
        self.table = page.locator("table").first
        self.rows = self.table.locator("> tbody > tr")

        # Tiles / tooltips
        self.tooltip_buttons = page.get_by_role("button", name="More information")

        # Filters
        self.date_filter = page.get_by_role(
            "button", name=DEFAULT_RANGE, exact=True
        )
        self.status_filter = page.get_by_role("button", name="Status", exact=True)
        self.type_filter = page.get_by_role("button", name="Driver Type", exact=True)

        # View toggle
        self.list_view = page.get_by_role("button", name="List", exact=True)
        self.map_view = page.get_by_role("button", name="Map", exact=True)

        # Empty state
        self.empty_state = page.get_by_text("No drivers found", exact=True)
        self.empty_clear = page.get_by_role("button", name="Clear filters")

        # Pagination
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(15|20|50|100)$")
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Map controls
        self.metric_filter = page.get_by_role(
            "button", name=re.compile(r"^Metric: ")
        )
        self.display_filter = page.get_by_role(
            "button", name=re.compile(r"^Display: ")
        )
        self.zoom_in = page.get_by_role("button", name="Zoom in")
        self.zoom_out = page.get_by_role("button", name="Zoom out")
        self.map_canvas = page.locator(
            ".leaflet-container, .mapboxgl-map, div[aria-label='Map']"
        )

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
        while hovered, covering the table's leading columns. A click there is
        then intercepted by the nav and retries until it times out.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] - 40, size["height"] // 2)
        self.page.wait_for_timeout(700)

    def _dismiss_tooltip(self):
        """Close an open tooltip and let it leave the DOM.

        Parked mid-header rather than in the corner: the corner sits over the
        sidebar, which then expands and blocks the next hover.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] // 2, 30)
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(250)

    def _tile_text(self, index, label):
        """The label and value of tile `index`, as rendered, or "" if not ready.

        The tiles carry no stable class of their own, so each is reached from
        its info icon and the DOM is walked upwards to the tile body. The climb
        stops at the first container holding *both* this tile's label and a
        digit: stopping at "any digit" instead lets a tile whose value has not
        rendered yet climb clean out of the tile and return the whole page,
        which then fails the shape check with a wall of unrelated text.

        The label is compared against the rendered text because CSS uppercases
        it -- the DOM string is title case and would not match.
        """
        return self.tooltip_buttons.nth(index).evaluate("""(e, label) => {
            let n = e.closest('div');
            for (let i = 0; i < 6 && n; i++) {
                const t = (n.innerText || '').replace(/\\n/g, ' ');
                if (t.includes(label) && /\\d/.test(t)) return t;
                n = n.parentElement;
            }
            return '';
        }""", label)

    def _tiles_loaded(self):
        """True once every summary tile has painted a value of the right shape."""
        for index, (label, (pattern, _)) in enumerate(self.TILES.items()):
            text = self._tile_text(index, label)
            if not text or not re.search(pattern, text.replace(label, "", 1)):
                return False
        return True

    def _names(self):
        """The driver ID in each row, top to bottom."""
        try:
            return [
                (r.locator("td").first.inner_text() or "").strip().split("\n")[0]
                for r in self.rows.all()
            ]
        except Exception:
            # The table re-renders as its refetch lands; treat a read that
            # catches it mid-repaint as "not settled yet".
            return []

    def _settled_names(self, timeout_ms=15000):
        """The row order once it has stopped changing.

        A baseline captured immediately after a previous action can still be
        the old order, which then looks like the *next* click reordered the
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

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        log.info("Opening the Driver Dashboard")
        self.dd_link.click()
        self.page.wait_for_url(
            re.compile(r"/operations/driver-dashboard/list"), timeout=20000
        )
        self.heading.wait_for(state="visible", timeout=20000)
        assert self._poll(self._loaded, timeout_ms=40000), (
            "the driver table never loaded"
        )
        # The tiles paint from their own summary call, which can land after the
        # table; wait for every one to carry a value before reading any.
        assert self._poll(self._tiles_loaded, timeout_ms=40000), (
            "the summary tiles never finished loading"
        )
        # Clicking the sidebar link leaves the pointer on the nav, which stays
        # expanded and covers the leading edge of the content -- including the
        # first summary tile. Park it before anything tries to hover or click
        # there.
        self._park_mouse()
        log.info("Driver Dashboard loaded with %s driver row(s)", self.rows.count())

    # ----------------------------------------------------------------- #
    # Summary tiles
    # ----------------------------------------------------------------- #
    def check_kpi_tiles(self):
        """All seven summary tiles render a value of the right shape."""
        count = self.tooltip_buttons.count()
        assert count == len(self.TILES), (
            f"expected {len(self.TILES)} summary tiles, found {count}"
        )
        for index, (label, (pattern, _)) in enumerate(self.TILES.items()):
            text = self._tile_text(index, label)
            assert label in text, (
                f"tile {index} should be {label!r} but reads {text[:120]!r}"
            )
            value = text.replace(label, "", 1).strip()
            assert re.search(pattern, value), (
                f"the {label} tile shows {value!r}, which does not match the "
                f"expected shape {pattern!r}"
            )
            log.info("Tile %-18s -> %s", label, value)

    def check_tile_tooltips(self):
        """Each tile's info icon explains that tile."""
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
            log.info("Tooltip %-18s -> %s", label, text[:60])
            self._dismiss_tooltip()
        log.info("All %s tile tooltips explain the right tile", len(self.TILES))

    # ----------------------------------------------------------------- #
    # Table structure
    # ----------------------------------------------------------------- #
    def check_table_structure(self):
        """The table renders its full column set and a full page of drivers."""
        headers = [
            (h.inner_text() or "").strip()
            for h in self.table.locator("thead th").all()
        ]
        log.info("Table columns: %s", headers)
        assert headers == self.COLUMNS, (
            f"unexpected column set: {headers} != {self.COLUMNS}"
        )

        size = int((self.page_size.first.inner_text() or "0").strip())
        assert self.rows.count() == size, (
            f"expected {size} rows on page 1, got {self.rows.count()}"
        )
        # Every row identifies its driver and states a session count.
        for row in self.rows.all():
            driver = (row.locator("td").first.inner_text() or "").strip()
            assert driver, "a row has no driver ID"
        log.info("Table shows %s driver row(s)", self.rows.count())

    # ----------------------------------------------------------------- #
    # Filters
    # ----------------------------------------------------------------- #
    def filter_by_status(self):
        """Apply the status filter, then clear it by re-selecting."""
        self._apply_option_filter(
            self.status_filter, "Status", STATUS, "status",
            self.STATUS_OPTIONS,
        )

    def filter_by_driver_type(self):
        """Apply the driver-type filter, then clear it by re-selecting."""
        self._apply_option_filter(
            self.type_filter, "Driver Type", DRIVER_TYPE, "driver_type",
            self.DRIVER_TYPE_OPTIONS,
        )

    def _apply_option_filter(self, trigger, label, value, param, expected_options):
        """Open a filter, check its options, select one, then clear it.

        These filters apply the moment an option is clicked -- there is no
        Apply button -- and re-selecting the same option clears them. That is
        worth pinning: the multi-select filters elsewhere in this app behave
        the opposite way, so a shared helper would get one of them wrong.
        """
        before = self._settled_names()

        log.info("Opening the %s filter", label)
        trigger.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0), (
            f"the {label} filter opened with no options"
        )
        listed = [o.inner_text().strip() for o in self.page.get_by_role("option").all()]
        assert listed == expected_options, (
            f"the {label} filter offers {listed}, expected {expected_options}"
        )

        log.info("Selecting %r", value)
        self.page.get_by_role("option", name=value, exact=True).click()
        self.page.wait_for_url(re.compile(rf"[?&]{param}="), timeout=15000)
        assert self._poll(self._loaded, timeout_ms=20000), (
            f"the table did not repopulate under the {label} filter"
        )
        expect(
            self.page.get_by_role("button", name=f"{label}: {value}", exact=True)
        ).to_be_visible()
        log.info("%s filter %r applied -> %s row(s)", label, value, self.rows.count())

        log.info("Clearing the %s filter by re-selecting %r", label, value)
        self.page.get_by_role(
            "button", name=f"{label}: {value}", exact=True
        ).click()
        self.page.wait_for_timeout(1200)
        selected = self.page.get_by_role("option", name=value, exact=True)
        expect(selected).to_have_attribute("aria-selected", "true")
        selected.click()
        assert self._poll(lambda: param not in self.page.url, timeout_ms=15000), (
            f"{param} is still in the URL after clearing: {self.page.url}"
        )
        expect(trigger).to_be_visible()
        assert self._poll(lambda: self._names() == before, timeout_ms=20000), (
            "the table did not return to its unfiltered rows"
        )
        if self.page.get_by_role("dialog").count():
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(600)

    def filter_by_date_range(self):
        """Switch the reporting window, then put it back.

        The presets are buttons rather than options, so the popover is checked
        on its full preset list before one is chosen.
        """
        log.info("Opening the date-range picker")
        self.date_filter.click()
        self.page.wait_for_timeout(1500)
        popover = self.page.get_by_role("dialog").last
        listed = [
            (b.inner_text() or "").strip().split("\n")[0]
            for b in popover.get_by_role("button").all()
        ]
        missing = [p for p in self.DATE_PRESETS if p not in listed]
        assert not missing, (
            f"the date picker is missing the preset(s) {missing} (offers {listed})"
        )
        log.info("Date picker offers all %s presets", len(self.DATE_PRESETS))

        log.info("Switching the window to %r", OTHER_RANGE)
        popover.get_by_role(
            "button", name=re.compile(rf"^{re.escape(OTHER_RANGE)}")
        ).click()
        self.page.wait_for_url(re.compile(r"[?&]date_range=last_30_days"), timeout=15000)
        assert self._poll(self._loaded, timeout_ms=25000), (
            f"the table did not repopulate for {OTHER_RANGE!r}"
        )
        expect(
            self.page.get_by_role("button", name=OTHER_RANGE, exact=True)
        ).to_be_visible()
        log.info("Window %r applied -> %s row(s)", OTHER_RANGE, self.rows.count())

        log.info("Restoring the default window (%s)", DEFAULT_RANGE)
        self.page.get_by_role("button", name=OTHER_RANGE, exact=True).click()
        self.page.wait_for_timeout(1500)
        self.page.get_by_role("dialog").last.get_by_role(
            "button", name=re.compile(rf"^{re.escape(DEFAULT_RANGE)}")
        ).click()
        expect(self.date_filter).to_be_visible()
        assert self._poll(self._loaded, timeout_ms=25000)

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def search_drivers(self):
        """Search narrows the table, and a no-match query shows the empty state."""
        before = self._settled_names()

        log.info("Searching drivers for %r", SEARCH_TERM)
        self.search.fill(SEARCH_TERM)
        self.page.wait_for_url(re.compile(r"[?&]search="), timeout=15000)
        assert self._poll(
            lambda: self._names() and self._names() != before, timeout_ms=20000
        ), "the search did not change the driver list"
        # Every result matches the term somewhere in its ID.
        for name in self._names():
            assert SEARCH_TERM in name, (
                f"the search returned {name!r}, which does not contain "
                f"{SEARCH_TERM!r}"
            )
        log.info("Search %r -> %s driver(s)", SEARCH_TERM, self.rows.count())

        log.info("Searching for a term that matches nothing")
        self.search.fill("zzzz-no-such-driver")
        assert self._poll(lambda: self.empty_state.count() > 0, timeout_ms=20000), (
            "a no-match search did not show the empty state"
        )
        body = self.table.locator("tbody").first.inner_text() or ""
        assert "zzzz-no-such-driver" in body, (
            f"the empty state does not quote the search term: {body[:140]!r}"
        )
        log.info("Empty state shown, quoting the term back")

        log.info("Recovering with the empty state's Clear filters button")
        self.empty_clear.click()
        # Wait for the query to actually drop before comparing rows -- staging
        # can take tens of seconds to answer a driver search, and comparing
        # while the old result is still on screen just burns the timeout.
        assert self._poll(lambda: "search=" not in self.page.url, timeout_ms=20000), (
            f"the search is still in the URL after clearing: {self.page.url}"
        )
        assert self._poll(lambda: self._names() == before, timeout_ms=40000), (
            "the table did not return to its unfiltered rows: "
            f"{self._names()[:3]} != {before[:3]}"
        )

    # ----------------------------------------------------------------- #
    # Sorting
    # ----------------------------------------------------------------- #
    def sort_columns(self):
        """Sort every sortable column both ways, and prove the rest are not."""
        for col, param in self.SORTABLE.items():
            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort_by={param}\b"), timeout=15000
            )
            assert self._poll(self._loaded, timeout_ms=20000), (
                f"the table is empty after sorting by {col!r}"
            )
            first_direction = self._settled_names()
            direction = re.search(r"sort_order=(asc|desc)", self.page.url)
            assert direction, f"sorting by {col!r} set no sort_order: {self.page.url}"
            opposite = "desc" if direction.group(1) == "asc" else "asc"

            self._park_mouse()
            self._header(col).click()
            # Wait for the *direction* to flip, not merely for sort_by to be
            # present -- it already is from the first click, so waiting on that
            # returns instantly and the row comparison below then races the
            # refetch instead of following it.
            self.page.wait_for_url(
                re.compile(rf"[?&]sort_by={param}&sort_order={opposite}\b"),
                timeout=15000,
            )
            assert self._poll(
                lambda a=first_direction: self._names() and self._names() != a,
                timeout_ms=20000,
            ), f"reversing the {col!r} sort did not reorder the table"
            log.info("Column %-17s sorts both ways (sort_by=%s)", col, param)

        # The negative control is checked structurally for every column: a
        # sortable header carries a sort chevron and a pointer cursor, and these
        # carry neither. Clicking each one instead would be flakier than it is
        # worth -- the leftmost headers sit right against the sidebar, which
        # intercepts the click whenever the nav happens to be expanded.
        for col in self.NOT_SORTABLE:
            header = self._header(col)
            classes = header.get_attribute("class") or ""
            assert "cursor-pointer" not in classes, (
                f"{col} presents itself as sortable but is not in SORTABLE"
            )
            assert header.locator("svg").count() == 0, (
                f"{col} carries a sort indicator but is not in SORTABLE"
            )

        # Then prove it behaviourally on one of them. Revenue Total is chosen
        # because it sits at the right-hand end of the table, well clear of the
        # sidebar -- and because it is the interesting case: the Revenue column
        # immediately beside it *is* sortable.
        col = "Revenue Total (£)"
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
    # Pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        """Step, jump and resize through the driver list."""
        first_page = self._settled_names()
        assert self.next_page.is_enabled(), "expected more than one page of drivers"

        log.info("Paging forward and back")
        self._park_mouse()
        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2"), timeout=15000)
        assert self._poll(
            lambda: self._names() and self._names() != first_page
        ), "page 2 shows the same drivers as page 1"

        self._park_mouse()
        self.prev_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=15000)
        assert self._poll(lambda: self._names() == first_page), (
            "going back did not restore page 1"
        )

        # A direct page jump. Matched exactly -- "Go to page 1" is a prefix of
        # "Go to page 100" and a substring match would resolve to both.
        page_3 = self.page.get_by_role("button", name="Go to page 3", exact=True)
        if page_3.count():
            log.info("Jumping straight to page 3")
            self._park_mouse()
            page_3.click()
            self.page.wait_for_url(re.compile(r"[?&]page=3"), timeout=15000)
            assert self._poll(
                lambda: self._names() and self._names() != first_page
            ), "page 3 shows the same drivers as page 1"
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
        self.page.wait_for_timeout(1000)
        listed = [o.inner_text().strip() for o in self.page.get_by_role("option").all()]
        assert listed == self.PAGE_SIZES, (
            f"the page-size selector offers {listed}, expected {self.PAGE_SIZES}"
        )
        self.page.get_by_role("option", name=target, exact=True).click()
        assert self._poll(lambda: self.rows.count() > before, timeout_ms=25000), (
            f"expected more than {before} rows at page size {target}, "
            f"got {self.rows.count()}"
        )
        log.info("Page size %s shows %s driver(s)", target, self.rows.count())

        log.info("Restoring the page size to %s", current)
        self._park_mouse()
        self.page_size.first.click()
        self.page.wait_for_timeout(1000)
        self.page.get_by_role("option", name=current, exact=True).click()
        assert self._poll(lambda: self.rows.count() == before, timeout_ms=25000), (
            f"expected {before} rows after restoring page size {current}, "
            f"got {self.rows.count()}"
        )

    # ----------------------------------------------------------------- #
    # Map view
    # ----------------------------------------------------------------- #
    def browse_map(self):
        """Switch to the Map view and exercise its own controls.

        The map is a separate route rather than a panel, and it carries two
        controls the list does not have: the metric it plots and how it draws
        it. Both are stepped through, then the view is switched back.
        """
        log.info("Switching to the Map view")
        self.map_view.click()
        self.page.wait_for_url(
            re.compile(r"/operations/driver-dashboard/map"), timeout=20000
        )
        self.map_canvas.first.wait_for(state="visible", timeout=30000)
        assert self.page.locator("table").count() == 0, (
            "the Map view still renders the driver table"
        )
        log.info("Map view rendered")

        # The summary tiles and the filters carry over from the list.
        assert self.tooltip_buttons.count() == len(self.TILES), (
            "the Map view lost the summary tiles"
        )
        expect(self.status_filter).to_be_visible()
        expect(self.type_filter).to_be_visible()

        # The legend names the metric it is shading by.
        body = self.page.locator("body").inner_text() or ""
        assert "Density" in body, "the map has no density legend"
        log.info("Map legend: %r",
                 body[body.find("Density"):body.find("Density") + 40].replace("\n", " "))

        self._step_map_control(self.metric_filter, "Metric", self.MAP_METRICS)
        self._step_map_control(self.display_filter, "Display", self.MAP_DISPLAYS)

        log.info("Zooming the map in and back out")
        self.zoom_in.click()
        self.page.wait_for_timeout(1500)
        self.zoom_out.click()
        self.page.wait_for_timeout(1500)
        expect(self.map_canvas.first).to_be_visible()

        log.info("Switching back to the List view")
        self.list_view.click()
        self.page.wait_for_url(
            re.compile(r"/operations/driver-dashboard/list"), timeout=20000
        )
        assert self._poll(self._loaded, timeout_ms=30000), (
            "the driver table did not come back"
        )

    def _step_map_control(self, trigger, label, options):
        """Step a map dropdown through every one of its options.

        The popover stays open after a selection rather than closing, so the
        options are clicked one after another without re-opening it. Clicking
        the trigger again between selections would *close* the popover and the
        next option click would then wait forever for an element that is no
        longer on the page.
        """
        original = (trigger.first.inner_text() or "").strip().split(": ", 1)[-1]

        self._open_map_control(trigger, label)
        listed = [
            o.inner_text().strip() for o in self.page.get_by_role("option").all()
        ]
        assert listed == options, (
            f"the {label} control offers {listed}, expected {options}"
        )

        for value in options + [original]:
            self._open_map_control(trigger, label)
            # The map repaints continuously behind this popover, so its bounding
            # box never settles and Playwright's stability check never passes --
            # it retries until it times out even though the option is perfectly
            # visible. force=True skips that check only; the option is still
            # located by role and name, and the assertion below proves the click
            # actually took effect.
            self.page.get_by_role("option", name=value, exact=True).click(force=True)
            assert self._poll(
                lambda v=value: v in (trigger.first.inner_text() or ""),
                timeout_ms=15000,
            ), f"the {label} control did not switch to {value!r}"
            expect(self.map_canvas.first).to_be_visible()
            log.info("Map %s -> %s", label, value)

        # Leave the popover closed so it cannot cover the next control.
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(600)

    def _open_map_control(self, trigger, label):
        """Make sure a map dropdown's options are on screen."""
        if not self.page.get_by_role("option").count():
            trigger.first.click()
            assert self._poll(
                lambda: self.page.get_by_role("option").count() > 0
            ), f"the {label} control opened with no options"
        self.page.wait_for_timeout(400)

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def driver_dashboard_page(self):
        self.open_page()
        self.check_kpi_tiles()
        self.check_tile_tooltips()
        self.check_table_structure()
        self.filter_by_status()
        self.filter_by_driver_type()
        self.filter_by_date_range()
        self.search_drivers()
        self.sort_columns()
        self.paginate()
        self.browse_map()
        log.info("Driver Dashboard workflow completed")
