import datetime
import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.operations_hub")

# The CPO the row-expansion checks are pinned to. Babergh Council is a small,
# stable CPO on staging (4 sites), so its expanded site list is short and the
# row counts stay readable in the log.
CPO = "Babergh Council"

# A search term that matches exactly one CPO, used to prove the search box
# really filters rather than just re-rendering the list.
SEARCH_TERM = "Capurro"

# The site the detail-page walk is pinned to. It is chosen deliberately rather
# than taken as "whichever row is first": the site walk reads the Maintenance
# panel, and this is the one site on staging that actually carries maintenance
# plans (several plans, upcoming and completed events) plus a plan with notes
# and an attachment. A site picked at random is usually empty, which would
# quietly reduce the maintenance checks to asserting an empty state.
SITE = "345 Woodbridge Road CO OP"
# ...and the CPO that owns it. The hub lists CPOs only, so a site search
# surfaces the site's CPO, and the site is reached by expanding that row.
SITE_CPO = "East of England CO OP"

# --------------------------------------------------------------------------- #
# Maintenance fixtures
#
# The Maintenance panel offers Create Plan, Edit Plan and Add Event, and neither
# a plan nor an event can be deleted -- the UI exposes no delete control
# anywhere. So the suite never submits any of them: each form is filled to a
# submittable state, checked, and cancelled. Edit Plan is opened on a single,
# clearly-labelled fixture plan that already exists on the site.
# --------------------------------------------------------------------------- #
PLAN_TITLE = "AUTOMATION - do not modify"
# The title typed into the (cancelled) Create Event form.
EVENT_TITLE = "AUTOMATION EVENT - do not modify"
# The description typed into the (cancelled) Create Plan form.
PLAN_DESCRIPTION = "Validated by the automated regression suite."

# The account the suite logs in as, used as the event assignee.
ASSIGNEE = "himanshu@equidria.com"


