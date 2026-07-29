import datetime
import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.operations_hub")

# The CPO the drill-down and the search checks are pinned to. Babergh Council
# is a small, stable CPO on staging (4 sites), so its detail page fits on a
# single page and the row counts stay readable in the log.
CPO = "Babergh Council"

# A search term that matches exactly one CPO and one site, used to prove the
# two search boxes really filter rather than just re-rendering the list.
SEARCH_TERM = "Capurro"

# The site the detail-page walk is pinned to. It is chosen deliberately rather
# than taken as "whichever row is first": the site walk reads the Maintenance
# tab, and this is the one site on staging that actually carries maintenance
# plans (2 plans, 1 upcoming and 7 completed events) plus a plan with notes and
# an attachment. A site picked at random is usually empty, which would quietly
# reduce the maintenance checks to asserting an empty state.
SITE = "345 Woodbridge Road CO OP"

# --------------------------------------------------------------------------- #
# Maintenance write fixtures
#
# The Maintenance tab is the one place in this workflow that writes. Neither a
# maintenance plan nor an event can be deleted -- the UI exposes no delete
# control anywhere -- so the suite deliberately does NOT create a new one on
# every run. Instead it keeps a single, clearly-labelled plan and event and
# reuses them: the first run creates them, and every run after that finds them
# and exercises the edit path against them. That gives full create *and* edit
# coverage while leaving exactly one permanent artefact on the site rather than
# one per run.
#
# If either is ever deleted from the database, the next run simply recreates it.
# --------------------------------------------------------------------------- #
PLAN_TITLE = "AUTOMATION - do not modify"
# Deliberately different from PLAN_TITLE: a plan generates its own events under
# the *plan's* name, so sharing one title would make the "does the event already
# exist?" check match the plan's generated event and silently skip the Create
# Event flow for ever.
EVENT_TITLE = "AUTOMATION EVENT - do not modify"

# The plan's description is edited and restored on every run, so these two
# strings are the round trip: the run leaves the plan on PLAN_DESCRIPTION.
PLAN_DESCRIPTION = (
    "Created by the automated regression suite. Reused on every run -- "
    "please leave it in place."
)
PLAN_DESCRIPTION_EDITED = (
    "Edited by the automated regression suite to prove the save path works. "
    "This is restored before the run ends."
)

# The account the suite logs in as, used as the event assignee.
ASSIGNEE = "himanshu@equidria.com"