class operations_hub:
    """Operations Hub (/operations/operations-hub).

    The operational view of the estate. It opens on a List View with one row
    per charge point operator; each CPO row expands in place into its own
    nested list of sites, and each site row expands again into an asset /
    organisation / maintenance-dates panel. A single search box spans CPO and
    site names and IDs, and CPO / Deal Type / Status / Criticality filters sit
    above the table. The same data can be shown as a Map View (?view=map).

    From an expanded site, "Open site" leads to the per-site page (Site Info /
    Tracker / Records tabs). Its breadcrumb links back up to the CPO's own
    sites page (/operations-hub/<cpo-uuid>), which keeps the older drill-down
    table with its Deal Type / Criticality filters and a per-row "Maintenance"
    action -- the only route left to a site's Maintenance panel, since the
    site page no longer shows a Maintenance tab.
    """

    # List View. The leading blank column holds the row expander ("Expand all
    # rows" in the header), and the trailing blank one is an unlabelled
    # actions column.
    CPO_COLUMNS = [
        "", "CPO", "Sites", "Deal Type", "Status",
        "Next Maint.", "Criticality", "Lifecycle", "",
    ]

    # Sortable CPO columns and the `sort_by` value each sends to the API.
    # Status and Lifecycle are not sortable. CPO and Sites used to send the
    # wrong parameter and get HTTP 422 (a pinned known bug); they now send
    # cpo_name / sites and sort correctly, so they are asserted like the rest
    # -- and, because their values are always populated, on the actual row
    # order too.
    CPO_SORTS = {
        "CPO": "cpo_name",
        "Sites": "sites",
        "Deal Type": "deal_type",
        "Next Maint.": "next_maintenance",
        "Criticality": "criticality",
    }

    # The CPO's own sites page drops the CPO column (every row is that CPO).
    CPO_DETAIL_COLUMNS = [
        "", "Site Name", "Deal Type", "Status",
        "Next Maint.", "Criticality", "Lifecycle", "Actions",
    ]

    # Options behind the filters, and the query parameter each one pushes.
    DEAL_TYPES = ["O&M with Install", "O&M Onboarding", "Fully Funded Install"]
    CRITICALITIES = ["Low", "Medium", "High"]
    STATUSES = ["Planned", "Installation", "Operational", "Decommissioned"]

    # The per-site page's tabs. Records carries a document count in its label
    # ("Records 2"), so tabs are matched on the leading text.
    SITE_TABS = ["Site Info", "Tracker", "Records"]

    # The expanded site panel (hub and CPO page alike). CSS upper-cases the
    # section headings, so these are matched case-insensitively.
    SITE_PANEL_LABELS = [
        "Asset details", "Charging devices", "Sockets", "Primary device",
        "Organisation", "Country", "Maintenance dates", "Live date",
        "Next maintenance", "Lifecycle",
    ]

    # The Maintenance panel's own sub-tabs. Most carry a count in their label
    # ("All Plans (2)"), so they are matched on the leading text.
    #
    # Each maps to the markers that prove the panel rendered: either it lists
    # items, or it says plainly that it has none. Both are valid -- which one
    # shows depends on the data on the day -- so the assertion is that one of
    # them is there, never that the tab is populated.
    MAINTENANCE_TABS = {
        "All Plans": ("Edit Plan", "No maintenance plans"),
        "Upcoming Events": ("Schedule Event", "No upcoming events"),
        "Scheduled": ("Scheduled:", "No scheduled events"),
        "Completed": ("Due:", "No completed events"),
    }

    # The Records tab's document categories -- the same set the Repository uses.
    RECORD_CATEGORIES = [
        "Survey", "Legal", "Finance", "Installation",
        "M-PPM", "M-Reaction", "Removal", "Others",
    ]

    # Option sets behind the maintenance dialogs.
    EVENT_CATEGORIES = ["Preventive", "Corrective", "Inspection"]
    PLAN_FREQUENCIES = ["Weekly", "Monthly", "Quarterly", "Semi Annual", "Annual"]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.hub_link = page.get_by_role("link", name="Operations Hub")
        self.heading = page.locator("//h1[normalize-space()='Operations Hub']")

        # List / Map toggle (page header)
        self.list_view = page.get_by_role("button", name="List View")
        self.map_view = page.get_by_role("button", name="Map View")

        # Search. The hub has one box spanning CPOs and sites; the CPO's own
        # sites page keeps a site-only box.
        self.hub_search = page.get_by_placeholder("Search CPOs and sites by name or ID…")
        self.site_search = page.get_by_placeholder(
            "Search sites, charging devices, sockets, IDs, locations…"
        )
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)
        self.clear_all_filters = page.get_by_role("button", name="Clear all filters")
        # "Showing 1–20 of 61" -- the total is the honest measure of a filter.
        self.showing = page.get_by_text(re.compile(r"^Showing "))

        # Table. An expanded CPO row inserts a <tr> holding a whole nested
        # table of sites, so "table" and "table tbody tr" would match those
        # too -- everything is scoped to the outer table's own rows.
        self.table = page.locator("table").first
        self.rows = self.table.locator(":scope > tbody > tr")

        # Row-level controls
        self.expand_row = page.get_by_role("button", name="Expand row")
        self.collapse_row = page.get_by_role("button", name="Collapse row")
        self.expand_all = page.get_by_role("button", name="Expand all rows")
        self.collapse_all = page.get_by_role("button", name="Collapse all rows")
        self.view_details = page.get_by_role("button", name="View details")

        # Filters on the hub, the CPO page and the map. Each trigger renders its
        # label above its current value, so the accessible name is the two run
        # together ("CPO All CPOs"). Anchored on the label: matching the value
        # half stops resolving as soon as a filter is applied.
        self.type_filter = page.get_by_role(
            "button", name=re.compile(r"^Deal Type\b")
        ).first
        self.criticality_filter = page.get_by_role(
            "button", name=re.compile(r"^Criticality\b")
        ).first
        self.status_filter = page.get_by_role(
            "button", name=re.compile(r"^Status\b")
        ).first
        self.cpo_filter = page.get_by_role(
            "button", name=re.compile(r"^CPO\b")
        ).first

        # Pagination
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$")
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Site detail: breadcrumb + Maintenance panel controls
        self.breadcrumb = page.get_by_role("navigation", name="Breadcrumb")
        self.breadcrumb_hub = self.breadcrumb.get_by_role(
            "link", name="Operations Hub", exact=True
        )
        self.back_to_hub = page.get_by_role("button", name="Back to Operations Hub")
        self.add_events = page.get_by_role("button", name="Add Events", exact=True)
        self.create_plan = page.get_by_role(
            "button", name="Create Maintenance Plan", exact=True
        )
        self.edit_plan = page.get_by_role("button", name="Edit Plan")
        self.view_all_events = page.get_by_role("button", name="View All Events")
        self.plan_documents = page.get_by_role("button", name="Documents", exact=True)
        self.read_more = page.get_by_role("button", name="Read more")
        self.read_less = page.get_by_role("button", name="Read less")

        # Dialogs raised from the Maintenance panel. A dropdown's popover is
        # also a dialog and stacks on top of the form, so `.first` is the form
        # and `.last` is whatever opened most recently.
        self.dialog = page.get_by_role("dialog")

        # Map
        self.zoom_in = page.get_by_role("button", name="Zoom in")
        self.zoom_out = page.get_by_role("button", name="Zoom out")
        self.map_canvas = page.locator(
            ".leaflet-container, .mapboxgl-map, div[aria-label='Map']"
        )

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _park_mouse(self):
        """Move the pointer off the sidebar and let it collapse.

        The sidebar is fixed to the left edge and widens while hovered,
        covering the table's leading column -- which is exactly where the row
        expanders live. A click there is then intercepted by the nav and
        retries until it times out. The pointer is left over the sidebar after
        every sidebar-link click, so this runs before any expander click.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] - 40, size["height"] // 2)
        self.page.wait_for_timeout(700)

    def _cpo_rows(self):
        """Real CPO rows on the hub.

        Every CPO row prints its ID as "ID: ...", while the "No CPOs or sites
        found" empty state is a single plain cell. An expanded row's nested
        site table also prints IDs, so the <tr> that holds it (it contains a
        table of its own) is excluded.
        """
        return self.rows.filter(has_text=re.compile(r"ID:\s*\S")).filter(
            has_not=self.page.locator("table")
        )

    def _site_rows(self):
        """Real site rows on the CPO's own sites page.

        An expanded row inserts a second <tr> holding the detail panel. Only a
        real site row carries the "View details" action, so filtering on it
        counts sites rather than <tr>s.
        """
        return self.rows.filter(
            has=self.page.get_by_role("button", name="View details")
        )

    def _nested_site_rows(self):
        """Site rows inside the hub's expanded CPO row(s).

        The nested table has no header; each real site row carries an "Open
        site" action, which the expanded detail panel below it does not.
        """
        return self.table.locator("table tbody tr").filter(
            has=self.page.get_by_role("button", name="Open site")
        )

    def _total(self):
        """The total from "Showing 1–20 of 61" (0 for "Showing 0 results")."""
        try:
            text = self.showing.first.text_content(timeout=2000) or ""
        except Exception:
            return -1
        m = re.search(r"of\s+([\d,]+)", text)
        return int(m.group(1).replace(",", "")) if m else 0

    def _columns(self):
        return [
            (h.inner_text() or "").strip()
            for h in self.table.locator(":scope > thead th").all()
        ]

    def _header(self, col):
        return self.table.locator(f"xpath=./thead//th[normalize-space()='{col}']")

    def _poll(self, predicate, timeout_ms=10000, interval_ms=200):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        Every list on this page refetches asynchronously after a search, sort,
        filter or page change, so state is polled until it settles rather than
        racing a fixed sleep.

        The deadline is wall-clock, not a count of the sleeps: some predicates
        here are expensive (checking for an event clicks through three sub-tabs
        and takes seconds), and counting only the sleeps let a 25s timeout run
        for minutes.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _clear_search(self, box):
        if self.search_clear.count():
            self.search_clear.click()
        else:
            box.fill("")

    def _open_filter(self, trigger, expected_options, name):
        """Open a filter popover and confirm it offers `expected_options`."""
        trigger.click()
        self.page.wait_for_timeout(800)
        options = [o.inner_text().strip() for o in self.page.get_by_role("option").all()]
        for opt in expected_options:
            assert opt in options, (
                f"the {name} filter is missing the {opt!r} option (has {options[:12]})"
            )
        log.info("%s filter offers %s", name, expected_options)
        return options

    def _column_index(self, col):
        cols = self._columns()
        assert col in cols, f"the table has no {col!r} column (has {cols})"
        return cols.index(col)

    def _column_values(self, col, row_locator):
        """The `col` cell of every row, read in one snapshot.

        The rows re-render whenever a refetch lands, so reading them one
        element handle at a time can hit a detached row half-way through.
        `evaluate_all` reads every cell in a single pass instead; a read that
        still races a re-render returns [] -- every caller either polls on
        this or asserts the result is non-empty, so that simply counts as "not
        settled yet".
        """
        idx = self._column_index(col)
        try:
            return row_locator.evaluate_all(
                """(rows, idx) => rows.map(r => {
                    const cell = r.querySelectorAll(':scope > td')[idx];
                    return cell ? cell.innerText.trim() : '';
                })""",
                idx,
            )
        except Exception:
            return []

    def _first_lines(self, col, row_locator):
        """The first line of each row's `col` cell -- e.g. a name without its ID."""
        return [v.split("\n")[0].strip() for v in self._column_values(col, row_locator)]

    def _assert_column(self, col, expected, applied, row_locator, exact=False):
        """Assert every row's `col` cell matches `expected` after a filter.

        The column-level check: it reads the one column the filter is supposed
        to drive rather than matching the term anywhere in the row, so a row
        that merely mentions the term elsewhere cannot pass.
        """
        # Retry a read that landed mid-refetch (see _column_values), so an empty
        # result can never make this assertion pass vacuously.
        assert self._poll(lambda: bool(self._column_values(col, row_locator))), (
            f"{applied}: could not read the {col} column -- the table has no rows"
        )
        values = self._first_lines(col, row_locator)
        offenders = [
            v for v in values
            if (v != expected if exact else expected.lower() not in v.lower())
        ]
        assert not offenders, (
            f"{applied}: the {col} column should "
            f"{'be' if exact else 'contain'} {expected!r} on every row, but "
            f"{len(offenders)} of {len(values)} row(s) differ -> {offenders[:3]}"
        )
        log.info("%s: all %s row(s) have %s %s %r",
                 applied, len(values), col, "=" if exact else "containing", expected)

    def _rows_match(self, col, term, row_locator):
        """True when there is at least one row and every one matches `term`.

        Used as a settle signal after a search: the list only agrees with the
        query once the refetch has landed.
        """
        values = self._column_values(col, row_locator)
        return bool(values) and all(term.lower() in v.lower() for v in values)

    def _order(self, row_locator):
        """The CPO names on the page, top to bottom."""
        return self._first_lines("CPO", row_locator)

    def _assert_panel(self, scope, where, labels=None):
        """An expanded site panel spells out its assets, organisation and dates.

        textContent keeps the source casing while CSS upper-cases the section
        headings on screen, so the labels are compared case-insensitively.
        """
        text = (scope.text_content() or "").lower()
        for label in labels or self.SITE_PANEL_LABELS:
            assert label.lower() in text, f"the {where} panel is missing {label!r}"
        log.info("%s panel shows the site's assets, organisation and dates", where)

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        log.info("Opening the Operations Hub")
        self.hub_link.click()
        self.page.wait_for_url(re.compile(r"/operations/operations-hub"), timeout=15000)
        self.heading.wait_for(state="visible", timeout=15000)
        self._park_mouse()
        # The table renders skeleton rows while it loads; wait for real data.
        assert self._poll(lambda: self._cpo_rows().count() > 0, timeout_ms=25000), (
            "the CPO list never loaded"
        )
        # The hub opens on the List View. The old CPOs / Sites radio switch is
        # gone -- the list is always CPOs, with sites nested inside each row --
        # so the List View is proven by the URL and the unified search box.
        assert "view=map" not in self.page.url, (
            f"the hub should open on the List View, got {self.page.url}"
        )
        expect(self.hub_search).to_be_visible()
        log.info("Hub open in List View with %s CPO(s) listed",
                 self._cpo_rows().count())

    # ----------------------------------------------------------------- #
    # List View
    # ----------------------------------------------------------------- #
    def check_cpo_table(self):
        """Confirm the CPO table renders its full column set and a full page."""
        self.table.wait_for(state="visible", timeout=10000)
        columns = self._columns()
        log.info("CPO table columns: %s", columns)
        assert columns == self.CPO_COLUMNS, (
            f"unexpected CPO column set: {columns} != {self.CPO_COLUMNS}"
        )

        size = int((self.page_size.text_content() or "0").strip())
        count = self._cpo_rows().count()
        assert count == size, f"expected {size} CPO rows on page 1, got {count}"
        assert self._total() > size, (
            f"expected more than one page of CPOs, the footer says {self._total()}"
        )

        # Every CPO names itself with its ID underneath, and the Sites column
        # is always a number.
        for value in self._column_values("Sites", self._cpo_rows()):
            assert value.isdigit(), f"the Sites column is not a count: {value!r}"
        for value in self._column_values("CPO", self._cpo_rows()):
            assert re.search(r"ID:\s*\S", value), f"a CPO row has no ID: {value!r}"
        log.info("CPO table shows %s of %s row(s), each with an ID and a site count",
                 count, self._total())

    def search_cpos(self):
        """Search the hub by CPO, then by site, and check the empty state.

        The one box now spans CPO *and* site names: a CPO term narrows to that
        CPO, while a site term surfaces the site's CPO with its Sites column
        reading "matched/total".
        """
        before = self._total()

        log.info("Searching the hub for CPO %r", SEARCH_TERM)
        self.hub_search.fill(SEARCH_TERM)
        assert self._poll(lambda: 0 < self._total() < before), (
            f"the CPO search should narrow the list from {before}, "
            f"got {self._total()}"
        )
        assert self._poll(
            lambda: self._rows_match("CPO", SEARCH_TERM, self._cpo_rows())
        ), f"the rows never agreed with the search: {self._order(self._cpo_rows())}"
        self._assert_column("CPO", SEARCH_TERM, f"CPO search {SEARCH_TERM!r}",
                            self._cpo_rows())

        log.info("Searching the hub for site %r", SITE)
        self.hub_search.fill(SITE)
        assert self._poll(
            lambda: self._order(self._cpo_rows()) == [SITE_CPO], timeout_ms=15000
        ), (
            f"searching for site {SITE!r} should list only its CPO {SITE_CPO!r}, "
            f"got {self._order(self._cpo_rows())}"
        )
        sites = self._column_values("Sites", self._cpo_rows())[0]
        assert re.fullmatch(r"1/\d+", sites), (
            f"a site search should show '1/<total>' in the Sites column, got {sites!r}"
        )
        log.info("Site search lists %s with %s site(s) matched", SITE_CPO, sites)

        log.info("Searching the hub for a term that matches nothing")
        self.hub_search.fill("zzzz-no-such-cpo")
        assert self._poll(lambda: self._cpo_rows().count() == 0), (
            f"expected no CPOs, got {self._cpo_rows().count()}"
        )
        # The empty state leads with a heading and repeats the term back
        # underneath it, so it is matched on the heading.
        expect(self.page.get_by_text("No CPOs or sites found", exact=True)).to_be_visible()
        log.info("Hub empty state shown")

        self._clear_search(self.hub_search)
        assert self._poll(lambda: self._total() == before), (
            f"expected {before} CPO(s) after clearing the search, got {self._total()}"
        )
        log.info("Search cleared, back to %s CPO(s)", before)

    def _sorted_ok(self, values, ascending):
        """True when `values` are in the expected order.

        The API sorts with a locale collation that ignores case, spaces and
        punctuation ("States of Guernsey" before "St Martins CO OP", "Leeds"
        before "Le Friquet"), so names are compared on their letters and digits
        only -- a plain string sort disagrees with a correct server sort.
        """
        keys = [re.sub(r"[^a-z0-9]", "", v.lower()) for v in values if v]
        if len(keys) < 2:
            return False
        return keys == sorted(keys, reverse=not ascending)

    def _numbers_sorted(self, values, ascending):
        nums = [int(v) for v in values if v.isdigit()]
        if len(nums) < 2 or len(nums) != len(values):
            return False
        return nums == sorted(nums, reverse=not ascending)

    def _watch_cpo_api(self):
        """Record the status of every CPO-list request, keyed by its sort_by.

        Sorting is asserted on the API response, not just on the rows: a
        rejected sort still updates the URL and still leaves a full (stale)
        table on screen, so only the response code tells the truth about
        whether a sort was accepted.
        """
        seen = []

        def on_response(response):
            if "/operations/hub" in response.url:
                match = re.search(r"sort_by=([a-z_]+)", response.url)
                order = re.search(r"sort_order=(asc|desc)", response.url)
                seen.append((match.group(1) if match else None,
                             order.group(1) if order else None, response.status))

        self.page.on("response", on_response)
        return seen, on_response

    def sort_cpos(self):
        """Sort every sortable CPO column both ways.

        Deal Type, Next Maint. and Criticality are "—" for most CPOs on staging,
        so a *correct* sort can legitimately leave the visible order unchanged
        -- a "did the rows move?" check would fail on data rather than on
        behaviour. Those are asserted on the API accepting the request (HTTP
        200) with the table still populated. CPO and Sites are always
        populated, so for them the row order itself is checked as well.
        """
        seen, listener = self._watch_cpo_api()
        try:
            for col, param in self.CPO_SORTS.items():
                for direction in ("asc", "desc"):
                    seen.clear()
                    self._header(col).click()
                    self.page.wait_for_url(
                        re.compile(rf"[?&]sort_order={direction}"), timeout=10000
                    )
                    assert self._poll(
                        lambda p=param, d=direction: any(
                            s == p and o == d for s, o, _ in seen),
                        timeout_ms=15000,
                    ), f"sorting by {col!r} {direction} never called the API"
                    statuses = [st for s, o, st in seen
                                if s == param and o == direction]
                    assert all(st == 200 for st in statuses), (
                        f"sorting by {col!r} {direction} was rejected by the API: "
                        f"{statuses} (sent sort_by={param})"
                    )
                    asc = direction == "asc"
                    if col == "CPO":
                        assert self._poll(
                            lambda a=asc: self._sorted_ok(
                                self._order(self._cpo_rows()), a),
                            timeout_ms=15000,
                        ), (
                            f"the CPO names are not in {direction} order: "
                            f"{self._order(self._cpo_rows())[:6]}"
                        )
                    elif col == "Sites":
                        assert self._poll(
                            lambda a=asc: self._numbers_sorted(
                                self._column_values("Sites", self._cpo_rows()), a),
                            timeout_ms=15000,
                        ), (
                            f"the Sites counts are not in {direction} order: "
                            f"{self._column_values('Sites', self._cpo_rows())}"
                        )
                    else:
                        assert self._poll(lambda: self._cpo_rows().count() > 0,
                                          timeout_ms=20000), (
                            f"the CPO list is empty after sorting by {col!r} {direction}"
                        )
                log.info("Column %-12s sorts asc+desc (sort_by=%s, HTTP 200)",
                         col, param)
        finally:
            self.page.remove_listener("response", listener)

        # Drop the sort so the rest of the run sees the default order.
        self.page.goto(self.page.url.split("?")[0])
        assert self._poll(lambda: self._cpo_rows().count() > 0, timeout_ms=25000)
        self._park_mouse()

    def paginate_cpos(self):
        """Step, jump and resize through the CPO list."""
        first_page = self._order(self._cpo_rows())
        assert self.next_page.is_enabled(), "expected more than one page of CPOs"

        log.info("Paging forward and back through the CPO list")
        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2"), timeout=10000)
        assert self._poll(
            lambda: self._order(self._cpo_rows()) and
            self._order(self._cpo_rows()) != first_page
        ), "page 2 shows the same CPOs as page 1"

        self.prev_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=10000)
        assert self._poll(lambda: self._order(self._cpo_rows()) == first_page), (
            "going back did not restore page 1"
        )

        # A direct page jump. Matched exactly -- "Go to page 1" is a prefix of
        # "Go to page 10" and a substring match would resolve to both.
        page_3 = self.page.get_by_role("button", name="Go to page 3", exact=True)
        if page_3.count():
            log.info("Jumping straight to page 3")
            page_3.click()
            self.page.wait_for_url(re.compile(r"[?&]page=3"), timeout=10000)
            assert self._poll(
                lambda: self._order(self._cpo_rows()) and
                self._order(self._cpo_rows()) != first_page
            ), "page 3 shows the same CPOs as page 1"
            self.page.get_by_role("button", name="Go to page 1", exact=True).click()
            self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=10000)
            assert self._poll(lambda: self._order(self._cpo_rows()) == first_page), (
                "returning to page 1 did not restore it"
            )

        current = (self.page_size.text_content() or "").strip()
        before = self._cpo_rows().count()
        target = "50" if current != "50" else "100"
        log.info("Switching the page size from %s to %s", current, target)
        self.page_size.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name=target, exact=True).click()
        assert self._poll(lambda: self._cpo_rows().count() > before), (
            f"expected more than {before} CPOs at page size {target}, "
            f"got {self._cpo_rows().count()}"
        )
        log.info("Page size %s shows %s CPOs", target, self._cpo_rows().count())

        log.info("Restoring the page size to %s", current)
        self.page_size.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name=current, exact=True).click()
        assert self._poll(lambda: self._cpo_rows().count() == before), (
            f"expected {before} CPOs after restoring page size {current}, "
            f"got {self._cpo_rows().count()}"
        )

    # ----------------------------------------------------------------- #
    # Hub filters
    # ----------------------------------------------------------------- #
    def _remove_chip(self, choice):
        """The chip an applied filter adds below the bar ("Remove <choice>")."""
        return self.page.get_by_role("button", name=f"Remove {choice}", exact=True)

    def _apply_filter(self, trigger, options, choice, param, name):
        """Open `trigger`, check its options, tick `choice` and Apply.

        The filters are multi-selects: ticking an option does nothing until
        Apply is pressed. The applied filter is confirmed three ways -- it
        pushes its own query parameter, the trigger relabels itself to the
        chosen value, and a removable chip appears -- so a popover that closes
        without doing anything cannot pass.
        """
        self._open_filter(trigger, options, name)
        log.info("Applying the %s filter %r", name, choice)
        self.page.get_by_role("option", name=choice, exact=True).click()
        self.dialog.last.get_by_role("button", name="Apply").click()
        self.page.wait_for_url(re.compile(rf"[?&]{param}="), timeout=10000)
        expect(trigger).to_contain_text(choice)
        expect(self._remove_chip(choice)).to_be_visible()

    def _clear_filter(self, choice, param, name):
        """Drop an applied filter through its own chip."""
        log.info("Clearing the %s filter", name)
        self._remove_chip(choice).click()
        self.page.wait_for_url(lambda url: f"{param}=" not in url, timeout=10000)

    def filter_cpos(self):
        """Apply each hub filter, check the list agrees, then clear it.

        Deal Type and Criticality are asserted on their own column of every
        remaining CPO row. The CPO filter must leave exactly the chosen CPO.
        The Status column holds a per-status site count rather than a label,
        so the Status filter is asserted on the list narrowing and on every
        CPO reporting a "matched/total" site count.
        """
        before = self._total()
        cases = (
            (self.type_filter, self.DEAL_TYPES, self.DEAL_TYPES[0],
             "deal_type", "Deal Type", "Deal Type"),
            (self.criticality_filter, self.CRITICALITIES, self.CRITICALITIES[-1],
             "criticality", "Criticality", "Criticality"),
            (self.status_filter, self.STATUSES, self.STATUSES[0],
             "status", "Status", None),
            (self.cpo_filter, ["Capurro Garage"], "Capurro Garage",
             "cpo_id", "CPO", "CPO"),
        )
        for trigger, options, choice, param, name, column in cases:
            self._apply_filter(trigger, options, choice, param, name)
            # The URL updates the moment Apply is pressed, but the table only
            # catches up when the refetch lands -- so poll on the total.
            assert self._poll(lambda: 0 < self._total() < before, timeout_ms=15000), (
                f"the {name} filter {choice!r} should narrow the list from "
                f"{before}, got {self._total()}"
            )
            if column == "CPO":
                assert self._poll(
                    lambda: self._order(self._cpo_rows()) == [choice]
                ), f"the CPO filter left {self._order(self._cpo_rows())}"
                log.info("CPO filter %r leaves just that CPO", choice)
            elif column:
                assert self._poll(
                    lambda c=column: self._rows_match(c, choice, self._cpo_rows()),
                    timeout_ms=15000,
                ), (
                    f"the {name} filter {choice!r} left rows that do not match: "
                    f"{self._column_values(column, self._cpo_rows())[:3]}"
                )
                self._assert_column(column, choice, f"{name} {choice!r}",
                                    self._cpo_rows(), exact=True)
            else:
                for value in self._column_values("Sites", self._cpo_rows()):
                    assert re.fullmatch(r"\d+(/\d+)?", value), (
                        f"{name} {choice!r}: unexpected Sites cell {value!r}"
                    )
                log.info("%s %r narrows the hub to %s CPO(s)",
                         name, choice, self._total())

            self._clear_filter(choice, param, name)
            assert self._poll(lambda: self._total() == before), (
                f"expected {before} CPO(s) after clearing the {name} filter, "
                f"got {self._total()}"
            )

    # ----------------------------------------------------------------- #
    # Row expansion (CPO -> sites -> site panel)
    # ----------------------------------------------------------------- #
    def expand_cpo_rows(self):
        """Expand a CPO into its sites, a site into its panel, then collapse.

        Replaces the old CPO drill-down page and the separate Sites view: a CPO
        row now expands in place into a nested list of its sites, and each of
        those expands again into the asset / organisation / maintenance panel.
        """
        log.info("Finding %r", CPO)
        self.hub_search.fill(CPO)
        assert self._poll(
            lambda: CPO in self._order(self._cpo_rows()), timeout_ms=15000
        ), f"{CPO!r} is not listed after searching for it"
        row = self._cpo_rows().filter(has_text=CPO).first
        expected = int(self._column_values("Sites", self._cpo_rows().filter(
            has_text=CPO))[0])

        log.info("Expanding %r into its sites", CPO)
        self._park_mouse()
        row.get_by_role("button", name="Expand row").click()
        assert self._poll(
            lambda: self._nested_site_rows().count() == expected, timeout_ms=20000
        ), (
            f"{CPO} lists {expected} site(s) but its expanded row shows "
            f"{self._nested_site_rows().count()}"
        )
        expect(row.get_by_role("button", name="Collapse row")).to_be_visible()
        log.info("%r expands into its %s site(s)", CPO, expected)

        log.info("Expanding the first site's detail panel")
        nested = self.table.locator("table").first
        nested_rows = nested.locator(":scope > tbody > tr")
        base = nested_rows.count()
        self._nested_site_rows().first.get_by_role("button", name="Expand row").click()
        assert self._poll(lambda: nested_rows.count() == base + 1), (
            f"expanding a site should add a panel row to the {base} present, "
            f"got {nested_rows.count()}"
        )
        self._assert_panel(nested.locator(":scope > tbody"), "expanded site")
        nested.get_by_role("button", name="Collapse row").click()
        assert self._poll(lambda: nested_rows.count() == base), (
            f"expected {base} site row(s) after collapsing, got {nested_rows.count()}"
        )

        row.get_by_role("button", name="Collapse row").click()
        assert self._poll(lambda: self._nested_site_rows().count() == 0), (
            "collapsing the CPO row left its sites on screen"
        )
        self._clear_search(self.hub_search)
        assert self._poll(lambda: self._cpo_rows().count() > 1, timeout_ms=15000)

        # The header toggle expands every CPO on the page at once.
        log.info("Expanding, then collapsing, every CPO row")
        page_rows = self._cpo_rows().count()
        self._park_mouse()
        self.expand_all.click()
        assert self._poll(
            lambda: self.table.locator("table").count() == page_rows,
            timeout_ms=25000,
        ), (
            f"Expand all should open all {page_rows} CPO row(s), "
            f"{self.table.locator('table').count()} opened"
        )
        self.collapse_all.click()
        assert self._poll(lambda: self.table.locator("table").count() == 0), (
            "Collapse all left some CPO rows open"
        )
        expect(self.expand_all).to_be_visible()
        log.info("Expand all / Collapse all toggle all %s CPO row(s)", page_rows)

    # ----------------------------------------------------------------- #
    # Site detail
    # ----------------------------------------------------------------- #
    def open_site_detail(self):
        """Open a site's own page and walk every tab it offers in depth.

        Pinned to `SITE` rather than "whichever row is first", because the
        Maintenance walk needs a site that actually has plans -- see the note on
        the constant. The site is reached the way a user now does: search the
        hub for it, expand its CPO, and "Open site".
        """
        log.info("Opening the detail page for %r", SITE)
        self.hub_search.fill(SITE)
        # Poll until the rows agree with the search rather than just until some
        # rows exist -- the previous, unfiltered list is still on screen while
        # the search refetches, and would otherwise be acted on.
        assert self._poll(
            lambda: self._order(self._cpo_rows()) == [SITE_CPO], timeout_ms=15000
        ), f"the site search never settled on {SITE_CPO!r}"
        self._park_mouse()
        self._cpo_rows().first.get_by_role("button", name="Expand row").click()
        site_row = self._nested_site_rows().filter(has_text=SITE)
        assert self._poll(lambda: site_row.count() == 1, timeout_ms=20000), (
            f"expanding {SITE_CPO!r} did not list {SITE!r}"
        )
        site_row.get_by_role("button", name="Open site").click()

        # /operations-hub/<cpo-uuid>/<site-uuid>
        self.page.wait_for_url(
            re.compile(r"/operations/operations-hub/[0-9a-f-]{36}/[0-9a-f-]{36}"),
            timeout=20000,
        )
        self.back_to_hub.wait_for(state="visible", timeout=20000)
        # The header states the site, its CPO, its external ID and its device
        # counts. CSS upper-cases the labels, so they are compared on
        # textContent, case-insensitively.
        main = self.page.locator("main")
        assert self._poll(
            lambda: SITE.lower() in (main.text_content() or "").lower(),
            timeout_ms=15000,
        ), f"the site page does not name {SITE!r}"
        header = (main.text_content() or "").lower()
        for label in ("Site :", "CPO", "External ID", "Charging devices",
                      "Sockets", "Next maintenance"):
            assert label.lower() in header, f"the site header is missing {label!r}"
        assert SITE_CPO.lower() in header, f"the site header does not name {SITE_CPO!r}"
        for tab in self.SITE_TABS:
            expect(self._tab(tab)).to_be_visible()
        # The Maintenance tab has gone from the site page's tab bar; the panel
        # is now reached from the CPO page's per-row Maintenance action (see
        # browse_cpo_page).
        assert not self.page.get_by_role(
            "button", name=re.compile(r"^Maintenance")
        ).count(), "the site page unexpectedly shows a Maintenance tab again"
        log.info("Site detail open for %s", SITE)

        self._check_breadcrumb()
        self._browse_site_info()
        self._browse_tracker()
        self._browse_records()
        self.browse_cpo_page()
        self._leave_site_detail()

    def _check_breadcrumb(self):
        """The trail links the hub and the CPO, and ends on the site."""
        expect(self.breadcrumb_hub).to_be_visible()
        expect(self.breadcrumb.get_by_role("link", name=SITE_CPO)).to_be_visible()
        expect(self.breadcrumb).to_contain_text(SITE)
        log.info("Breadcrumb shows Operations Hub > %s > %s", SITE_CPO, SITE)

    def _tab(self, name):
        """A site tab, whose label may carry a count ("Records 2")."""
        return self.page.get_by_role(
            "button", name=re.compile(rf"^{re.escape(name)}(\s*\d+)?$")
        ).first

    # -- Site Info ----------------------------------------------------- #
    def _browse_site_info(self):
        self._tab("Site Info").click()
        body = self.page.locator("body")
        assert self._poll(
            lambda: "Address :" in (body.inner_text() or ""), timeout_ms=15000
        ), "the Site Info tab never rendered"
        text = body.inner_text() or ""
        for label in ("Address :", "City :", "Postal Code :", "Contact Person :",
                      "Contact No :", "Latitude :", "Longitude :"):
            assert label in text, f"the Site Info tab is missing {label!r}"
        # The site's hardware is listed underneath its location details. The
        # form is editable in place (with Delete / Add controls), which this
        # run deliberately never touches.
        for label in ("Charging Device", "Model :", "Status :", "Socket"):
            assert label in text, f"the Site Info tab is missing {label!r}"
        log.info("Site Info tab shows the location, contact and hardware details")

    # -- Tracker ------------------------------------------------------- #
    def _browse_tracker(self):
        self._tab("Tracker").click()
        self.page.wait_for_timeout(2500)
        body = self.page.locator("main").inner_text() or ""
        # A site either has a tracker attached or says plainly that it has none.
        assert "Tracker" in body, "the Tracker tab renders nothing"
        if "No Tracker Attached" in body:
            log.info("Tracker tab: no tracker attached to this site")
        else:
            log.info("Tracker tab: a tracker is attached")

    # ----------------------------------------------------------------- #
    # CPO sites page (via the site's breadcrumb)
    # ----------------------------------------------------------------- #
    def browse_cpo_page(self):
        """Follow the breadcrumb up to the CPO's own sites page and exercise it.

        This page keeps the older drill-down table: its column set, Deal Type
        and Criticality filters, row expansion with "Collapse all rows", and a
        per-row Maintenance action -- which is now the way into a site's
        Maintenance panel.
        """
        log.info("Opening %r's own sites page from the breadcrumb", SITE_CPO)
        self.breadcrumb.get_by_role("link", name=SITE_CPO).click()
        self.page.wait_for_url(
            re.compile(r"/operations/operations-hub/[0-9a-f-]{36}(\?|$)"), timeout=15000
        )
        assert self._poll(lambda: self._site_rows().count() > 0, timeout_ms=25000), (
            f"{SITE_CPO} sites page never listed any sites"
        )
        self._park_mouse()
        log.info("CPO sites page open with %s site(s)", self._site_rows().count())

        columns = self._columns()
        assert columns == self.CPO_DETAIL_COLUMNS, (
            f"unexpected CPO page column set: {columns} != {self.CPO_DETAIL_COLUMNS}"
        )
        expect(self.site_search).to_be_visible()

        self._filter_sites()
        self._expand_and_collapse()
        self._browse_maintenance()

    def _filter_sites(self):
        """Apply the deal-type and criticality filters on the CPO sites page.

        Each filter is asserted on its option set, really applied, and then
        removed through its chip. The Criticality filter is asserted on its
        own column of every remaining row.

        The Deal Type filter is asserted on the list narrowing only: every site
        row prints "Managed" in its Deal Type column whichever deal type it was
        filtered on (while the hub's CPO row prints e.g. "O&M with Install").
        That mismatch is reported as a suspected product bug rather than pinned
        here.
        """
        before = self._site_rows().count()

        for trigger, options, choice, param, name, column in (
            (self.type_filter, self.DEAL_TYPES, self.DEAL_TYPES[0],
             "deal_type", "Deal type", None),
            (self.criticality_filter, self.CRITICALITIES, self.CRITICALITIES[-1],
             "criticality", "Criticality", "Criticality"),
        ):
            self._apply_filter(trigger, options, choice, param, name)
            # The URL updates the moment Apply is pressed, but the table only
            # catches up when the refetch lands -- so poll for the *rows*.
            assert self._poll(
                lambda: 0 < self._site_rows().count() < before, timeout_ms=15000
            ), (
                f"the {name} filter {choice!r} should narrow the {before} site(s), "
                f"got {self._site_rows().count()}"
            )
            if column:
                assert self._poll(
                    lambda c=column: self._rows_match(c, choice, self._site_rows()),
                    timeout_ms=15000,
                ), (
                    f"the {name} filter {choice!r} left rows that do not match: "
                    f"{self._column_values(column, self._site_rows())[:3]}"
                )
                self._assert_column(column, choice, f"{name} {choice!r}",
                                    self._site_rows(), exact=True)
            else:
                log.info("%s %r narrows the CPO page to %s site(s)",
                         name, choice, self._site_rows().count())

            self._clear_filter(choice, param, name)
            assert self._poll(lambda: self._site_rows().count() == before), (
                f"expected {before} site(s) after clearing the {name} filter, "
                f"got {self._site_rows().count()}"
            )

    def _expand_and_collapse(self):
        """Expand a row's detail panel, then collapse every row."""
        base = self.rows.count()
        log.info("Expanding the first site row")
        self._park_mouse()
        self.expand_row.first.click()
        assert self._poll(lambda: self.rows.count() == base + 1), (
            f"expanding a row should add a detail row to the {base} present, "
            f"got {self.rows.count()}"
        )
        # The CPO page's panel is the hub's without the Lifecycle line.
        self._assert_panel(
            self.table.locator(":scope > tbody"), "CPO page site",
            [label for label in self.SITE_PANEL_LABELS if label != "Lifecycle"],
        )

        log.info("Collapsing all rows")
        self.collapse_all.click()
        assert self._poll(lambda: self.rows.count() == base), (
            f"expected {base} row(s) after collapsing all, got {self.rows.count()}"
        )

    # -- Maintenance --------------------------------------------------- #
    def _browse_maintenance(self):
        """Open the site's Maintenance panel and walk it.

        The site page no longer has a Maintenance tab: the panel is reached
        from the CPO page's per-row "Maintenance" action, which opens the site
        page on ?tab=maintenance.
        """
        log.info("Opening %r's Maintenance panel from its row action", SITE)
        row = self._site_rows().filter(has_text=SITE)
        assert self._poll(lambda: row.count() == 1), (
            f"{SITE!r} is not on {SITE_CPO!r}'s sites page"
        )
        row.get_by_role("button", name="Maintenance", exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]tab=maintenance"), timeout=20000)
        self.add_events.wait_for(state="visible", timeout=20000)
        expect(self.create_plan).to_be_visible()
        log.info("Maintenance panel open")

        self._maintenance_subtabs()
        self._maintenance_plan_card()
        # Plans and events cannot be deleted, so every form here is filled,
        # checked and cancelled -- never submitted.
        self._validate_create_plan()
        self._validate_edit_plan()
        self._validate_create_event()

    def _maintenance_tab(self, name):
        """A Maintenance sub-tab, whose label may carry a count."""
        return self.page.get_by_role(
            "button", name=re.compile(rf"^{re.escape(name)}(\s*\(\d+\))?$")
        ).first

    def _maintenance_subtabs(self):
        """Step through All Plans / Upcoming Events / Scheduled / Completed."""
        for name, markers in self.MAINTENANCE_TABS.items():
            tab = self._maintenance_tab(name)
            expect(tab).to_be_visible()
            tab.click()
            assert self._poll(
                lambda m=markers: any(
                    marker in (self.page.locator("body").inner_text() or "")
                    for marker in m
                ),
                timeout_ms=15000,
            ), (
                f"the {name!r} sub-tab renders neither items nor an empty state "
                f"(expected one of {markers})"
            )
            if name == "All Plans":
                log.info("Maintenance sub-tab %-16s -> %s plan(s)",
                         name, self.edit_plan.count())
            else:
                log.info("Maintenance sub-tab %-16s -> rendered", name)

        # Come back to the plans, which the card checks below rely on.
        self._maintenance_tab("All Plans").click()
        self.page.wait_for_timeout(2500)
        assert self.edit_plan.count() > 0, "All Plans lists no maintenance plan"

    def _maintenance_plan_card(self):
        """Exercise a plan card's own controls: notes, documents, all-events."""
        body = self.page.locator("body").inner_text() or ""
        # A plan states its frequency, its category and when it was last updated.
        for label in ("Next Event:", "Last Updated:"):
            assert label in body, f"a plan card is missing {label!r}"
        assert any(f in body for f in self.PLAN_FREQUENCIES), (
            f"no plan card states a frequency from {self.PLAN_FREQUENCIES}"
        )
        assert any(c in body for c in self.EVENT_CATEGORIES), (
            f"no plan card states a category from {self.EVENT_CATEGORIES}"
        )
        log.info("Plan cards state their frequency, category and next event")

        # Long notes are truncated behind a Read more / Read less toggle.
        if self.read_more.count():
            log.info("Expanding a plan's notes with Read more")
            self.read_more.first.click()
            self.page.wait_for_timeout(1200)
            expect(self.read_less.first).to_be_visible()
            self.read_less.first.click()
            self.page.wait_for_timeout(1200)
            expect(self.read_more.first).to_be_visible()

        # The Documents section collapses and expands.
        if self.plan_documents.count():
            log.info("Toggling a plan's Documents section")
            before = self.page.locator("body").inner_text()
            self.plan_documents.first.click()
            assert self._poll(
                lambda: self.page.locator("body").inner_text() != before
            ), "toggling Documents changed nothing"
            self.plan_documents.first.click()
            self.page.wait_for_timeout(1200)

        # "View All Events" is exercised, but on staging it does not navigate or
        # raise a dialog -- so the check is that the plans survive the click
        # rather than a claim about where it goes.
        if self.view_all_events.count():
            log.info("Clicking View All Events")
            plans = self.edit_plan.count()
            self.view_all_events.first.click()
            self.page.wait_for_timeout(2500)
            assert self.edit_plan.count() == plans, (
                "View All Events left the plan list in a different state"
            )

    # -- Shared form helpers ------------------------------------------- #
    def _choose_option(self, dlg, trigger_name, value, expected_options=None):
        """Open a dropdown inside `dlg` and pick `value`.

        The popover renders as its own dialog on top of the form, so the option
        is clicked at page level rather than inside `dlg`.
        """
        dlg.get_by_role("button", name=trigger_name).first.click()
        self.page.wait_for_timeout(900)
        if expected_options:
            options = [o.inner_text().strip()
                       for o in self.page.get_by_role("option").all()]
            for option in expected_options:
                assert option in options, (
                    f"the {trigger_name!r} list is missing {option!r} (has {options})"
                )
        self.page.get_by_role("option", name=value, exact=True).click()
        self.page.wait_for_timeout(900)

    def _today(self):
        return datetime.date.today().isoformat()

    def _tomorrow(self):
        """Tomorrow, in ISO form.

        Events are scheduled for tomorrow rather than today because the API
        rejects a start time in the past ("Scheduled start cannot be in the
        past") -- and any fixed hour today is in the past for most of the day.
        """
        return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    def _pick_day(self, popover, day=None):
        """Click `day` (ISO) in an open calendar, defaulting to today.

        Day cells carry `data-day="YYYY-MM-DD"`, which is exact -- picking by
        the visible number would be ambiguous, because the grid also renders the
        neighbouring months' spill-over days. Those spill-over cells are marked
        `data-outside` and are preferred against only when the wanted day is in
        the current month, so that "tomorrow" still works on the last day of a
        month, when it shows up as a trailing outside cell.
        """
        day = day or self._today()
        cell = popover.locator(f'td[data-day="{day}"]:not([data-outside])')
        if not cell.count():
            cell = popover.locator(f'td[data-day="{day}"]')
        assert cell.count() >= 1, f"the calendar does not offer {day}"
        cell.first.click()
        self.page.wait_for_timeout(1200)

    def _pick_date(self, dlg, day=None, label="Select date"):
        """Open a date field in `dlg` and choose `day` (today by default)."""
        dlg.get_by_role("button").filter(has_text=label).first.click()
        self.page.wait_for_timeout(1200)
        self._pick_day(self.dialog.last, day)

    def _pick_datetime(self, dlg, hour, minute="0", day=None):
        """Fill the next empty date-and-time field with `day` at `hour`:`minute`.

        The picker is a calendar plus two native <select>s and a Done button, so
        the time is set with select_option rather than by clicking through a
        list.
        """
        dlg.get_by_role("button").filter(
            has_text="Select date & time"
        ).first.click()
        self.page.wait_for_timeout(1200)
        popover = self.dialog.last
        self._pick_day(popover, day)
        popover.locator("select[aria-label='Hour']").select_option(hour)
        popover.locator("select[aria-label='Minute']").select_option(minute)
        popover.get_by_role("button", name="Done").click()
        self.page.wait_for_timeout(1200)

    def _plan_card(self, title):
        """The plan card carrying `title`, scoped to its own Edit Plan button.

        Several plans are listed at once, so every plan-level action is taken
        through this card rather than through a page-level "Edit Plan" -- which
        would silently act on whichever plan happens to be first.
        """
        return self.page.locator("div").filter(has_text=title).filter(
            has=self.page.get_by_role("button", name="Edit Plan")
        ).last

    def _plan_exists(self, title):
        return self.page.get_by_text(title, exact=True).count() > 0

    def _cancel_dialog(self, what):
        """Dismiss a form dialog without saving it."""
        cancel = self.dialog.first.get_by_role("button", name="Cancel")
        if cancel.count():
            cancel.first.click()
        else:
            self.page.keyboard.press("Escape")
        assert self._poll(lambda: self.dialog.count() == 0, timeout_ms=10000), (
            f"the {what} dialog did not close on Cancel"
        )

    # -- Create Plan: validated, never submitted ----------------------- #
    def _validate_create_plan(self):
        """Walk the Create Plan wizard to a submittable state, then cancel.

        Plans cannot be deleted through the UI, so the suite never creates
        one. The form is still proven to guard itself: the submit starts
        disabled, stays disabled with only a title, and enables once every
        required field is filled -- at which point the dialog is cancelled.
        """
        log.info("Validating the Create Plan wizard (never submitted)")
        self.create_plan.click()
        self.dialog.last.wait_for(state="visible", timeout=15000)
        dlg = self.dialog.first

        text = dlg.inner_text() or ""
        # A two-step wizard: plan details, then optional attachments.
        for marker in ("Plan Details", "Attachments", "Plan Title", "Category",
                       "Frequency", "Criticality", "Start Date", "Description"):
            assert marker in text, f"the Create Plan wizard is missing {marker!r}"
        assert "Medium" in text, "Criticality should default to Medium"

        submit = dlg.get_by_role("button", name="Create Plan", exact=True)
        expect(submit, "Create Plan should start disabled").to_be_disabled()
        dlg.locator("input[placeholder*='plan']").first.fill(PLAN_TITLE)
        expect(
            submit, "Create Plan should stay disabled with only a title"
        ).to_be_disabled()

        self._choose_option(dlg, "Select category", "Inspection",
                            self.EVENT_CATEGORIES)
        self._choose_option(dlg, "Select frequency", "Monthly",
                            self.PLAN_FREQUENCIES)
        self._pick_date(dlg)
        dlg.locator("textarea").first.fill(PLAN_DESCRIPTION)
        expect(submit, "Create Plan should enable once the form is complete"
               ).to_be_enabled()

        self._cancel_dialog("Create Plan")
        log.info("Create Plan wizard validated and cancelled")

    # -- Edit Plan: validated, never saved ----------------------------- #
    def _validate_edit_plan(self):
        """Open the suite's plan in the editor, check it, and cancel.

        Saving -- even a save that is later restored -- would rewrite a real
        record on staging on every run, so the editor is only inspected: it
        must open on the right plan, pre-filled, with Save Changes on offer.
        """
        assert self._plan_exists(PLAN_TITLE), (
            f"the {PLAN_TITLE!r} fixture plan is missing from All Plans"
        )
        log.info("Validating the Edit Plan dialog for %r (never saved)",
                 PLAN_TITLE)
        self._open_plan_editor()
        dlg = self.dialog.first

        # Guard: the dialog must belong to our plan, not another card's.
        title = dlg.locator("input[placeholder*='plan']").first.input_value()
        assert title.strip() == PLAN_TITLE, (
            f"the editor opened on {title!r}, not {PLAN_TITLE!r}"
        )
        text = dlg.inner_text() or ""
        for marker in ("Edit Maintenance Plan", "Start Date", "Status",
                       "Category", "Frequency", "Criticality", "Attachments"):
            assert marker in text, f"the Edit Plan dialog is missing {marker!r}"
        assert "ACTIVE" in text, "the plan should be ACTIVE"
        description = dlg.locator("textarea").first.input_value()
        assert description.strip(), "the editor did not pre-fill the description"
        expect(dlg.get_by_role("button", name="Save Changes")).to_be_visible()

        self._cancel_dialog("Edit Plan")
        log.info("Edit Plan dialog validated and cancelled")

    def _open_plan_editor(self):
        """Open Edit Plan on the suite's own card."""
        self._plan_card(PLAN_TITLE).get_by_role(
            "button", name="Edit Plan"
        ).first.click()
        self.dialog.last.wait_for(state="visible", timeout=15000)

    # -- Create Event: validated, never submitted ---------------------- #
    def _validate_create_event(self):
        """Fill the Create Event dialog to a submittable state, then cancel.

        Events cannot be deleted either, so none is ever created. Every field,
        including the assignee and both times, is filled so the check still
        proves the submit enables on a complete form.
        """
        log.info("Validating the Create Event dialog (never submitted)")
        self.add_events.click()
        self.dialog.last.wait_for(state="visible", timeout=15000)
        dlg = self.dialog.first

        text = dlg.inner_text() or ""
        for field in ("Event Title", "Category", "Assign to", "Due Date",
                      "Start Time", "End Time", "Description", "Notes"):
            assert field in text, f"the Create Event dialog is missing {field!r}"

        submit = dlg.get_by_role("button", name="Create Event", exact=True)
        expect(submit, "Create Event should start disabled").to_be_disabled()
        dlg.locator("input[placeholder*='event']").first.fill(EVENT_TITLE)
        expect(
            submit, "Create Event should stay disabled with only a title"
        ).to_be_disabled()

        self._choose_option(dlg, "Select category", "Inspection",
                            self.EVENT_CATEGORIES)
        # The assignee popover lists users as plain rows, not options.
        dlg.get_by_role("button", name="Select assignee").click()
        self.page.wait_for_timeout(1200)
        self.dialog.last.get_by_text(ASSIGNEE, exact=True).first.click()
        self.page.wait_for_timeout(1000)

        # Tomorrow: the form refuses a start time in the past, so a fixed hour
        # today would leave the submit disabled for most of the day.
        tomorrow = self._tomorrow()
        self._pick_date(dlg, tomorrow)
        self._pick_datetime(dlg, hour="9", day=tomorrow)
        self._pick_datetime(dlg, hour="10", day=tomorrow)
        dlg.locator("textarea").first.fill("Validated by the automated suite.")
        expect(submit, "Create Event should enable once the form is complete"
               ).to_be_enabled()

        self._cancel_dialog("Create Event")
        log.info("Create Event dialog validated and cancelled")

    # -- Records ------------------------------------------------------- #
    def _browse_records(self):
        """Step through every document category on the Records tab."""
        self._tab("Records").click()
        main = self.page.locator("main")
        for category in self.RECORD_CATEGORIES:
            tab = self.page.get_by_role("button", name=category, exact=True)
            assert self._poll(lambda t=tab: t.count() > 0, timeout_ms=15000), (
                f"the Records tab has no {category!r} category"
            )
            tab.first.click()
            # Each category states how many documents it holds, then lists them
            # or says it has none. Both are valid; which one depends on data.
            assert self._poll(
                lambda: re.search(r"\d+ documents? held against this site",
                                  main.inner_text() or ""),
                timeout_ms=10000,
            ), f"selecting {category!r} did not open its document panel"
            text = main.inner_text() or ""
            count = int(re.search(r"(\d+) documents? held", text).group(1))
            if count == 0:
                assert "No documents found in this category." in text, (
                    f"{category!r} holds no documents but shows no empty state"
                )
            log.info("Records category %-12s -> %s document(s)", category, count)
        log.info("Records tab steps through all %s categories",
                 len(self.RECORD_CATEGORIES))
        # The upload control is present but deliberately not used.
        expect(self.page.get_by_role("button", name="Add records").first).to_be_visible()

    def _leave_site_detail(self):
        """Go back to the hub through the breadcrumb, not the sidebar."""
        log.info("Returning to the Operations Hub via the breadcrumb")
        self.breadcrumb_hub.click()
        self.page.wait_for_url(
            re.compile(r"/operations/operations-hub(\?|$)"), timeout=15000
        )
        assert self._poll(lambda: self._cpo_rows().count() > 0, timeout_ms=25000), (
            "the breadcrumb did not get back to the CPO list"
        )
        self._park_mouse()

    # ----------------------------------------------------------------- #
    # Map View
    # ----------------------------------------------------------------- #
    def browse_map(self):
        """Switch to the Map View and exercise its filters, legend and zoom."""
        log.info("Switching to the Map View")
        self.map_view.click()
        self.page.wait_for_url(re.compile(r"[?&]view=map"), timeout=15000)
        self.map_canvas.first.wait_for(state="visible", timeout=25000)
        log.info("Map rendered")

        # The legend explains the marker colours; every threshold must be named.
        body = self.page.locator("body").inner_text() or ""
        for state in ("Overdue", "In Progress", "Scheduled", "Upcoming",
                      "Completed", "No Maintenance"):
            assert state in body, f"the map legend is missing {state!r}"
        log.info("Map legend lists all six maintenance states")

        # Markers: one card per organisation, each naming its ID and site
        # count. They are drawn a few seconds after the map container itself
        # appears, so this polls rather than reading the count the instant the
        # canvas is visible -- checking immediately is a race that fails on a
        # working map.
        markers = self.page.get_by_role("button", name=re.compile(r"ID:\s*\d+"))
        assert self._poll(lambda: markers.count() > 0, timeout_ms=30000), (
            "the map shows no markers"
        )
        log.info("Map shows %s marker card(s)", markers.count())

        # All three filters are present with their full option sets. The CPO
        # filter lists every CPO, so it is checked on its size rather than on a
        # fixed set of names that staging data could change.
        self.cpo_filter.click()
        self.page.wait_for_timeout(800)
        options = [o.inner_text().strip() for o in self.page.get_by_role("option").all()]
        assert len(options) > 5, f"the map CPO filter looks empty: {options}"
        log.info("Map CPO filter offers %s CPO(s)", len(options))
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(600)

        self._open_filter(self.type_filter, self.DEAL_TYPES, "Map deal type")
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(600)
        self._open_filter(self.criticality_filter, self.CRITICALITIES, "Map criticality")
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(600)

        log.info("Zooming the map in and back out")
        self.zoom_in.click()
        self.page.wait_for_timeout(1200)
        self.zoom_out.click()
        self.page.wait_for_timeout(1200)
        expect(self.map_canvas.first).to_be_visible()

        log.info("Switching back to the List View")
        self.list_view.click()
        self.page.wait_for_url(lambda url: "view=map" not in url, timeout=15000)
        assert self._poll(lambda: self._cpo_rows().count() > 0, timeout_ms=25000), (
            "the List View did not come back"
        )

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def operations_hub_page(self):
        self.open_page()
        self.check_cpo_table()
        self.search_cpos()
        self.sort_cpos()
        self.paginate_cpos()
        self.filter_cpos()
        self.expand_cpo_rows()
        self.open_site_detail()
        self.browse_map()
        log.info("Operations Hub workflow completed")