class operations_hub:
    """Operations Hub (/operations/operations-hub).

    The operational view of the estate. It opens on a List View that can be
    read either as CPOs (one row per charge point operator) or as Sites (one
    row per site), and the same data can be shown as a Map View. From the list
    a CPO drills into its own sites page, and a site drills into a per-site
    page with Site Info / Tracker / Maintenance / Records tabs.

    The whole workflow is read-only. It exercises every control the hub
    exposes -- the List/Map toggle, the CPO/Sites view switch, both search
    boxes and their empty states, column sorting, the page-size selector and
    pagination, the CPO drill-down with its type and criticality filters and
    its expand/collapse-all rows, the per-site row expansion, the site detail
    tabs, and the map's filters, legend, zoom and markers -- but it never
    creates, edits or deletes anything. The site's Maintenance tab is *read*,
    and its "Add Events" / "Create Maintenance Plan" / "Edit Plan" controls are
    deliberately left alone, so the run always leaves staging as it found it.
    """

    # List View, CPOs mode.
    CPO_COLUMNS = ["CPO Name", "Sites", "Deal Type", "Next Maint.", "Criticality"]

    # CPO columns whose sort actually works, and the `sort_by` value each sends.
    CPO_SORTS = {
        "Deal Type": "deal_type",
        "Next Maint.": "next_maintenance",
        "Criticality": "criticality",
    }

    # KNOWN BUG -- the CPO Name and Sites headers are broken on staging.
    #
    # The backend accepts sort_by of 'cpo_name', 'sites', 'deal_type',
    # 'next_maintenance' or 'criticality', but the frontend sends 'name' and
    # 'total_sites' for these two columns. Each click therefore returns
    # HTTP 422 (VALIDATION_ERROR) and the table silently keeps the previous
    # rows -- clicking those headers appears to do nothing at all.
    #
    # Mapping is {column: (parameter the UI sends, parameter the API expects)}.
    # `_check_broken_sorts` asserts this broken state deliberately, so the suite
    # stays green on a known defect *and* trips the moment someone fixes it --
    # at which point move these two entries into CPO_SORTS above.
    CPO_SORTS_BROKEN = {
        "CPO Name": ("name", "cpo_name"),
        "Sites": ("total_sites", "sites"),
    }

    # List View, Sites mode. The leading blank column holds the row expander.
    SITE_COLUMNS = [
        "", "Site Name", "CPO", "Deal Type", "Status",
        "Next Maint.", "Criticality", "Lifecycle", "Actions",
    ]
    SITE_SORTS = {"CPO": "cpo", "Status": "status", "Criticality": "criticality"}

    # The CPO drill-down drops the CPO column (every row is that CPO already).
    CPO_DETAIL_COLUMNS = [
        "", "Site Name", "Deal Type", "Status",
        "Next Maint.", "Criticality", "Lifecycle", "Actions",
    ]

    # Options behind the deal-type and criticality filters.
    DEAL_TYPES = ["O&M with Install", "O&M Onboarding", "Fully Funded Install"]
    CRITICALITIES = ["Low", "Medium", "High"]

    # The per-site page's tabs.
    SITE_TABS = ["Site Info", "Tracker", "Maintenance", "Records"]

    # The Maintenance tab's own sub-tabs. Most carry a count in their label
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

        # CPOs / Sites view switch. These are real radio inputs, so they are
        # driven directly rather than by clicking their label text -- "Sites" is
        # also a column header in the CPO table and would be ambiguous.
        self.cpo_mode = page.locator("input[name='ops-view-mode'][value='cpo']")
        self.sites_mode = page.locator("input[name='ops-view-mode'][value='sites']")

        # Search. The placeholder changes with the view mode, which is itself
        # proof that the switch took effect.
        self.cpo_search = page.get_by_placeholder("Search CPOs by name…")
        self.site_search = page.get_by_placeholder(
            "Search sites, charging devices, sockets, IDs, locations…"
        )
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)

        # Table
        self.table = page.locator("table")
        self.rows = page.locator("table tbody tr")

        # Row-level controls (Sites mode and the CPO drill-down)
        self.expand_row = page.get_by_role("button", name="Expand row")
        # An expanded row's own toggle relabels itself. "Collapse all rows" is a
        # header control that only the CPO drill-down offers -- the Sites view
        # collapses row by row.
        self.collapse_row = page.get_by_role("button", name="Collapse row")
        self.collapse_all = page.get_by_role("button", name="Collapse all rows")
        self.view_details = page.get_by_role("button", name="View details")
        self.row_maintenance = page.get_by_role("button", name="Maintenance", exact=True)

        # Filters on the CPO drill-down and the map
        self.type_filter = page.get_by_role("button", name="All types", exact=True)
        self.criticality_filter = page.get_by_role(
            "button", name="All criticalities", exact=True
        )
        self.cpo_filter = page.get_by_role("button", name="All CPOs", exact=True)

        # Pagination
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$")
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Site detail: breadcrumb + Maintenance tab controls
        self.breadcrumb_hub = page.get_by_role("button", name="Operations Hub", exact=True)
        self.add_events = page.get_by_role("button", name="Add Events", exact=True)
        self.create_plan = page.get_by_role(
            "button", name="Create Maintenance Plan", exact=True
        )
        self.edit_plan = page.get_by_role("button", name="Edit Plan")
        self.view_all_events = page.get_by_role("button", name="View All Events")
        self.plan_documents = page.get_by_role("button", name="Documents", exact=True)
        self.read_more = page.get_by_role("button", name="Read more")
        self.read_less = page.get_by_role("button", name="Read less")

        # Dialogs raised from the Maintenance tab. A dropdown's popover is also
        # a dialog and stacks on top of the form, so `.first` is the form and
        # `.last` is whatever opened most recently.
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
    def _cpo_rows(self):
        """Real CPO rows.

        Every CPO row prints its UUID as "ID: ...", while the "No CPOs found"
        empty state is a single plain cell -- so this can never be fooled into
        counting the empty state as data.
        """
        return self.rows.filter(has_text=re.compile(r"ID:\s*\S"))

    def _site_rows(self):
        """Real site rows, excluding expansion panels and the empty state.

        An expanded row inserts a second <tr> holding the Asset / Organisation /
        Maintenance detail panel. Only a real site row carries the "View
        details" action, so filtering on it counts sites rather than <tr>s.
        """
        return self.rows.filter(
            has=self.page.get_by_role("button", name="View details")
        )

    def _columns(self):
        return [
            (h.inner_text() or "").strip()
            for h in self.page.locator("table thead th").all()
        ]

    def _column_index(self, col):
        cols = self._columns()
        assert col in cols, f"the table has no {col!r} column (has {cols})"
        return cols.index(col)

    def _column_values(self, col, row_locator):
        """The `col` cell of every row, or [] if the table re-rendered mid-read.

        `.all()` snapshots element handles, and a refetch landing a moment later
        detaches them -- reading the next cell then raises. Since every caller
        either polls on this or asserts the result is non-empty, returning []
        makes a mid-flight read simply count as "not settled yet" instead of
        crashing the run.
        """
        idx = self._column_index(col)
        values = []
        for row in row_locator.all():
            try:
                values.append((row.locator("td").nth(idx).inner_text() or "").strip())
            except Exception:
                return []
        return values

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
        values = self._column_values(col, row_locator)
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

    def _order(self, row_locator, col_index=0):
        """The first line of each row's `col_index` cell, top to bottom."""
        return [
            (r.locator("td").nth(col_index).inner_text() or "").strip().split("\n")[0]
            for r in row_locator.all()
        ]

    def _header(self, col):
        return self.page.locator(f"//th[normalize-space()='{col}']")

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

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        log.info("Opening the Operations Hub")
        self.hub_link.click()
        self.page.wait_for_url(re.compile(r"/operations/operations-hub"), timeout=15000)
        self.heading.wait_for(state="visible", timeout=15000)
        # The table renders skeleton rows while it loads; wait for real data.
        assert self._poll(lambda: self._cpo_rows().count() > 0, timeout_ms=25000), (
            "the CPO list never loaded"
        )
        # The hub opens on List View in CPOs mode.
        expect(self.cpo_mode).to_be_checked()
        log.info("Hub open in List View / CPOs mode with %s CPO(s) listed",
                 self._cpo_rows().count())

    # ----------------------------------------------------------------- #
    # List View: CPOs
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

        # Every CPO names itself and prints its UUID underneath, and the Sites
        # column is always a number.
        for value in self._column_values("Sites", self._cpo_rows()):
            assert value.isdigit(), f"the Sites column is not a count: {value!r}"
        log.info("CPO table shows %s row(s), each with an ID and a site count", count)

    def search_cpos(self):
        """Search the CPO list, check the column, and check the empty state."""
        before = self._cpo_rows().count()

        log.info("Searching CPOs for %r", SEARCH_TERM)
        self.cpo_search.fill(SEARCH_TERM)
        assert self._poll(lambda: 0 < self._cpo_rows().count() < before), (
            f"the CPO search should narrow the list from {before}, "
            f"got {self._cpo_rows().count()}"
        )
        self._assert_column("CPO Name", SEARCH_TERM, f"CPO search {SEARCH_TERM!r}",
                            self._cpo_rows())

        log.info("Searching CPOs for a term that matches nothing")
        self.cpo_search.fill("zzzz-no-such-cpo")
        assert self._poll(lambda: self._cpo_rows().count() == 0), (
            f"expected no CPOs, got {self._cpo_rows().count()}"
        )
        expect(
            self.page.get_by_text("No CPOs found matching your criteria")
        ).to_be_visible()
        log.info("CPO empty state shown")

        self._clear_search(self.cpo_search)
        assert self._poll(lambda: self._cpo_rows().count() == before), (
            f"expected {before} CPO(s) after clearing the search, "
            f"got {self._cpo_rows().count()}"
        )
        log.info("CPO search cleared, back to %s CPO(s)", before)

    def _sorted_ok(self, values, ascending):
        """True when `values` are in the expected order (case-insensitive)."""
        keys = [v.lower() for v in values if v]
        if len(keys) < 2:
            return False
        return keys == sorted(keys, reverse=not ascending)

    def _watch_cpo_api(self):
        """Record the status of every CPO-list request, keyed by its sort_by.

        Sorting is asserted on the API response, not just on the rows: the two
        broken columns still update the URL and still leave a full table on
        screen (the stale one), so only the response code tells the truth about
        whether a sort was accepted.
        """
        seen = []

        def on_response(response):
            if "/operations/cpos" in response.url:
                match = re.search(r"sort_by=([a-z_]+)", response.url)
                seen.append((match.group(1) if match else None, response.status))

        self.page.on("response", on_response)
        return seen, on_response

    def sort_cpos(self):
        """Sort the CPO columns both ways, and pin the two broken headers.

        Deal Type, Next Maint. and Criticality are "—" for nearly every CPO on
        staging, so a *correct* sort legitimately leaves the visible order
        unchanged -- a "did the rows move?" check would fail on data rather than
        on behaviour. They are therefore asserted on the URL and on the API
        accepting the request (HTTP 200) with the table still populated.

        CPO Name and Sites are covered separately, because they are broken --
        see CPO_SORTS_BROKEN.
        """
        seen, listener = self._watch_cpo_api()
        try:
            for col, param in self.CPO_SORTS.items():
                for direction in ("asc", "desc"):
                    seen.clear()
                    self._header(col).click()
                    self.page.wait_for_url(
                        re.compile(rf"[?&]sort_by={param}&sort_order={direction}"),
                        timeout=10000,
                    )
                    assert self._poll(
                        lambda p=param: any(s == p for s, _ in seen), timeout_ms=15000
                    ), f"sorting by {col!r} {direction} never called the API"
                    statuses = [st for s, st in seen if s == param]
                    assert all(st == 200 for st in statuses), (
                        f"sorting by {col!r} {direction} was rejected by the API: "
                        f"{statuses} (sent sort_by={param})"
                    )
                    assert self._poll(lambda: self._cpo_rows().count() > 0,
                                      timeout_ms=20000), (
                        f"the CPO list is empty after sorting by {col!r} {direction}"
                    )
                log.info("Column %-12s sorts asc+desc (sort_by=%s, HTTP 200)",
                         col, param)

            self._check_broken_sorts(seen)
        finally:
            self.page.remove_listener("response", listener)

        # Drop the sort so the rest of the run sees the default order.
        self.page.goto(self.page.url.split("?")[0])
        assert self._poll(lambda: self._cpo_rows().count() > 0, timeout_ms=25000)

    def _check_broken_sorts(self, seen):
        """Pin the CPO Name / Sites sort defect (see CPO_SORTS_BROKEN).

        Asserts the *current, broken* behaviour on purpose: the header sends the
        wrong sort_by, the API rejects it with 422, and the table is left
        showing whatever it had before. When the frontend is corrected this
        assertion fails -- which is the point. Move the column into CPO_SORTS
        and delete its entry from CPO_SORTS_BROKEN at that stage.
        """
        for col, (sent, expected) in self.CPO_SORTS_BROKEN.items():
            seen.clear()
            self._header(col).click()
            assert self._poll(
                lambda s=sent: any(p == s for p, _ in seen), timeout_ms=15000
            ), f"clicking {col!r} never called the API"
            statuses = [st for p, st in seen if p == sent]
            assert 422 in statuses, (
                f"KNOWN BUG APPEARS FIXED: sorting by {col!r} now returns "
                f"{statuses} instead of 422. Move {col!r} from CPO_SORTS_BROKEN "
                f"into CPO_SORTS and assert it properly."
            )
            log.warning(
                "KNOWN BUG -- the %r header sends sort_by=%r but the API only "
                "accepts %r, so it returns HTTP 422 and the sort silently does "
                "nothing", col, sent, expected,
            )

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
    # CPO drill-down
    # ----------------------------------------------------------------- #
    def open_cpo_detail(self):
        """Drill into a CPO and exercise its sites page, then come back.

        Covers the drill-down URL, the (CPO-less) column set, the deal-type and
        criticality filters, and expanding a row then collapsing every row.
        """
        log.info("Opening the CPO drill-down for %r", CPO)
        self.cpo_search.fill(CPO)
        assert self._poll(lambda: self._cpo_rows().count() == 1), (
            f"expected exactly 1 CPO for {CPO!r}, got {self._cpo_rows().count()}"
        )
        self.page.get_by_role("button", name=CPO, exact=True).click()

        # The drill-down carries the CPO's UUID in the path and its name in the
        # query string.
        self.page.wait_for_url(
            re.compile(r"/operations/operations-hub/[0-9a-f-]{36}"), timeout=15000
        )
        assert "cpo_name=" in self.page.url, (
            f"the drill-down URL does not name the CPO: {self.page.url}"
        )
        assert self._poll(lambda: self._site_rows().count() > 0, timeout_ms=25000), (
            f"{CPO} drill-down never listed any sites"
        )
        log.info("Drill-down open at %s with %s site(s)",
                 self.page.url.split("/")[-1][:40], self._site_rows().count())

        columns = self._columns()
        assert columns == self.CPO_DETAIL_COLUMNS, (
            f"unexpected drill-down column set: {columns} != {self.CPO_DETAIL_COLUMNS}"
        )

        self._filter_sites()
        self._expand_and_collapse()

        log.info("Returning to the Operations Hub list")
        self.page.go_back()
        assert self._poll(lambda: self._cpo_rows().count() > 0, timeout_ms=25000), (
            "did not get back to the CPO list"
        )
        self._clear_search(self.cpo_search)
        self.page.wait_for_timeout(1000)

    def _filter_sites(self):
        """Apply the deal-type and criticality filters on the drill-down.

        Each filter is asserted on its option set, really applied, and then
        toggled back off by re-selecting the same option. Staging's sites mostly
        carry neither a deal type nor a criticality, so a *correct* filter
        legitimately empties this table -- the assertion is therefore that the
        table responds (narrows, or shows its own "No sites found for this CPO"
        empty state), not that it keeps rows the data may not have.
        """
        before = self._site_rows().count()

        for trigger, options, choice, param, name in (
            (self.type_filter, self.DEAL_TYPES, self.DEAL_TYPES[1],
             "deal_type", "Deal type"),
            (self.criticality_filter, self.CRITICALITIES, self.CRITICALITIES[-1],
             "criticality", "Criticality"),
        ):
            column = "Deal Type" if param == "deal_type" else "Criticality"
            self._apply_filter(trigger, options, choice, param, name)

            # The URL updates and the trigger relabels the moment the option is
            # clicked, but the table only catches up when the refetch lands --
            # so poll for the *rows* to agree with the filter. Asserting
            # straight away reads the stale pre-filter rows and fails on timing
            # rather than on behaviour.
            assert self._poll(
                lambda c=column: self._filter_settled(c, choice), timeout_ms=15000
            ), (
                f"the {name} filter {choice!r} left rows that do not match: "
                f"{self._column_values(column, self._site_rows())[:3]}"
            )

            if self._site_rows().count():
                self._assert_column(column, choice, f"{name} {choice!r}",
                                    self._site_rows(), exact=True)
            else:
                expect(
                    self.page.get_by_text("No sites found for this CPO")
                ).to_be_visible()
                log.info("%s %r matches no site here -- empty state shown",
                         name, choice)

            self._clear_filter(choice, param, name)
            assert self._poll(lambda: self._site_rows().count() == before), (
                f"expected {before} site(s) after clearing the {name} filter, "
                f"got {self._site_rows().count()}"
            )

    def _filter_settled(self, column, choice):
        """True once the table agrees with the filter.

        Either the filter matched nothing (an empty table is a valid result
        here -- most staging sites carry no deal type or criticality), or every
        remaining row carries the chosen value in the column the filter drives.
        """
        rows = self._site_rows()
        if rows.count() == 0:
            return True
        return all(v == choice for v in self._column_values(column, rows))

    def _apply_filter(self, trigger, options, choice, param, name):
        """Open `trigger`, check its options, and select `choice`.

        The applied filter is confirmed two ways -- it pushes its own query
        parameter, and the trigger relabels itself to the chosen value -- so a
        popover that closes without doing anything cannot pass.
        """
        self._open_filter(trigger, options, name)
        log.info("Applying the %s filter %r", name, choice)
        self.page.get_by_role("option", name=choice, exact=True).click()
        self.page.wait_for_url(re.compile(rf"[?&]{param}="), timeout=10000)
        expect(self._filter_trigger(choice)).to_be_visible()

    def _clear_filter(self, choice, param, name):
        """Toggle `choice` back off by re-selecting it in its own popover."""
        log.info("Clearing the %s filter", name)
        self._filter_trigger(choice).click()
        self.page.wait_for_timeout(800)
        self.page.get_by_role("option", name=choice, exact=True).click()
        self.page.wait_for_url(
            lambda url: f"{param}=" not in url, timeout=10000
        )

    def _filter_trigger(self, choice):
        """The filter trigger once it has relabelled itself to `choice`.

        The label becomes e.g. "Criticality: High", so the trigger is matched on
        the chosen value rather than on a hard-coded prefix per filter.
        """
        return self.page.get_by_role(
            "button", name=re.compile(rf":\s*{re.escape(choice)}$")
        )

    def _expand_and_collapse(self):
        """Expand a row's detail panel, then collapse every row."""
        base = self.rows.count()
        log.info("Expanding the first site row")
        self.expand_row.first.click()
        assert self._poll(lambda: self.rows.count() == base + 1), (
            f"expanding a row should add a detail row to the {base} present, "
            f"got {self.rows.count()}"
        )
        # The panel spells out the site's assets, organisation and maintenance
        # dates; confirm the labels are all there so an empty panel is caught.
        body = self.page.locator("table tbody").inner_text() or ""
        for label in ("Asset Details", "Charging Devices", "Sockets",
                      "Organisation Details", "Organisation",
                      "Maintenance Dates", "Next Maintenance"):
            assert label in body, f"the expanded panel is missing {label!r}"
        log.info("Expanded panel shows Asset / Organisation / Maintenance details")

        log.info("Collapsing all rows")
        self.collapse_all.click()
        assert self._poll(lambda: self.rows.count() == base), (
            f"expected {base} row(s) after collapsing all, got {self.rows.count()}"
        )

    # ----------------------------------------------------------------- #
    # List View: Sites
    # ----------------------------------------------------------------- #
    def browse_sites_view(self):
        """Switch the hub to Sites mode and exercise it."""
        log.info("Switching the hub to Sites view")
        # Clicked rather than .check()ed: the radio's DOM state only flips once
        # React has re-rendered off the new URL, and .check() asserts the state
        # synchronously right after the click, so it fails on a control that
        # works. The URL and the checked assertion below cover it properly.
        self.sites_mode.click()
        self.page.wait_for_url(re.compile(r"[?&]view=sites"), timeout=10000)
        expect(self.sites_mode).to_be_checked()
        assert self._poll(lambda: self._site_rows().count() > 0, timeout_ms=25000), (
            "the Sites list never loaded"
        )
        # The search box is relabelled for sites -- proof the mode really changed.
        expect(self.site_search).to_be_visible()

        columns = self._columns()
        log.info("Sites table columns: %s", columns)
        assert columns == self.SITE_COLUMNS, (
            f"unexpected Sites column set: {columns} != {self.SITE_COLUMNS}"
        )
        log.info("Sites view shows %s site(s)", self._site_rows().count())

        self._search_sites()
        self._sort_sites()
        self._expand_site_row()

    def _search_sites(self):
        before = self._site_rows().count()

        log.info("Searching sites for %r", SEARCH_TERM)
        self.site_search.fill(SEARCH_TERM)
        assert self._poll(lambda: 0 < self._site_rows().count() < before), (
            f"the site search should narrow the list from {before}, "
            f"got {self._site_rows().count()}"
        )
        self._assert_column("Site Name", SEARCH_TERM, f"site search {SEARCH_TERM!r}",
                            self._site_rows())

        log.info("Searching sites for a term that matches nothing")
        self.site_search.fill("zzzz-no-such-site")
        assert self._poll(lambda: self._site_rows().count() == 0), (
            f"expected no sites, got {self._site_rows().count()}"
        )
        expect(
            self.page.get_by_text("No sites found matching your criteria")
        ).to_be_visible()
        log.info("Site empty state shown")

        self._clear_search(self.site_search)
        assert self._poll(lambda: self._site_rows().count() == before), (
            f"expected {before} site(s) after clearing the search, "
            f"got {self._site_rows().count()}"
        )

    def _sort_sites(self):
        """Sort the Sites table, asserting through the URL as for CPOs."""
        for col, param in self.SITE_SORTS.items():
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort_by={param}&sort_order=asc"), timeout=10000
            )
            assert self._poll(lambda: self._site_rows().count() > 0), (
                f"the site list is empty after sorting by {col!r}"
            )
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort_by={param}&sort_order=desc"), timeout=10000
            )
            assert self._poll(lambda: self._site_rows().count() > 0), (
                f"the site list is empty after reversing the {col!r} sort"
            )
            log.info("Sites column %-12s sorts asc+desc (sort_by=%s)", col, param)

    def _expand_site_row(self):
        """Expand a site row, read its panel, then collapse it again.

        The Sites view has no "Collapse all rows" header control -- the row's
        own toggle relabels from "Expand row" to "Collapse row" -- so this
        closes the panel the same way a user would.
        """
        base = self.rows.count()
        log.info("Expanding the first site row in the Sites view")
        self.expand_row.first.click()
        assert self._poll(lambda: self.rows.count() == base + 1), (
            f"expanding a row should add a detail row to the {base} present, "
            f"got {self.rows.count()}"
        )
        expect(self.collapse_row).to_have_count(1)

        body = self.page.locator("table tbody").inner_text() or ""
        for label in ("Asset Details", "Primary Device", "Organisation Details",
                      "Country", "Maintenance Dates", "Live Date"):
            assert label in body, f"the expanded panel is missing {label!r}"
        log.info("Expanded panel shows the site's assets, organisation and dates")

        log.info("Collapsing the row again")
        self.collapse_row.click()
        assert self._poll(lambda: self.rows.count() == base), (
            f"expected {base} row(s) after collapsing, got {self.rows.count()}"
        )

    # ----------------------------------------------------------------- #
    # Site detail
    # ----------------------------------------------------------------- #
    def open_site_detail(self):
        """Open a site's own page and walk all four of its tabs in depth.

        Pinned to `SITE` rather than "whichever row is first", because the
        Maintenance walk needs a site that actually has plans -- see the note on
        the constant.

        Read-only throughout: the Maintenance tab's Add Events / Create
        Maintenance Plan / Edit Plan dialogs are opened and validated, but every
        one of them is cancelled, and the Revert / Schedule Event / Upload
        controls are left alone.
        """
        log.info("Opening the detail page for %r", SITE)
        self.site_search.fill(SITE)
        # Poll until the rows agree with the search rather than just until some
        # rows exist -- the previous, unfiltered list is still on screen while
        # the search refetches, and would otherwise be asserted against.
        assert self._poll(
            lambda: self._rows_match("Site Name", SITE, self._site_rows()),
            timeout_ms=15000,
        ), (
            f"the site search never settled on {SITE!r}: "
            f"{self._column_values('Site Name', self._site_rows())[:3]}"
        )
        self._assert_column("Site Name", SITE, f"site search {SITE!r}",
                            self._site_rows())
        self.view_details.first.click()

        # /operations-hub/<cpo-uuid>/<site-uuid>
        self.page.wait_for_url(
            re.compile(r"/operations/operations-hub/[0-9a-f-]{36}/[0-9a-f-]{36}"),
            timeout=20000,
        )
        # The header states the site, its CPO, its external ID and its device count.
        for label in ("Site :", "CPO :", "External ID :", "No. of Charging Devices :"):
            expect(self.page.get_by_text(label, exact=False).first).to_be_visible()
        expect(self.page.get_by_text(SITE).first).to_be_visible()
        log.info("Site detail open for %s", SITE)

        self._check_breadcrumb()
        self._browse_site_info()
        self._browse_tracker()
        self._browse_maintenance()
        self._browse_records()
        self._leave_site_detail()

    def _check_breadcrumb(self):
        """The trail names the hub, the CPO and the site, and the hub is clickable."""
        expect(self.breadcrumb_hub).to_be_visible()
        body = self.page.locator("body").inner_text() or ""
        assert SITE in body, "the breadcrumb does not name the site"
        log.info("Breadcrumb shows Operations Hub > CPO > %s", SITE)

    def _tab(self, name):
        return self.page.get_by_role("button", name=name, exact=True).first

    # -- Site Info ----------------------------------------------------- #
    def _browse_site_info(self):
        self._tab("Site Info").click()
        self.page.wait_for_timeout(2500)
        body = self.page.locator("body").inner_text() or ""
        for label in ("Address :", "City :", "Postal Code :", "Contact Person :",
                      "Contact No :", "Latitude :", "Longitude :"):
            assert label in body, f"the Site Info tab is missing {label!r}"
        # The site's hardware is listed underneath its location details.
        for label in ("Charging Device", "Model :", "Status :", "Socket"):
            assert label in body, f"the Site Info tab is missing {label!r}"
        log.info("Site Info tab shows the location, contact and hardware details")

    # -- Tracker ------------------------------------------------------- #
    def _browse_tracker(self):
        self._tab("Tracker").click()
        self.page.wait_for_timeout(2500)
        body = self.page.locator("body").inner_text() or ""
        # A site either has a workflow attached or says plainly that it has none.
        assert "Workflow" in body, "the Tracker tab renders no workflow state"
        if "No Workflow Attached" in body:
            log.info("Tracker tab: no workflow attached to this site")
        else:
            log.info("Tracker tab: a workflow is attached")

    # -- Maintenance --------------------------------------------------- #
    def _browse_maintenance(self):
        """Walk the Maintenance tab: its sub-tabs, plan cards and dialogs."""
        self._tab("Maintenance").click()
        self.page.wait_for_timeout(3000)
        expect(self.add_events).to_be_visible()
        expect(self.create_plan).to_be_visible()
        log.info("Maintenance tab open")

        self._maintenance_subtabs()
        self._maintenance_plan_card()
        self._create_or_reuse_plan()
        self._edit_plan_round_trip()
        self._create_or_reuse_event()

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

    # -- Create (once) / reuse the automation plan --------------------- #
    def _create_or_reuse_plan(self):
        """Create the suite's own maintenance plan, or reuse it if it exists.

        A plan cannot be deleted through the UI, so this creates one only when
        it is missing -- see the note on PLAN_TITLE. The validation rules are
        asserted on the way through, so the run still proves the form guards
        itself even when it takes the reuse path.
        """
        if self._plan_exists(PLAN_TITLE):
            log.info("Reusing the existing %r plan (plans cannot be deleted, "
                     "so the suite keeps just the one)", PLAN_TITLE)
            expect(self._plan_card(PLAN_TITLE).get_by_role(
                "button", name="Edit Plan")).to_be_visible()
            return

        log.info("No %r plan yet -- creating it", PLAN_TITLE)
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
        log.info("Submitting the new maintenance plan")
        submit.click()

        # Step 2 confirms the write and offers an optional attachment.
        expect(self.dialog.first).to_contain_text(
            "Plan created successfully", timeout=20000
        )
        log.info("Plan created; skipping the optional attachment step")
        self.dialog.first.get_by_role("button", name="Skip", exact=True).click()
        assert self._poll(lambda: self.dialog.count() == 0, timeout_ms=15000), (
            "the Create Plan wizard did not close after Skip"
        )

        assert self._poll(lambda: self._plan_exists(PLAN_TITLE), timeout_ms=20000), (
            f"the new {PLAN_TITLE!r} plan is not listed after creating it"
        )
        log.info("New plan %r is listed", PLAN_TITLE)

    # -- Edit the automation plan and put it back ---------------------- #
    def _edit_plan_round_trip(self):
        """Edit the suite's plan, prove the change saved, then restore it.

        Written as a round trip on purpose -- the same shape as the device
        ownership test. The description is changed and saved, the card is
        checked for the new text, and then it is set back, so the plan always
        ends the run on PLAN_DESCRIPTION no matter how many times this runs.
        """
        log.info("Editing the %r plan", PLAN_TITLE)
        self._open_plan_editor()
        dlg = self.dialog.first

        # Guard: the dialog must belong to our plan, not another card's.
        title = dlg.locator("input[placeholder*='plan']").first.input_value()
        assert title.strip() == PLAN_TITLE, (
            f"refusing to save: the editor is for {title!r}, not {PLAN_TITLE!r}"
        )
        text = dlg.inner_text() or ""
        for marker in ("Edit Maintenance Plan", "Start Date", "Status",
                       "Category", "Frequency", "Criticality", "Attachments"):
            assert marker in text, f"the Edit Plan dialog is missing {marker!r}"
        assert "ACTIVE" in text, "the plan should be ACTIVE"

        try:
            self._save_description(dlg, PLAN_DESCRIPTION_EDITED)
            assert self._poll(
                lambda: PLAN_DESCRIPTION_EDITED in
                (self._plan_card(PLAN_TITLE).inner_text() or ""),
                timeout_ms=20000,
            ), "the edited description is not shown on the plan card"
            log.info("Edit saved -- the plan card shows the new description")
        finally:
            # Always restore, even if the assertion above failed, so a broken
            # run never leaves the plan on the edited text.
            log.info("Restoring the plan's original description")
            self._open_plan_editor()
            self._save_description(self.dialog.first, PLAN_DESCRIPTION)

        assert self._poll(
            lambda: PLAN_DESCRIPTION in
            (self._plan_card(PLAN_TITLE).inner_text() or ""),
            timeout_ms=20000,
        ), f"FAILED TO RESTORE the description on {PLAN_TITLE!r}"
        log.info("Plan %r restored to its original description", PLAN_TITLE)

    def _open_plan_editor(self):
        """Open Edit Plan on the suite's own card."""
        self._plan_card(PLAN_TITLE).get_by_role(
            "button", name="Edit Plan"
        ).first.click()
        self.dialog.last.wait_for(state="visible", timeout=15000)

    def _save_description(self, dlg, description):
        dlg.locator("textarea").first.fill(description)
        save = dlg.get_by_role("button", name="Save Changes")
        expect(save).to_be_enabled()
        save.click()
        assert self._poll(lambda: self.dialog.count() == 0, timeout_ms=20000), (
            "the Edit Plan dialog did not close after saving"
        )

    # -- Create (once) / reuse the automation event -------------------- #
    def _create_or_reuse_event(self):
        """Create the suite's own maintenance event, or reuse it if it exists.

        Events cannot be deleted either, so this follows the same
        create-once-then-reuse rule as the plan.
        """
        if self._event_exists():
            log.info("Reusing the existing %r event", EVENT_TITLE)
            return

        log.info("No %r event yet -- creating it", EVENT_TITLE)
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

        # Scheduled for tomorrow: the API refuses a start time in the past, so
        # a fixed hour today would fail for most of the day.
        tomorrow = self._tomorrow()
        self._pick_date(dlg, tomorrow)
        # Start and end time are both mandatory; an hour apart.
        self._pick_datetime(dlg, hour="9", day=tomorrow)
        self._pick_datetime(dlg, hour="10", day=tomorrow)
        dlg.locator("textarea").first.fill(
            "Created by the automated regression suite. Reused on every run."
        )

        expect(submit, "Create Event should enable once the form is complete"
               ).to_be_enabled()
        log.info("Submitting the new maintenance event")
        submit.click()
        # A rejected submit leaves the dialog open with the reason on it, so the
        # failure message carries that rather than just "it did not close".
        assert self._poll(lambda: self.dialog.count() == 0, timeout_ms=25000), (
            "the Create Event dialog did not close after submitting -- the save "
            f"was rejected: {(self.dialog.first.inner_text() or '')[:300]!r}"
        )

        assert self._poll(self._event_exists, timeout_ms=25000), (
            f"the new {EVENT_TITLE!r} event is not listed after creating it"
        )
        log.info("New event %r is listed", EVENT_TITLE)

    def _event_exists(self):
        """True when the suite's event is listed under any events sub-tab.

        A created event moves between Upcoming / Scheduled / Completed as its
        due date passes, so every sub-tab is checked rather than assuming where
        it lands.
        """
        for name in ("Upcoming Events", "Scheduled", "Completed"):
            self._maintenance_tab(name).click()
            self.page.wait_for_timeout(1800)
            if EVENT_TITLE in (self.page.locator("body").inner_text() or ""):
                log.info("Event %r found under %r", EVENT_TITLE, name)
                return True
        return False

    # -- Records ------------------------------------------------------- #
    def _browse_records(self):
        """Step through every document category on the Records tab."""
        self._tab("Records").click()
        self.page.wait_for_timeout(2500)
        for category in self.RECORD_CATEGORIES:
            tab = self.page.get_by_role("button", name=category, exact=True)
            assert tab.count(), f"the Records tab has no {category!r} category"
            tab.first.click()
            self.page.wait_for_timeout(1200)
            body = self.page.locator("body").inner_text() or ""
            # The panel heading follows the selected category.
            assert f"{category} Documents" in body, (
                f"selecting {category!r} did not open its document panel"
            )
        log.info("Records tab steps through all %s categories",
                 len(self.RECORD_CATEGORIES))
        # The Upload control is present but deliberately not used.
        expect(self.page.get_by_role("button", name="Upload").first).to_be_visible()

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

    # ----------------------------------------------------------------- #
    # Map View
    # ----------------------------------------------------------------- #
    def browse_map(self):
        """Switch to the Map View and exercise its filters, legend and zoom."""
        log.info("Switching to the Map View")
        self.map_view.click()
        self.page.wait_for_url(re.compile(r"/operations/operations-hub/map"), timeout=15000)
        self.map_canvas.first.wait_for(state="visible", timeout=25000)
        log.info("Map rendered")

        # The legend explains the marker colours; every threshold must be named.
        body = self.page.locator("body").inner_text() or ""
        for state in ("Overdue", "In Progress", "Scheduled", "Upcoming",
                      "Completed", "No Maintenance"):
            assert state in body, f"the map legend is missing {state!r}"
        log.info("Map legend lists all six maintenance states")

        # Markers: one card per CPO, each naming its ID and site count. They are
        # drawn a few seconds after the map container itself appears, so this
        # polls rather than reading the count the instant the canvas is visible
        # -- checking immediately is a race that fails on a working map.
        markers = self.page.get_by_role("button", name=re.compile(r"ID:\s*\d+"))
        assert self._poll(lambda: markers.count() > 0, timeout_ms=30000), (
            "the map shows no CPO markers"
        )
        log.info("Map shows %s CPO marker(s)", markers.count())

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
        self.page.wait_for_url(
            re.compile(r"/operations/operations-hub(?!/map)"), timeout=15000
        )
        assert self._poll(lambda: self.table.count() > 0, timeout_ms=25000), (
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
        self.open_cpo_detail()
        self.browse_sites_view()
        self.open_site_detail()
        self.browse_map()
        log.info("Operations Hub workflow completed")
