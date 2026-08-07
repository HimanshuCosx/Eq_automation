import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.maintenance")

# Staging keeps two fixtures named for automation -- an event and a plan, both
# spelled "do not modify". Every check that needs a *named* record is pinned to
# them rather than to ordinary operational data: the real events are created,
# scheduled and completed by the team as they work, so a run pinned to one of
# those would start failing the day somebody completed it.
SEARCH_TERM = "AUTOMATION"
EVENT = "AUTOMATION - do not modify"
EVENT_SITE = "345 Woodbridge Road CO OP"
EVENT_CPO = "East of England CO OP"
PLAN = "AUTOMATION - do not modify"

# The values the three filter checks are pinned to, with the API value each
# sends. Preventive is the largest category and O&M with Install the smallest
# deal type, so between them they prove a filter that widens and one that
# collapses the table.
CATEGORY = "Preventive"
CATEGORY_PARAM = "PREVENTIVE"
STATUS = "Completed"
STATUS_PARAM = "COMPLETED"
SECOND_STATUS = "In Progress"
SECOND_STATUS_PARAM = "IN_PROGRESS"
DEAL_TYPE = "O&M with Install"
DEAL_TYPE_PARAM = "OPERATED_AND_MAINTAINED_WITH_INSTALL"

NO_MATCH = "zzzz-no-such-event"


class maintenance:
    """Maintenance (/operations/maintenance).

    The planned-work view of the estate: four headline tiles that double as
    filters, a dual All events / All plans switch, three multi-select filters,
    a searchable and sortable event table whose rows carry the whole event
    lifecycle (schedule it, edit it, complete it, delete it), a month/week/day
    calendar of the same events, and a card list of the recurring plans that
    generate them.

    The workflow is deliberately read-only, and on this page that matters more
    than on most. Every row carries a **Delete** control, and the page also
    offers Schedule, Mark complete, Edit event, Edit plan, Pause plan and
    Cancel plan -- all of them one-way from this UI. Completing an event in
    particular is irreversible: it writes a site visit and a signed-off
    checklist against a real site, which is what the finance side later bills
    from.

    So no write is ever submitted. Delete is **never clicked at all** -- it is
    asserted present and left alone. The rest are opened and *validated*: each
    dialog is checked on the fields it offers, the values it pre-fills and the
    submit it guards, then dismissed with Cancel or Escape. The run leaves
    staging exactly as it found it.

    Two checks live outside `maintenance_page` -- `sort_columns_reorder` and
    `plans_tab_pagination` -- purely because each leaves the page in a state
    the rest of the workflow would have to unwind. Both pass.
    """

    # ------------------------------------------------------------------ #
    # Events tab
    # ------------------------------------------------------------------ #

    COLUMNS = ["Event", "Organisation", "Deal Type", "Category", "Assignee",
               "Due Date", "Status", "Actions"]

    # Sortable columns and the `sort_by` value each sends.
    SORTABLE = {
        "Event": "title",
        "Due Date": "due_date",
        "Status": "status",
    }
    NOT_SORTABLE = ["Organisation", "Deal Type", "Category", "Assignee",
                    "Actions"]

    # The four headline tiles, in render order: the aria-label each exposes,
    # the query parameter clicking it sets, and a phrase its tooltip must
    # carry. The tooltips are the only place the page explains what each bucket
    # actually counts, so they are worth pinning.
    TILES = {
        "Overdue": ("is_overdue=true", "due date has passed"),
        "Due this week": ("due_within_days=7", "next seven days"),
        "In progress": ("status=IN_PROGRESS", "started but not yet completed"),
        "Completed": ("status=COMPLETED", "logged site visit"),
    }

    # The legend under the filter bar.
    LEGEND = ["Overdue", "Upcoming", "Scheduled", "In progress", "Completed"]

    # Filter options, exactly as each dropdown lists them.
    STATUSES = ["Upcoming", "Scheduled", "In Progress", "Completed", "Cancelled"]
    CATEGORIES = ["Preventive", "Corrective", "Inspection"]
    DEAL_TYPES = ["O&M with Install", "O&M Onboarding", "Fully Funded Install"]

    # The statuses a row can actually render. Note "In Progress" is spelled
    # with a capital P in the filter list but a lower-case p in the table, so
    # the two are deliberately not compared to one another.
    ROW_STATUSES = ["Overdue", "Upcoming", "Scheduled", "In progress",
                    "Completed", "Cancelled"]

    PAGE_SIZES = ["10", "20", "50", "100"]

    # ------------------------------------------------------------------ #
    # Calendar
    # ------------------------------------------------------------------ #

    CALENDAR_VIEWS = ["Month", "Week", "Day"]
    WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    MONTH = re.compile(
        r"^(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{4}$"
    )

    # ------------------------------------------------------------------ #
    # Dialogs
    # ------------------------------------------------------------------ #

    # The Add event / Edit event panel.
    EVENT_FORM_FIELDS = ["Site", "Event name", "Category", "Assignee",
                         "Due date", "Notes"]
    # The plan editor.
    PLAN_FORM_FIELDS = ["Start Date :", "Status :", "Plan Title :",
                        "Category :", "Frequency :", "Criticality :",
                        "End Date (Optional) :", "Description :",
                        "Attachments :"]
    # The Schedule dialog.
    SCHEDULE_FIELDS = ["Maintenance Plan Title :", "Maintenance Type :",
                       "Scheduled Date:", "Assign to :", "Start Time :",
                       "End Time :", "Note :"]
    # The per-plan overflow menu.
    PLAN_MENU = ["Pause plan", "View events", "Cancel plan"]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.nav_link = page.get_by_role("link", name="Maintenance")
        self.breadcrumb = page.locator("nav[aria-label='Breadcrumb']")

        # Search. The two tabs carry different search boxes, so each is held
        # separately -- matching loosely would find whichever was mounted.
        self.search = page.get_by_placeholder(re.compile(r"^Search events"))
        self.plan_search = page.get_by_placeholder(re.compile(r"^Search plans"))

        # Table. Rows are matched on *not* carrying a colspan cell: the empty
        # state renders as a single full-width td, so counting `tbody > tr`
        # alone would report a row where there is no event.
        self.table = page.locator("table").first
        self.rows = self.table.locator("tbody > tr:not(:has(td[colspan]))")

        # Tab switch
        self.events_tab = page.get_by_role("button", name=re.compile(r"^All events"))
        self.plans_tab = page.get_by_role("button", name=re.compile(r"^All plans"))

        # Filters. Each is a multi-select popover that only commits on Apply --
        # see `_apply_filter`.
        self.status_filter = page.get_by_role("button", name=re.compile(r"^Status\b"))
        self.category_filter = page.get_by_role(
            "button", name=re.compile(r"^Category\b")
        )
        self.deal_type_filter = page.get_by_role(
            "button", name=re.compile(r"^Deal Type\b")
        )
        self.apply_filter = page.get_by_role("button", name="Apply", exact=True)
        self.clear_filter = page.get_by_role("button", name="Clear", exact=True)
        self.clear_filters = page.get_by_role("button", name="Clear filters")

        # View switch
        self.list_view = page.get_by_role("button", name="List", exact=True)
        self.calendar_view = page.get_by_role("button", name="Calendar", exact=True)
        self.prev_period = page.get_by_role("button", name="Previous")
        self.next_period = page.get_by_role("button", name="Next")
        self.month_title = page.get_by_text(self.MONTH)

        # Empty states
        self.no_events = page.get_by_text("No events found", exact=True)
        self.no_plans = page.get_by_text("No plans found", exact=True)

        # Footer / pagination
        self.showing = page.get_by_text(re.compile(r"^Showing "))
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$"), exact=True
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Write controls. Delete is held only so it can be asserted present --
        # it is never clicked, anywhere in this file.
        self.add_event = page.get_by_role("button", name="Add event", exact=True)
        self.delete_row = page.get_by_role("button", name="Delete")
        self.edit_event = page.get_by_role("button", name="Edit event")
        self.schedule = page.get_by_role("button", name="Schedule")
        self.mark_complete = page.get_by_role("button", name="Mark complete")
        self.edit_plan = page.get_by_role("button", name="Edit plan")

        self.dialog = page.get_by_role("dialog")
        self.cancel = page.get_by_role("button", name="Cancel", exact=True)

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _poll(self, predicate, timeout_ms=20000, interval_ms=250):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        Every tile, filter, search, sort and page change refetches the table,
        so state is polled until it settles rather than raced with a fixed
        sleep. The deadline is wall-clock so an expensive predicate cannot
        overrun it.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _park_mouse(self):
        """Move the pointer off the sidebar and let it collapse.

        The sidebar is fixed to the left edge and widens while hovered,
        covering the table's leading column -- which here is the event name. A
        click there is then intercepted by the nav and retries until it times
        out.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] - 40, size["height"] - 60)
        self.page.wait_for_timeout(700)

    def _dismiss(self):
        """Close whatever popover, menu or panel is open."""
        self.page.keyboard.press("Escape")
        self._poll(
            lambda: self.page.get_by_role("option").count() == 0
            and self.dialog.count() == 0,
            timeout_ms=6000,
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
        """The event name in each row, without the site line beneath it."""
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

    def _settled_names_column(self, index):
        """Column `index` once it has stopped changing."""
        previous = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = self._column(index)
            if current and current == previous:
                return current
            previous = current
            self.page.wait_for_timeout(400)
        return self._column(index)

    def _cells_snapshot(self):
        """Every row's full cell list, read atomically.

        Same hazard as `_rows_snapshot`, and the same fix: the table
        re-renders as a refetch lands, invalidating locators handed out a
        moment before, so the whole read is retried rather than patched up
        half-done.
        """
        for _ in range(6):
            try:
                return [
                    [(c.inner_text() or "").strip() for c in row.locator("td").all()]
                    for row in self.rows.all()
                ]
            except Exception:
                self.page.wait_for_timeout(700)
        raise AssertionError("the event table would not hold still to be read")

    def _rows_snapshot(self):
        """Every row's status and action set, read atomically.

        The table re-renders whenever a refetch lands, which invalidates the
        locators `rows.all()` handed out a moment earlier -- reading one of
        them then raises rather than returning stale text. Retrying the whole
        snapshot is the only safe way round it: a partial read would pair one
        row's status with another's actions.
        """
        for _ in range(6):
            try:
                return [
                    (
                        (row.locator("td").nth(0).inner_text() or "").split("\n")[0],
                        (row.locator("td").nth(6).inner_text() or "").strip(),
                        self._row_actions(row),
                    )
                    for row in self.rows.all()
                ]
            except Exception:
                self.page.wait_for_timeout(700)
        raise AssertionError("the event table would not hold still to be read")

    def _plan_names(self):
        """Every plan card's name, read atomically.

        Same hazard as `_rows_snapshot`: the card list re-renders on every
        search and page change, which invalidates locators handed out a moment
        earlier.
        """
        cards = self.page.get_by_role(
            "button", name=re.compile(r"^More actions for")
        )
        for _ in range(6):
            try:
                return [
                    (c.get_attribute("aria-label") or "").replace(
                        "More actions for ", ""
                    )
                    for c in cards.all()
                ]
            except Exception:
                self.page.wait_for_timeout(700)
        return []

    def _loaded(self):
        return self.rows.count() > 0 and all(self._names())

    def _header(self, col):
        """A header cell by column name, located by position.

        Positional rather than by text: "Status" also names a filter and the
        legend, and "Deal Type" names a filter too, so a text match would
        resolve to several elements. The column set is pinned by
        `check_table_structure`, so the index is safe.
        """
        return self.table.locator("thead th").nth(self.COLUMNS.index(col))

    def _footer(self):
        try:
            return (self.showing.first.inner_text() or "").strip()
        except Exception:
            return ""

    def _total(self):
        """However many records the footer says exist, filtered or not."""
        found = re.search(r"of (\d+)", self._footer())
        return int(found.group(1)) if found else 0

    def _pages(self):
        return [
            (b.get_attribute("aria-label") or "").replace("Go to page ", "")
            for b in self.page.get_by_role("button").all()
            if (b.get_attribute("aria-label") or "").startswith("Go to page")
        ]

    def _tile(self, label):
        return self.page.get_by_role("button", name=f"Filter by {label}", exact=True)

    def _tile_count(self, label):
        """The number a tile is currently showing."""
        text = (self._tile(label).inner_text() or "")
        found = re.search(r"\n(\d+)\n", text)
        return int(found.group(1)) if found else None

    def _search_for(self, box, term, min_rows=None):
        """Type into a search box and wait for the list to answer it.

        The box is a controlled React input behind a debounce. A value typed
        while the list is still settling from a tab or route change can be
        dropped before its onChange is attached -- the box then reads empty, no
        request is made, and the wait that follows times out against a list
        that was never asked to change. So the value is checked back.
        """
        box.fill(term)
        assert self._poll(
            lambda: (box.input_value() or "") == term, timeout_ms=8000
        ), f"the search box would not accept {term!r}"
        if term:
            self.page.wait_for_url(re.compile(r"[?&]search="), timeout=20000)
        else:
            assert self._poll(
                lambda: "search=" not in self.page.url, timeout_ms=25000
            ), f"the search is still in the URL after clearing: {self.page.url}"
        if min_rows is not None:
            assert self._poll(
                lambda: self.rows.count() >= min_rows, timeout_ms=30000
            ), (
                f"searching for {term!r} returned {self.rows.count()} row(s), "
                f"expected at least {min_rows}"
            )

    def _await_matches(self, term):
        """Wait until every row on screen answers `term`.

        The URL gains `search=` the moment the box is filled, well before the
        refetch it triggered comes back, so the rows visible at that point are
        still the previous result. Reading them straight away compares the new
        query against the old table and fails on a race rather than on a fault.

        The box searches events, sites *and* CPOs -- its own placeholder says so
        -- so a row legitimately matches on any of those, and the whole row is
        checked rather than only the event name.
        """
        def _rows_answer():
            texts = [
                (r.inner_text() or "").lower() for r in self.rows.all()
            ]
            return bool(texts) and all(term.lower() in t for t in texts)

        assert self._poll(_rows_answer, timeout_ms=30000), (
            f"searching for {term!r} returned row(s) that do not mention it "
            "anywhere -- event, site or CPO: "
            + str([
                (r.locator("td").nth(0).inner_text() or "").split("\n")[0]
                for r in self.rows.all()
                if term.lower() not in (r.inner_text() or "").lower()
            ])
        )

    def _apply_filter(self, control, option, param, label):
        """Tick one option in a multi-select filter and commit it.

        Every filter here is a checkbox list that does *nothing* until Apply is
        pressed -- ticking an option alone leaves the button reading "All
        ..." and never touches the URL. Anything that clicks the option and
        then reads the table is testing the wrong thing, so Apply is part of
        the helper rather than left to each caller.
        """
        self._park_mouse()
        control.first.click()
        assert self._poll(
            lambda: self.page.get_by_role("option").count() > 0, timeout_ms=10000
        ), f"the {label} filter opened with no options"
        self.page.get_by_role("option", name=option, exact=True).click()
        expect(self.apply_filter.first).to_be_visible()
        self.apply_filter.first.click()
        assert self._poll(
            lambda: param in self.page.url, timeout_ms=25000
        ), (
            f"applying {option!r} on the {label} filter did not reach the URL: "
            f"{self.page.url}"
        )
        self._dismiss()

    def _clear_filter(self, control, param, label):
        """Drop a filter with the popover's own Clear control."""
        self._park_mouse()
        control.first.click()
        assert self._poll(
            lambda: self.page.get_by_role("option").count() > 0, timeout_ms=10000
        ), f"the {label} filter would not reopen"
        expect(self.clear_filter.first).to_be_visible()
        self.clear_filter.first.click()
        assert self._poll(
            lambda: param not in self.page.url, timeout_ms=25000
        ), f"the {label} filter is still in the URL after clearing: {self.page.url}"
        self._dismiss()

    def _close_panel(self, title, name):
        """Dismiss a side panel or dialog and prove it went.

        Escape first because every panel here honours it; Cancel as a fallback
        for the ones that also carry a confirm-on-discard step. Never Save.
        """
        self.page.keyboard.press("Escape")
        if self._poll(lambda: title.count() == 0, timeout_ms=8000):
            return
        for label in ("Cancel", "Close"):
            control = self.page.get_by_role("button", name=label, exact=True)
            if control.count():
                control.first.click()
                break
        assert self._poll(lambda: title.count() == 0, timeout_ms=15000), (
            f"the {name} panel would not close"
        )

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        """Reach the page through the sidebar rather than a direct goto.

        A full page load on staging costs upwards of fifteen seconds because
        the whole SPA re-bootstraps and re-authenticates; the client-side route
        change is near-instant and exercises the nav link at the same time.

        If the session is already on Maintenance it is routed away and back
        first. The tab, tiles, filters and view are held in component state as
        well as the URL, so clicking the sidebar link while already here is a
        no-op that would carry a previous test's state into this one -- the
        `page` fixture is shared across the whole session (see conftest.py).
        """
        # Nothing can be clicked underneath an open panel. A check that failed
        # part-way through one would otherwise strand the shared session behind
        # it and take every later test down with it.
        if self.dialog.count():
            log.info("Dismissing a dialog left open on the page")
            self._dismiss()

        if "/operations/maintenance" in self.page.url:
            log.info("Already on Maintenance -- routing away to reset the tab, "
                     "tiles, filters and view")
            self._park_mouse()
            self.page.get_by_role("link", name="Network Status").first.click()
            self.page.wait_for_url(
                re.compile(r"/operations/network-status"), timeout=30000
            )
            self.page.wait_for_timeout(1000)
            self._park_mouse()

        log.info("Opening Maintenance")
        self.nav_link.first.click()
        self.page.wait_for_url(
            re.compile(r"/operations/maintenance"), timeout=30000
        )
        assert self._poll(self._loaded, timeout_ms=45000), (
            "the maintenance event table never loaded"
        )
        self._park_mouse()
        log.info("Maintenance loaded with %s event row(s)", self.rows.count())

        crumbs = [
            c.strip()
            for c in (self.breadcrumb.first.inner_text() or "").split("\n")
            if c.strip()
        ]
        assert crumbs == ["Operations", "Maintenance"], (
            f"unexpected breadcrumb: {crumbs}"
        )
        expect(self.add_event).to_be_visible()

    # ----------------------------------------------------------------- #
    # Tiles
    # ----------------------------------------------------------------- #
    def check_tiles(self):
        """All four tiles: their counts, their tooltips, and their filtering.

        Each tile is both a headline figure and a filter, so each is checked on
        both jobs: the number it shows must match the number of events the
        table then returns, which is the one assertion that catches a tile
        counting a different query from the one it applies.
        """
        for index, (label, (param, phrase)) in enumerate(self.TILES.items()):
            tile = self._tile(label)
            expect(tile).to_be_visible()
            headline = self._tile_count(label)
            assert headline is not None, (
                f"the {label!r} tile shows no count: "
                f"{(tile.inner_text() or '')!r}"
            )
            assert "maintenance events" in (tile.inner_text() or ""), (
                f"the {label!r} tile does not say what it is counting"
            )

            # Its tooltip explains what the bucket means.
            self.page.get_by_role("button", name="More information").nth(
                index
            ).hover()
            assert self._poll(
                lambda p=phrase: p in (
                    self.page.locator("[data-radix-popper-content-wrapper]")
                    .last.inner_text() or ""
                    if self.page.locator(
                        "[data-radix-popper-content-wrapper]"
                    ).count() else ""
                ),
                timeout_ms=10000,
            ), (
                f"the {label!r} tooltip does not explain the bucket "
                f"(expected it to mention {phrase!r})"
            )
            self._park_mouse()

            # Clicking it filters the table to exactly the events it counted.
            log.info("Filtering by the %r tile (%s event(s))", label, headline)
            tile.click()
            assert self._poll(
                lambda p=param: p in self.page.url, timeout_ms=25000
            ), (
                f"clicking the {label!r} tile did not set {param} in the URL: "
                f"{self.page.url}"
            )
            assert self._poll(
                lambda: self.showing.count() > 0, timeout_ms=25000
            ), f"the table never answered the {label!r} tile"
            assert tile.get_attribute("aria-pressed") == "true", (
                f"the {label!r} tile does not mark itself pressed once applied"
            )
            assert self._poll(
                lambda h=headline: self._total() == h, timeout_ms=25000
            ), (
                f"the {label!r} tile counts {headline} event(s) but filtering "
                f"on it returns {self._total()} -- the tile and the table are "
                "answering different queries"
            )
            log.info("Tile %-14s -> %s event(s), matching its headline",
                     label, self._total())

            # And clicking it again releases it.
            self._park_mouse()
            tile.click()
            assert self._poll(
                lambda p=param: p not in self.page.url, timeout_ms=25000
            ), f"the {label!r} tile would not toggle back off: {self.page.url}"
            assert self._poll(self._loaded, timeout_ms=25000), (
                f"the table did not come back after releasing the {label!r} tile"
            )
            assert tile.get_attribute("aria-pressed") == "false", (
                f"the {label!r} tile still marks itself pressed once released"
            )

    # ----------------------------------------------------------------- #
    # Table structure
    # ----------------------------------------------------------------- #
    def check_table_structure(self):
        """The table renders its full column set and well-formed event rows."""
        headers = [
            (h.inner_text() or "").strip()
            for h in self.table.locator("thead th").all()
        ]
        log.info("Table columns: %s", headers)
        assert headers == self.COLUMNS, (
            f"unexpected column set: {headers} != {self.COLUMNS}"
        )

        # The status legend names every state a row can be in.
        legend = self.page.get_by_text("Status:", exact=True)
        expect(legend.first).to_be_visible()
        for state in self.LEGEND:
            assert self.page.get_by_text(state, exact=True).count() > 0, (
                f"the status legend does not list {state!r}"
            )

        for cells in self._cells_snapshot():
            event, org, deal_type, category, assignee, due, status = cells[:7]
            name = event.split("\n")[0]

            assert name, "a row has no event name"
            # The event cell carries the site beneath the event name -- that is
            # what makes the row actionable, since the site cannot be changed
            # once an event exists.
            assert len(event.split("\n")) > 1 and event.split("\n")[1].strip(), (
                f"the {name!r} row names no site: {event!r}"
            )
            assert org, f"the {name!r} row names no organisation"
            # A deal type is only set for sites onboarded under a deal, so an
            # em dash here is legitimate.
            assert deal_type == "—" or deal_type in self.DEAL_TYPES, (
                f"the {name!r} row shows deal type {deal_type!r}"
            )
            assert category in self.CATEGORIES, (
                f"the {name!r} row shows category {category!r}, expected one of "
                f"{self.CATEGORIES}"
            )
            assert assignee, (
                f"the {name!r} row shows no assignee, expected a name or "
                "'Unassigned'"
            )
            # The due date is a date, optionally with an overdue counter under
            # it -- and the counter must only ever appear on an overdue row.
            due_lines = [d.strip() for d in due.split("\n") if d.strip()]
            assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", due_lines[0]), (
                f"the {name!r} row is due {due_lines[0]!r}, expected dd-mm-yyyy"
            )
            assert status in self.ROW_STATUSES, (
                f"the {name!r} row shows status {status!r}, expected one of "
                f"{self.ROW_STATUSES}"
            )
            if len(due_lines) > 1:
                assert re.fullmatch(r"\d+d overdue", due_lines[1]), (
                    f"the {name!r} row carries {due_lines[1]!r} under its due "
                    "date, expected an overdue counter"
                )
                assert status == "Overdue", (
                    f"the {name!r} row is marked {status!r} but still shows "
                    f"{due_lines[1]!r}"
                )

        log.info("Table shows %s event row(s) -- %s", self.rows.count(),
                 self._footer())

    # The action set each status entitles a row to. This is the page's whole
    # safety model and it is stricter than it first looks: an event that has
    # started can no longer be edited or rescheduled, only completed, and a
    # completed event offers nothing at all -- not even Delete, so a finished
    # visit cannot be erased. Everything else is derived from that.
    ROW_ACTIONS = {
        "Overdue": {"Delete", "Edit event", "Schedule"},
        "Upcoming": {"Delete", "Edit event", "Schedule"},
        "Scheduled": {"Delete", "Edit event", "Schedule"},
        "In progress": {"Delete", "Mark complete"},
        "Completed": set(),
        "Cancelled": set(),
    }

    def _row_actions(self, row):
        """The action controls one row offers, by aria-label."""
        return {
            (b.get_attribute("aria-label") or "")
            for b in row.get_by_role("button").all()
        } - {""}

    def check_row_actions_present(self):
        """Every row offers exactly the controls its status entitles it to.

        Delete is asserted present where it belongs and **never clicked** --
        see the class docstring. The matrix is worth pinning precisely because
        it is the product's own guard-rail: if a completed event ever started
        offering Delete or Edit again, a finished and invoiced visit would
        become editable, and that would show up here first.
        """
        seen = {}
        for name, status, available in self._rows_snapshot():
            seen[status] = seen.get(status, 0) + 1

            expected = self.ROW_ACTIONS.get(status)
            assert expected is not None, (
                f"the {name!r} row is {status!r}, which has no known action set"
            )
            # A completed event may additionally offer its report, which only
            # exists once one has been generated.
            extra = available - expected
            assert extra <= {"View maintenance report"}, (
                f"the {status.lower()} event {name!r} offers {sorted(extra)}, "
                f"which its status should not allow"
            )
            missing = expected - available
            assert not missing, (
                f"the {status.lower()} event {name!r} is missing "
                f"{sorted(missing)}"
            )
        log.info("Row actions correct for %s row(s) across %s",
                 self.rows.count(), dict(seen))

    def check_completed_events_are_locked(self):
        """A completed event offers no way to change or remove it.

        Reached through the Completed tile because the default list does not
        contain them -- see `check_default_list_is_open_work`. This is the one
        place the strictest half of the action matrix can actually be
        exercised, and it is the half that matters: once a visit is logged and
        the work is billable, the row must go read-only.
        """
        log.info("Filtering to completed events to check they are locked")
        self._park_mouse()
        self._tile("Completed").click()
        assert self._poll(
            lambda: "status=COMPLETED" in self.page.url, timeout_ms=25000
        )
        assert self._poll(self._loaded, timeout_ms=30000), (
            "the Completed tile returned no rows"
        )

        for name, status, available in self._rows_snapshot():
            assert status == "Completed", (
                f"the Completed tile returned a {status!r} row ({name!r})"
            )
            assert available <= {"View maintenance report"}, (
                f"the completed event {name!r} still offers {sorted(available)} "
                "-- a finished, billable visit must not be editable or "
                "deletable"
            )
        reports = self.page.get_by_role(
            "button", name="View maintenance report"
        ).count()
        log.info("All %s completed event(s) are locked; %s carry a report",
                 self.rows.count(), reports)

        self._park_mouse()
        self._tile("Completed").click()
        assert self._poll(
            lambda: "status=COMPLETED" not in self.page.url, timeout_ms=25000
        )
        assert self._poll(self._loaded, timeout_ms=25000)

    def check_default_list_is_open_work(self):
        """The default list is the open work queue, not every event there is.

        Worth pinning because the tab above it reads "All events" while the
        list deliberately withholds the completed ones: the four tiles count 11
        completed events that the unfiltered table never shows. That is
        defensible as an open-work queue -- it is what an engineer wants to see
        -- but it means the tab's own label and count describe a subset, so a
        change in either direction should be noticed here rather than by a user
        wondering where a finished job went.
        """
        statuses = set(self._column(6))
        total = self._total()
        completed = self._tile_count("Completed")
        log.info("Default list holds %s event(s) in %s; the Completed tile "
                 "counts a further %s", total, sorted(statuses), completed)

        assert "Completed" not in statuses, (
            f"the default list now includes completed events ({statuses}); the "
            "tab count and the tile counts need re-checking against each other"
        )
        assert completed > 0, (
            "no completed events exist on staging, so this check proves nothing"
        )
        stated = re.search(r"(\d+)", self.events_tab.first.inner_text() or "")
        assert stated and int(stated.group(1)) == total, (
            f"the events tab says {stated.group(1) if stated else '?'} but the "
            f"footer counts {total}"
        )

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def search_events(self):
        """Search narrows the table, and a no-match query shows the empty state."""
        before = self._settled_names()

        log.info("Searching events for %r", SEARCH_TERM)
        self._search_for(self.search, SEARCH_TERM, min_rows=1)
        self._await_matches(SEARCH_TERM)
        assert EVENT in self._names(), (
            f"searching for {SEARCH_TERM!r} did not return the pinned event "
            f"{EVENT!r}: {self._names()}"
        )
        log.info("Search %r -> %s event(s)", SEARCH_TERM, self.rows.count())

        log.info("Searching for a term that matches nothing")
        self._search_for(self.search, NO_MATCH)
        assert self._poll(lambda: self.no_events.count() > 0, timeout_ms=25000), (
            "a no-match search did not show the 'No events found' empty state"
        )
        assert self.rows.count() == 0, "the empty state still drew event rows"
        assert self.page.get_by_text(
            "No maintenance events match the current search or filters",
            exact=False,
        ).count() > 0, "the empty state gives the user no way forward"
        expect(self.clear_filters).to_be_visible()
        assert self._poll(
            lambda: self._footer() == "Showing 0 results", timeout_ms=20000
        ), f"the footer should report no results, reads {self._footer()!r}"
        log.info("Empty state shown -- %s", self._footer())

        log.info("Clearing the search")
        self._search_for(self.search, "")
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "the table did not return to its unsearched rows: "
            f"{self._names()[:3]} != {before[:3]}"
        )

    def clear_filters_control(self):
        """The empty state's own Clear filters control drops search and filter.

        It only surfaces once the table is empty, so it is reached by applying
        a filter *and* a search that cannot coexist -- which is also what
        proves it clears both rather than one.
        """
        before = self._settled_names()

        log.info("Applying a category filter and a search that cannot coexist")
        self._apply_filter(self.category_filter, CATEGORY, CATEGORY_PARAM,
                           "category")
        self._search_for(self.search, NO_MATCH)
        assert self._poll(lambda: self.no_events.count() > 0, timeout_ms=25000), (
            "a category filter plus a no-match search did not empty the table"
        )

        log.info("Clearing everything with the Clear filters control")
        expect(self.clear_filters).to_be_visible()
        self.clear_filters.click()
        assert self._poll(lambda: self._names() == before, timeout_ms=35000), (
            "Clear filters did not restore the full table"
        )
        assert (self.search.input_value() or "") == "", (
            f"Clear filters left the search box filled: "
            f"{self.search.input_value()!r}"
        )
        assert "All categories" in (self.category_filter.first.inner_text() or ""), (
            "Clear filters left the category filter applied"
        )
        assert CATEGORY_PARAM not in self.page.url, (
            f"Clear filters left the filter in the URL: {self.page.url}"
        )
        log.info("Clear filters restored %s event(s)", self.rows.count())

    # ----------------------------------------------------------------- #
    # Filters
    # ----------------------------------------------------------------- #
    def filter_by_category(self):
        """Narrow the table to one category, then clear it."""
        before = self._settled_names()

        log.info("Opening the category filter")
        self._park_mouse()
        self.category_filter.first.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0)
        listed = self._options()
        assert listed == self.CATEGORIES, (
            f"the category filter offers {listed}, expected {self.CATEGORIES}"
        )
        # Ticking an option alone must not filter anything -- Apply is what
        # commits, and a filter that jumped the gun would make the Cancel-only
        # discipline elsewhere on this page unreliable.
        self.page.get_by_role("option", name=CATEGORY, exact=True).click()
        self.page.wait_for_timeout(1200)
        assert CATEGORY_PARAM not in self.page.url, (
            "ticking a category applied it without Apply being pressed: "
            f"{self.page.url}"
        )
        expect(self.apply_filter.first).to_be_visible()
        expect(self.clear_filter.first).to_be_visible()
        self.apply_filter.first.click()
        assert self._poll(
            lambda: CATEGORY_PARAM in self.page.url, timeout_ms=25000
        ), f"applying the category filter did not reach the URL: {self.page.url}"
        self._dismiss()

        assert self._poll(self._loaded, timeout_ms=25000), (
            f"the table did not repopulate under the {CATEGORY!r} filter"
        )
        assert CATEGORY in (self.category_filter.first.inner_text() or ""), (
            "the category filter button does not name what it is filtering on: "
            f"{self.category_filter.first.inner_text()!r}"
        )
        assert self._poll(
            lambda: bool(self._column(3))
            and all(v == CATEGORY for v in self._column(3)),
            timeout_ms=25000,
        ), (
            f"the category filter returned row(s) in "
            f"{sorted(set(self._column(3)) - {CATEGORY})}, expected only "
            f"{CATEGORY!r}"
        )
        log.info("Category filter %r -> %s event(s)", CATEGORY, self._total())

        self._clear_filter(self.category_filter, CATEGORY_PARAM, "category")
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "clearing the category filter did not restore the table"
        )
        assert "All categories" in (self.category_filter.first.inner_text() or "")

    def filter_by_status(self):
        """Narrow the table to one status, add a second, then clear both.

        The filters are multi-select, which is the interesting part: a second
        status must *widen* the result rather than replace the first.
        """
        before = self._settled_names()

        log.info("Opening the status filter")
        self._park_mouse()
        self.status_filter.first.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0)
        listed = self._options()
        assert listed == self.STATUSES, (
            f"the status filter offers {listed}, expected {self.STATUSES}"
        )
        self._dismiss()

        self._apply_filter(self.status_filter, STATUS, STATUS_PARAM, "status")
        assert self._poll(self._loaded, timeout_ms=25000)
        assert self._poll(
            lambda: bool(self._column(6))
            and all(v == STATUS for v in self._column(6)),
            timeout_ms=25000,
        ), (
            f"the status filter returned row(s) in "
            f"{sorted(set(self._column(6)) - {STATUS})}, expected only "
            f"{STATUS!r}"
        )
        single = self._total()
        log.info("Status filter %r -> %s event(s)", STATUS, single)

        log.info("Adding %r to the same filter", SECOND_STATUS)
        self._apply_filter(self.status_filter, SECOND_STATUS,
                           SECOND_STATUS_PARAM, "status")
        assert self._poll(self._loaded, timeout_ms=25000)
        # Both are in the URL at once, and the result widened rather than
        # swapping one for the other.
        assert STATUS_PARAM in self.page.url and SECOND_STATUS_PARAM in self.page.url, (
            f"the two statuses did not compose in the URL: {self.page.url}"
        )
        assert self._poll(lambda: self._total() > single, timeout_ms=25000), (
            f"adding {SECOND_STATUS!r} left the table at {self._total()} "
            f"event(s), down from or equal to {single} -- a multi-select must "
            "widen"
        )
        assert set(self._column(6)) <= {STATUS, "In progress"}, (
            f"the combined filter returned {sorted(set(self._column(6)))}"
        )
        log.info("Statuses %r + %r -> %s event(s) (up from %s)",
                 STATUS, SECOND_STATUS, self._total(), single)

        self._clear_filter(self.status_filter, "status=", "status")
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "clearing the status filter did not restore the table"
        )
        assert "All statuses" in (self.status_filter.first.inner_text() or "")

    def filter_by_deal_type(self):
        """Narrow the table to one deal type, then clear it."""
        before = self._settled_names()

        log.info("Opening the deal-type filter")
        self._park_mouse()
        self.deal_type_filter.first.click()
        assert self._poll(lambda: self.page.get_by_role("option").count() > 0)
        listed = self._options()
        assert listed == self.DEAL_TYPES, (
            f"the deal-type filter offers {listed}, expected {self.DEAL_TYPES}"
        )
        self._dismiss()

        self._apply_filter(self.deal_type_filter, DEAL_TYPE, DEAL_TYPE_PARAM,
                           "deal-type")
        assert self._poll(self._loaded, timeout_ms=25000)
        assert DEAL_TYPE in (self.deal_type_filter.first.inner_text() or "")
        assert self._poll(
            lambda: bool(self._column(2))
            and all(v == DEAL_TYPE for v in self._column(2)),
            timeout_ms=25000,
        ), (
            f"the deal-type filter returned row(s) in "
            f"{sorted(set(self._column(2)) - {DEAL_TYPE})}, expected only "
            f"{DEAL_TYPE!r}"
        )
        log.info("Deal-type filter %r -> %s event(s)", DEAL_TYPE, self._total())

        self._clear_filter(self.deal_type_filter, DEAL_TYPE_PARAM, "deal-type")
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "clearing the deal-type filter did not restore the table"
        )
        assert "All deal types" in (self.deal_type_filter.first.inner_text() or "")

    def combine_filters(self):
        """A tile and a dropdown filter narrow together rather than replacing."""
        before = self._settled_names()

        log.info("Applying the In progress tile and the category filter together")
        self._park_mouse()
        self._tile("In progress").click()
        assert self._poll(
            lambda: "status=IN_PROGRESS" in self.page.url, timeout_ms=25000
        )
        assert self._poll(lambda: self.showing.count() > 0, timeout_ms=25000)
        tile_only = self._total()

        self._apply_filter(self.category_filter, CATEGORY, CATEGORY_PARAM,
                           "category")
        assert self._poll(lambda: self.showing.count() > 0, timeout_ms=25000)

        assert "status=IN_PROGRESS" in self.page.url and CATEGORY_PARAM in self.page.url, (
            f"the tile and the filter did not compose in the URL: {self.page.url}"
        )
        # Both narrowings land in one refetch, so the rows are polled until
        # they satisfy the pair rather than read the instant the URL changes --
        # at that point the table is still answering the tile alone.
        assert self._poll(
            lambda: bool(self._column(6))
            and all(s == "In progress" for s in self._column(6))
            and all(c == CATEGORY for c in self._column(3)),
            timeout_ms=30000,
        ), (
            "the combined filter returned "
            + str(sorted(set(zip(self._column(6), self._column(3)))))
            + f", expected only ('In progress', {CATEGORY!r})"
        )
        assert self._total() <= tile_only, (
            f"adding the {CATEGORY!r} filter widened the table from "
            f"{tile_only} to {self._total()} event(s)"
        )
        log.info("Combined tile + category -> %s event(s) (down from %s)",
                 self._total(), tile_only)

        self._clear_filter(self.category_filter, CATEGORY_PARAM, "category")
        self._park_mouse()
        self._tile("In progress").click()
        assert self._poll(
            lambda: "status=IN_PROGRESS" not in self.page.url, timeout_ms=25000
        )
        assert self._poll(lambda: self._names() == before, timeout_ms=30000), (
            "the table did not return to its unfiltered rows"
        )

    # ----------------------------------------------------------------- #
    # Sorting
    # ----------------------------------------------------------------- #
    def sort_columns(self):
        """Every sortable column takes a sort, and the rest offer none.

        This pins the *contract* -- which columns sort, what each sends, and
        that a third click clears it. Whether the rows actually come back in
        order is checked separately by `sort_columns_reorder`, which currently
        fails on the product.
        """
        for col, param in self.SORTABLE.items():
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

            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(r"[?&]sort_order=desc\b"), timeout=20000
            )
            assert self._poll(self._loaded, timeout_ms=25000)

            self._park_mouse()
            self._header(col).click()
            assert self._poll(
                lambda: "sort_by=" not in self.page.url, timeout_ms=20000
            ), (
                f"a third click on {col!r} left the sort in the URL: "
                f"{self.page.url}"
            )
            assert self._poll(self._loaded, timeout_ms=25000)
            log.info("Column %-10s sorts both ways and clears (sort_by=%s)",
                     col, param)

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
        total = self._total()
        log.info("Paginating %s event(s)", total)

        for size in self.PAGE_SIZES:
            self._park_mouse()
            self.page_size.first.click()
            assert self._poll(
                lambda: self.page.get_by_role("option").count() > 0, timeout_ms=8000
            ), "the page-size selector opened with no options"
            listed = self._options()
            assert listed == self.PAGE_SIZES, (
                f"the page-size selector offers {listed}, expected "
                f"{self.PAGE_SIZES}"
            )
            self.page.get_by_role("option", name=size, exact=True).click()
            self._dismiss()

            expected = min(int(size), total)
            assert self._poll(
                lambda e=expected: self.rows.count() == e, timeout_ms=30000
            ), (
                f"page size {size} drew {self.rows.count()} row(s), expected "
                f"{expected}"
            )
            assert self._poll(
                lambda e=expected: self._footer() == f"Showing 1–{e} of {total}",
                timeout_ms=20000,
            ), f"at page size {size} the footer reads {self._footer()!r}"
            last = -(-total // int(size))
            assert self._pages()[-1] == str(last), (
                f"page size {size} over {total} event(s) should end on page "
                f"{last}, the pager ends on {self._pages()[-1]}"
            )
            log.info("Page size %-3s -> %s row(s), %s page(s) -- %s",
                     size, self.rows.count(), last, self._footer())

        log.info("Stepping through the pages at size 10")
        self._park_mouse()
        self.page_size.first.click()
        self._poll(lambda: self.page.get_by_role("option").count() > 0)
        self.page.get_by_role("option", name="10", exact=True).click()
        self._dismiss()
        assert self._poll(lambda: self.rows.count() == 10, timeout_ms=30000)
        first_page = self._settled_names()

        assert self.next_page.is_enabled(), (
            f"page size 10 over {total} event(s) should give more than one page"
        )
        assert self.prev_page.is_disabled(), (
            "the previous-page control is enabled on page 1"
        )

        self._park_mouse()
        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2\b"), timeout=20000)
        assert self._poll(
            lambda: self._names() and self._names() != first_page, timeout_ms=25000
        ), "page 2 shows the same events as page 1"
        assert not set(self._names()) & set(first_page), (
            "page 2 repeats events from page 1"
        )
        assert self._poll(
            lambda: self._footer() == f"Showing 11–{total} of {total}",
            timeout_ms=20000,
        ), f"page 2's footer reads {self._footer()!r}"
        log.info("Page 2 shows %s event(s) -- %s", self.rows.count(),
                 self._footer())

        self._park_mouse()
        self.prev_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=1\b"), timeout=20000)
        assert self._poll(lambda: self._names() == first_page, timeout_ms=25000), (
            "going back did not restore page 1"
        )

    # ----------------------------------------------------------------- #
    # Write paths -- opened, validated, never submitted
    # ----------------------------------------------------------------- #
    def check_add_event_panel(self):
        """The new-event panel offers every field and guards its submit.

        Never submitted: an event created here is a real work item against a
        real site, and the page offers no way to withdraw it except Delete --
        which this suite does not use.
        """
        log.info("Opening the Add event panel")
        self._park_mouse()
        self.add_event.click()
        title = self.page.get_by_text("New maintenance event", exact=True)
        expect(title.first).to_be_visible(timeout=25000)
        panel = self.dialog.last

        text = panel.inner_text() or ""
        for field in self.EVENT_FORM_FIELDS:
            assert field in text, f"the new-event panel is missing {field!r}"
        assert "One-off event" in text and "Recurring plan" in text, (
            "the panel does not offer both event kinds"
        )
        # A recurring plan cannot be started from here yet -- the control is
        # present but disabled, which is worth pinning so it is noticed if it
        # ever quietly becomes live.
        recurring = panel.get_by_role("button", name=re.compile("Recurring plan"))
        assert recurring.first.is_disabled(), (
            "the Recurring plan tab is now enabled; this panel's coverage "
            "needs extending to the plan form behind it"
        )

        # Empty, the panel must refuse to submit.
        submit = panel.get_by_role("button", name="Add event", exact=True)
        assert submit.first.is_disabled(), (
            "Add event is enabled with no site and no name entered"
        )

        log.info("Checking the site selector")
        panel.get_by_role("button", name=re.compile(r"^Site")).first.click()
        assert self._poll(
            lambda: self.page.get_by_role("option").count() > 0, timeout_ms=15000
        ), "the site selector opened with no options"
        sites = self._options()
        assert sites and all(s.strip() for s in sites), (
            f"the site selector offers blank entries: {sites[:5]}"
        )
        # It is searchable -- the estate is far too large to scroll.
        expect(
            self.page.get_by_placeholder(re.compile(r"^Search sites"))
        ).to_be_visible()
        log.info("Site selector opens on %s site(s)", len(sites))
        self._dismiss()

        # The three categories are offered as a segmented control.
        for category in self.CATEGORIES:
            assert panel.get_by_role(
                "button", name=category, exact=True
            ).count() == 1, f"the panel does not offer the {category!r} category"

        # A name alone is still not enough -- the site is what the event hangs
        # off, so the submit must stay shut.
        panel.get_by_placeholder("e.g. Connector replacement").fill(
            "AUTOMATION CHECK -- not submitted"
        )
        self.page.wait_for_timeout(800)
        assert submit.first.is_disabled(), (
            "Add event is enabled with a name but no site chosen"
        )
        log.info("Add event stays disabled until a site is chosen")

        log.info("Cancelling the panel without creating anything")
        panel.get_by_role("button", name="Cancel", exact=True).click()
        assert self._poll(lambda: title.count() == 0, timeout_ms=20000), (
            "the Add event panel did not close"
        )

    def check_edit_event_panel(self):
        """The edit panel pre-fills the event and locks the site, then cancels."""
        log.info("Finding %r to edit", EVENT)
        self._search_for(self.search, SEARCH_TERM, min_rows=1)
        row = self.table.locator(f"tbody > tr:has-text({EVENT!r})").first
        assert row.count(), f"{EVENT!r} is not in the table"

        log.info("Opening the edit panel")
        self._park_mouse()
        row.get_by_role("button", name="Edit event", exact=True).click()
        title = self.page.get_by_text("Edit event", exact=True)
        expect(title.first).to_be_visible(timeout=25000)
        panel = self.dialog.last

        text = panel.inner_text() or ""
        assert "The site cannot be changed" in text, (
            "the edit panel does not say the site is fixed"
        )
        # The site is shown as text rather than a control -- that is the lock.
        assert EVENT_SITE in text, (
            f"the edit panel does not name the event's site ({EVENT_SITE!r})"
        )
        assert panel.get_by_role(
            "button", name=re.compile(r"^Site")
        ).count() == 0, "the edit panel offers a site selector; the site is "
        "supposed to be fixed once an event exists"

        # The name comes back pre-filled -- a panel that opened empty would
        # blank the event on save.
        name = panel.get_by_placeholder("e.g. Connector replacement")
        assert (name.first.input_value() or "").strip() == EVENT, (
            f"the edit panel opened on {name.first.input_value()!r}, expected "
            f"{EVENT!r}"
        )
        for field in ["Event name", "Category", "Assignee", "Due date", "Notes"]:
            assert field in text, f"the edit panel is missing {field!r}"
        expect(panel.get_by_role("button", name="Save changes")).to_be_visible()
        log.info("Edit panel pre-filled with %r, site locked to %r",
                 EVENT, EVENT_SITE)

        log.info("Cancelling the edit without saving")
        panel.get_by_role("button", name="Cancel", exact=True).click()
        assert self._poll(lambda: title.count() == 0, timeout_ms=20000), (
            "the edit panel did not close"
        )
        self._search_for(self.search, "")

    def check_schedule_dialog(self):
        """The Schedule dialog carries the event's facts, then is cancelled."""
        schedulable = self.schedule
        assert schedulable.count(), (
            "no event on this page can be scheduled, so the dialog cannot be "
            "checked"
        )
        log.info("Opening the Schedule dialog")
        self._park_mouse()
        schedulable.first.click()
        title = self.page.get_by_role("heading", name="Schedule Event")
        expect(title.first).to_be_visible(timeout=25000)
        dialog = self.dialog.last

        text = dialog.inner_text() or ""
        for field in self.SCHEDULE_FIELDS:
            assert field in text, f"the Schedule dialog is missing {field!r}"
        # It restates the event it is scheduling, in the engineer's terms.
        assert re.search(r"(PREVENTIVE|CORRECTIVE|INSPECTION)", text), (
            "the Schedule dialog does not state the maintenance type"
        )
        assert re.search(r"\d{2}-\d{2}-\d{4}", text), (
            "the Schedule dialog states no scheduled date"
        )
        # Start and end are pre-filled as a working window rather than left
        # blank for the engineer to guess at.
        times = re.findall(r"\d{1,2} \w{3} \d{4}, \d{2}:\d{2}", text)
        assert len(times) >= 2, (
            f"the Schedule dialog pre-fills no start/end window: {times}"
        )
        log.info("Schedule dialog pre-fills the window %s", times[:2])
        expect(
            dialog.get_by_role("button", name="Schedule Event", exact=True)
        ).to_be_visible()

        log.info("Cancelling the Schedule dialog without scheduling")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        assert self._poll(lambda: title.count() == 0, timeout_ms=20000), (
            "the Schedule dialog did not close"
        )

    def check_complete_panel(self):
        """The completion panel: lifecycle, visit log and checklist, cancelled.

        This is the most destructive control on the page -- completing an event
        writes a site visit and a signed-off checklist that the finance side
        later bills from, and there is no way back. So the panel is checked on
        what it *asks for* and then abandoned at step one of two.
        """
        completable = self.mark_complete
        assert completable.count(), (
            "no event on this page is in progress, so the completion panel "
            "cannot be checked"
        )
        log.info("Opening the completion panel")
        self._park_mouse()
        completable.first.click()
        # Matched case-insensitively: the heading is upper-cased by CSS, so it
        # reads "COMPLETE — STEP 1 OF 2" on screen while the DOM -- which is
        # what a text locator matches against -- holds "Complete — step 1 of 2".
        step = self.page.get_by_text(re.compile(r"step 1 of 2", re.I))
        expect(step.first).to_be_visible(timeout=25000)
        panel = self.dialog.last

        text = panel.inner_text() or ""
        # The lifecycle strip shows where the event has got to.
        assert "LIFECYCLE" in text, "the completion panel shows no lifecycle"
        for stage in ["Created", "Scheduled", "In progress", "Completed"]:
            assert stage in text, f"the lifecycle omits {stage!r}"

        # It explains why a visit is mandatory rather than failing later.
        assert "at least one site visit" in text, (
            "the panel does not explain that a visit is required"
        )
        for field in ["Visit title", "Technician", "Visit type", "Start", "End"]:
            assert field in text, f"the visit form is missing {field!r}"
        # ...and the checklist that gets signed off with it.
        assert "CHECKLIST" in text.upper(), (
            "the completion panel carries no checklist"
        )
        assert "Attachments" in text, (
            "the completion panel offers nowhere to attach evidence"
        )
        # It is step one of two -- the next step reviews before committing.
        expect(
            panel.get_by_role("button", name=re.compile("Continue to review"))
        ).to_be_visible()
        log.info("Completion panel asks for a visit, a checklist and evidence")

        log.info("Abandoning the completion at step 1 -- nothing is submitted")
        self._close_panel(step, "completion")

    def check_delete_is_offered_but_untouched(self):
        """Delete appears on exactly the rows it should -- and is never pressed.

        Recorded explicitly rather than left implicit: the read-only discipline
        this whole file depends on is only meaningful if the destructive
        control is known to be there and known to be avoided. The count is
        derived from the statuses on screen rather than from the row count,
        because a completed event deliberately offers no Delete -- a finished,
        invoiced visit is not erasable, and that is the behaviour being pinned.

        If the product ever gains a confirmation step in front of Delete, this
        is where covering it belongs -- still without pressing it.
        """
        deletable = [
            status for status in self._column(6)
            if "Delete" in self.ROW_ACTIONS.get(status, set())
        ]
        assert self.delete_row.count() == len(deletable), (
            f"{len(deletable)} row(s) should offer Delete but "
            f"{self.delete_row.count()} do"
        )
        for control in self.delete_row.all():
            assert control.is_enabled(), "a Delete control is disabled"
        log.info("Delete offered on %s of %s row(s) -- none pressed",
                 self.delete_row.count(), self.rows.count())

    # ----------------------------------------------------------------- #
    # Calendar
    # ----------------------------------------------------------------- #
    def check_calendar(self):
        """The calendar renders the same events across month, week and day."""
        log.info("Switching to the calendar")
        self._park_mouse()
        self.calendar_view.click()
        assert self._poll(
            lambda: "view=calendar" in self.page.url, timeout_ms=25000
        ), f"the calendar toggle did not reach the URL: {self.page.url}"
        assert self._poll(
            lambda: self.month_title.count() > 0, timeout_ms=30000
        ), "the calendar never drew its period title"

        opened = (self.month_title.first.inner_text() or "").strip()
        log.info("Calendar opened on %s", opened)

        # The month grid names every weekday. Polled rather than read once:
        # the period title renders from the current date as soon as the view
        # mounts, but the grid beneath it waits on the month's events, so a
        # single read here catches the header with no grid under it.
        assert self._poll(
            lambda: all(
                day in (self.page.locator("body").inner_text() or "")
                for day in self.WEEKDAYS
            ),
            timeout_ms=30000,
        ), (
            "the month grid never drew its weekday headers: missing "
            + str([
                day for day in self.WEEKDAYS
                if day not in (self.page.locator("body").inner_text() or "")
            ])
        )

        # The three views are tabs rather than buttons -- a role="button"
        # lookup finds nothing here.
        for view in self.CALENDAR_VIEWS:
            tab = self.page.get_by_role("tab", name=view, exact=True)
            assert tab.count() == 1, (
                f"the calendar offers no {view!r} view"
            )
        assert self.page.get_by_role(
            "tab", name="Month", exact=True
        ).get_attribute("aria-selected") == "true", (
            "the calendar does not open on the month view"
        )

        log.info("Stepping forward a month and back")
        self._park_mouse()
        self.next_period.first.click()
        assert self._poll(
            lambda o=opened: (self.month_title.first.inner_text() or "").strip() != o,
            timeout_ms=20000,
        ), "the calendar would not step forward"
        stepped = (self.month_title.first.inner_text() or "").strip()
        self.prev_period.first.click()
        assert self._poll(
            lambda o=opened: (self.month_title.first.inner_text() or "").strip() == o,
            timeout_ms=20000,
        ), f"stepping back did not return to {opened!r}"
        log.info("Calendar steps %s -> %s -> %s", opened, stepped, opened)

        for view in ["Week", "Day"]:
            log.info("Switching to the %s view", view)
            self._park_mouse()
            self.page.get_by_role("tab", name=view, exact=True).click()
            assert self._poll(
                lambda v=view: self.page.get_by_role(
                    "tab", name=v, exact=True
                ).get_attribute("aria-selected") == "true",
                timeout_ms=20000,
            ), f"the {view} tab did not become selected"
            # Both narrower views lay the day out against a clock.
            assert self._poll(
                lambda: len(re.findall(
                    r"\b\d{2}:00\b", self.page.locator("body").inner_text() or ""
                )) > 4,
                timeout_ms=20000,
            ), f"the {view} view draws no hour axis"
            log.info("%s view drew its hour axis", view)

        self._park_mouse()
        self.page.get_by_role("tab", name="Month", exact=True).click()
        assert self._poll(
            lambda: self.page.get_by_role(
                "tab", name="Month", exact=True
            ).get_attribute("aria-selected") == "true",
            timeout_ms=20000,
        )

        self._check_calendar_event()

        log.info("Switching back to the list")
        self._park_mouse()
        self.list_view.click()
        assert self._poll(
            lambda: "view=calendar" not in self.page.url, timeout_ms=25000
        ), f"the list toggle did not clear the view: {self.page.url}"
        assert self._poll(self._loaded, timeout_ms=30000), (
            "the event table did not come back"
        )

    def _check_calendar_event(self):
        """An event in the grid opens its visit history -- read only."""
        # Calendar entries are the only unlabelled buttons inside the grid, so
        # they are found by elimination against the chrome around them.
        chrome = set(self.CALENDAR_VIEWS) | {"List", "Calendar", "Add event"}
        entries = [
            b for b in self.page.get_by_role("button").all()
            if b.get_attribute("aria-label") is None
            and (b.inner_text() or "").strip()
            and (b.inner_text() or "").strip() not in chrome
            and "maintenance events" not in (b.inner_text() or "")
            and not re.match(
                r"^(All (events|plans)|Status|Category|Deal Type|🇬🇧|\d+$)",
                (b.inner_text() or "").strip(),
            )
        ]
        if not entries:
            log.info("No events fall in this month -- skipping the entry check")
            return

        entry = entries[0]
        name = (entry.inner_text() or "").strip()
        log.info("Opening the calendar entry %r", name)
        entry.click()
        title = self.page.get_by_role("heading", name="Visits").first
        expect(title).to_be_visible(timeout=25000)
        dialog = self.dialog.last

        # The dialog's shell renders before its visit history is fetched, so
        # the fields are polled rather than read the instant it opens.
        fields = ["Site", "Organisation", "Due Date", "Total Visits",
                  "Status :", "Visit History"]
        assert self._poll(
            lambda: all(f in (dialog.inner_text() or "") for f in fields),
            timeout_ms=25000,
        ), (
            "the visit dialog never showed "
            + str([f for f in fields if f not in (dialog.inner_text() or "")])
        )

        text = dialog.inner_text() or ""
        assert re.search(r"\d{2}-\d{2}-\d{4}", text), (
            "the visit dialog states no due date"
        )
        assert re.search(r"(Preventive|Corrective|Inspection)", text), (
            "the visit dialog states no category"
        )
        # The visit tally is the number the completion flow later writes to,
        # so it must at least be a number rather than a blank.
        visits = re.search(r"Total Visits\s*\n?\s*(\d+)", text)
        assert visits, f"the visit dialog states no visit count: {text[:200]!r}"
        log.info("Visit dialog for %r carries all its facts (%s visit(s))",
                 name, visits.group(1))

        self._close_panel(title, "visit")

    # ----------------------------------------------------------------- #
    # Plans tab
    # ----------------------------------------------------------------- #
    def check_plans_tab(self):
        """The recurring plans behind the events: cards, search and menus."""
        # The plans tab renders the events list's own footer and pager, so it
        # must be entered from the list view: switching straight out of the
        # calendar leaves the two views fighting over the same region and the
        # cards never mount.
        if "view=calendar" in self.page.url:
            self._park_mouse()
            self.list_view.click()
            self._poll(lambda: "view=calendar" not in self.page.url,
                       timeout_ms=25000)
            self._poll(self._loaded, timeout_ms=30000)

        events_total = self._total()
        log.info("Switching to the plans tab")
        self._park_mouse()
        self.plans_tab.first.click()
        assert self._poll(
            lambda: "tab=plans" in self.page.url, timeout_ms=25000
        ), f"the plans tab did not reach the URL: {self.page.url}"
        cards = self.page.get_by_role("button", name=re.compile(r"^More actions for"))
        assert self._poll(lambda: cards.count() > 0, timeout_ms=40000), (
            "the plans tab drew no plan cards"
        )
        # The events table is gone -- plans are cards, not rows.
        assert self.page.locator("table").count() == 0, (
            "the plans tab still renders the events table"
        )
        log.info("Plans tab drew %s card(s) -- %s", cards.count(), self._footer())

        # The tab switch states its own count, and it is not the events count.
        plans_total = self._total()
        stated = re.search(r"(\d+)", self.plans_tab.first.inner_text() or "")
        assert stated and int(stated.group(1)) == plans_total, (
            f"the plans tab says {stated.group(1) if stated else '?'} but the "
            f"footer counts {plans_total}"
        )
        assert self._poll(
            lambda: str(events_total) in (self.events_tab.first.inner_text() or ""),
            timeout_ms=10000,
        ), "the events tab lost its count while the plans tab is open"

        self._check_plan_cards(cards)
        self._check_plan_menu()
        self._check_plan_search(cards)
        self._check_edit_plan_dialog()
        self._check_view_plan_events()

    def _check_plan_cards(self, cards):
        """Every card states the cadence it runs on and how it is faring."""
        body = self.page.locator("body").inner_text() or ""
        for name in self._plan_names():
            assert name, "a plan card carries no name"
        # Each plan states a frequency, a category and a live status.
        assert re.search(r"(Weekly|Monthly|Quarterly|Every \d+ months)", body), (
            "no plan card states a frequency"
        )
        assert re.search(r"(Preventive|Corrective|Inspection)", body), (
            "no plan card states a category"
        )
        # The cadence line is the useful part: it says whether the plan is
        # still generating work, or has stalled behind an overdue event.
        assert "Cadence" in body, (
            "the plan cards do not report their cadence health"
        )
        healthy = body.count("Cadence healthy")
        stalled = body.count("Cadence stalled")
        assert healthy + stalled == cards.count(), (
            f"{cards.count()} plan(s) but {healthy + stalled} cadence line(s)"
        )
        assert self.page.get_by_role(
            "button", name=re.compile(r"^View events")
        ).count() == cards.count(), (
            f"{cards.count()} plan card(s) but "
            f"{self.page.get_by_role('button', name=re.compile(r'^View events')).count()}"
            " View events control(s)"
        )
        log.info("Plan cadence: %s healthy, %s stalled", healthy, stalled)

        assert PLAN in body, (
            f"the pinned plan {PLAN!r} is not on the first page of plans"
        )

    def _check_plan_menu(self):
        """The per-plan overflow menu offers its three actions -- none taken."""
        log.info("Opening a plan's overflow menu")
        self._park_mouse()
        self.page.get_by_role(
            "button", name=f"More actions for {PLAN}", exact=True
        ).click()
        assert self._poll(
            lambda: self.page.locator(
                "[data-radix-popper-content-wrapper]"
            ).count() > 0,
            timeout_ms=15000,
        ), "the plan menu opened nothing"
        offered = [
            o.strip()
            for o in (
                self.page.locator("[data-radix-popper-content-wrapper]")
                .last.inner_text() or ""
            ).split("\n")
            if o.strip()
        ]
        assert offered == self.PLAN_MENU, (
            f"the plan menu offers {offered}, expected {self.PLAN_MENU}"
        )
        # Pause and Cancel are both one-way from here, so neither is taken.
        log.info("Plan menu offers %s -- none taken", offered)
        self._dismiss()

    def _check_plan_search(self, cards):
        """Plans have their own search box and their own empty state."""
        before = cards.count()
        expect(self.plan_search).to_be_visible()

        log.info("Searching plans for %r", SEARCH_TERM)
        self._search_for(self.plan_search, SEARCH_TERM)

        _names = self._plan_names

        # Polled on the names themselves rather than on the count: the card
        # list is still the unsearched one for a beat after the box is filled,
        # and a check that merely counted would pass against it.
        assert self._poll(
            lambda: bool(_names())
            and all(SEARCH_TERM.lower() in n.lower() for n in _names()),
            timeout_ms=30000,
        ), (
            f"the plan search for {SEARCH_TERM!r} returned plan(s) that do not "
            "carry it: "
            + str([n for n in _names() if SEARCH_TERM.lower() not in n.lower()])
        )
        assert len(_names()) < before, (
            f"the plan search for {SEARCH_TERM!r} left all {before} plan(s) on "
            "screen"
        )
        log.info("Plan search %r -> %s plan(s): %s", SEARCH_TERM,
                 cards.count(), _names())

        log.info("Searching plans for a term that matches nothing")
        self._search_for(self.plan_search, NO_MATCH)
        assert self._poll(lambda: self.no_plans.count() > 0, timeout_ms=25000), (
            "a no-match plan search did not show the 'No plans found' state"
        )
        assert cards.count() == 0, "the empty state still drew plan cards"
        assert self.page.get_by_text(
            "No maintenance plans match the current search", exact=False
        ).count() > 0, "the plans empty state gives the user no way forward"
        expect(self.clear_filters).to_be_visible()
        assert self._footer() == "Showing 0 results", (
            f"the plans footer reads {self._footer()!r}"
        )

        log.info("Clearing the plan search")
        self._search_for(self.plan_search, "")
        assert self._poll(lambda: cards.count() == before, timeout_ms=30000), (
            f"the plan list did not come back: {cards.count()} != {before}"
        )

    def _check_edit_plan_dialog(self):
        """The plan editor offers every field, then is cancelled."""
        log.info("Opening the Edit plan dialog")
        self._park_mouse()
        self.edit_plan.first.click()
        title = self.page.get_by_role("heading", name="Edit Maintenance Plan")
        expect(title.first).to_be_visible(timeout=25000)
        dialog = self.dialog.last

        text = dialog.inner_text() or ""
        for field in self.PLAN_FORM_FIELDS:
            assert field in text, f"the plan editor is missing {field!r}"
        # A plan's start date is fixed once it exists -- it is what every
        # generated event is dated from -- so it shows as a value, not a field.
        assert re.search(r"Start Date :\s*\n?\s*\d{2}-\d{2}-\d{4}", text), (
            f"the plan editor states no start date: {text[:200]!r}"
        )
        assert re.search(r"(ACTIVE|PAUSED|CANCELLED)", text), (
            "the plan editor states no plan status"
        )
        assert "File format:" in text, (
            "the plan editor offers no attachment upload"
        )
        expect(dialog.get_by_role("button", name="Save Changes")).to_be_visible()
        log.info("Plan editor offers all %s field(s)", len(self.PLAN_FORM_FIELDS))

        log.info("Cancelling the plan editor without saving")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        assert self._poll(lambda: title.count() == 0, timeout_ms=20000), (
            "the plan editor did not close"
        )

    def _check_view_plan_events(self):
        """A plan drills through to exactly the events it generated."""
        log.info("Drilling from a plan into its events")
        view = self.page.get_by_role("button", name=re.compile(r"^View events"))
        stated = re.search(r"\((\d+)\)", view.first.inner_text() or "")
        assert stated, (
            f"the View events control states no count: "
            f"{view.first.inner_text()!r}"
        )
        expected = int(stated.group(1))

        self._park_mouse()
        view.first.click()
        assert self._poll(
            lambda: "maintenance_plan_id=" in self.page.url, timeout_ms=25000
        ), f"the drill-through set no plan filter: {self.page.url}"
        assert self._poll(
            lambda: "tab=events" in self.page.url, timeout_ms=20000
        ), "the drill-through did not switch to the events tab"
        assert self._poll(
            lambda: self._total() == expected, timeout_ms=30000
        ), (
            f"the plan says it generated {expected} event(s) but the drill-"
            f"through returns {self._total()}"
        )
        log.info("Plan drill-through -> %s event(s), matching its stated count",
                 expected)

    # ----------------------------------------------------------------- #
    # Split out of the main workflow -- see the note in the test file
    # ----------------------------------------------------------------- #
    def sort_columns_reorder(self):
        """Sorting really reorders the rows, not only the URL.

        `sort_columns` pins the contract -- which columns sort, what each
        sends, and that a third click clears it. This goes further and checks
        the rows come back in the order the column was sorted on.

        Checked on Due Date only, and deliberately so. Status looks like the
        obvious second candidate but cannot be verified from the screen: the
        label in that column is *derived* rather than stored. "Overdue" is not
        a status the API holds -- it is computed at render time from a due date
        in the past on an event that is not yet complete -- so an UPCOMING and
        a SCHEDULED event both surface as "Overdue" once their date passes.
        Sorting by the real column therefore returns displayed labels that
        interleave (Overdue, Upcoming, Overdue, ...) while being perfectly
        ordered underneath.

        Asserting a grouping on those labels would fail against correct
        behaviour, so the Status column's sorting is left to `sort_columns`,
        which pins the contract without claiming to know the order. Worth
        knowing that a user sorting by Status sees an apparently jumbled column
        -- that is a UX wart rather than a defect, and is not something this
        suite can prove either way.
        """
        for col, index in (("Due Date", 5),):
            param = self.SORTABLE[col]
            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(rf"[?&]sort_by={param}&sort_order=asc\b"), timeout=20000
            )
            assert self._poll(self._loaded, timeout_ms=25000), (
                f"the table is empty after sorting by {col!r}"
            )
            self._settled_names()

            values = [v.split(" ")[0] for v in self._column(index)]
            # dd-mm-yyyy does not sort as text, so each date is keyed on
            # (yyyy, mm, dd) before comparing.
            keyed = [tuple(reversed(v.split("-"))) for v in values]
            ordered = sorted(keyed)

            assert keyed == ordered, (
                f"sorting by {col!r} set sort_by={param}&sort_order=asc but the "
                f"rows came back out of order: {values}"
            )
            log.info("Column %-9s really sorts ascending", col)

            self._park_mouse()
            self._header(col).click()
            self.page.wait_for_url(
                re.compile(r"[?&]sort_order=desc\b"), timeout=20000
            )
            assert self._poll(self._loaded, timeout_ms=25000)
            # Polled rather than read once. The URL flips to desc the instant
            # the header is clicked, well before the refetch it triggered comes
            # back, so the rows on screen at that point are still the ascending
            # ones -- and already stable, so settling first would lock onto
            # them and report a reversal that never happened.
            assert self._poll(
                lambda v=values: bool(self._column(index))
                and [x.split(" ")[0] for x in self._column(index)] != v,
                timeout_ms=30000,
            ), (
                f"reversing the {col!r} sort returned the same order: {values}"
            )
            # Descending page 1 holds the *last* rows of the set, so it is not
            # the reverse of ascending page 1 -- with 18 events over a page of
            # 10 the two pages barely overlap. What must hold is that this page
            # is itself in descending order.
            reversed_values = [
                v.split(" ")[0] for v in self._settled_names_column(index)
            ]
            keyed = [tuple(reversed(v.split("-"))) for v in reversed_values]
            assert keyed == sorted(keyed, reverse=True), (
                f"reversing the {col!r} sort returned rows out of descending "
                f"order: {reversed_values}"
            )

            self._park_mouse()
            self._header(col).click()
            self._poll(lambda: "sort_by=" not in self.page.url, timeout_ms=20000)

    def plans_tab_pagination(self):
        """The plans tab pages through every plan it counts.

        Split out of the main workflow because it leaves the plans tab on its
        second page, which the workflow's later checks would have to unwind.
        The interesting assertion is the last one: no plan may appear on both
        pages, which is what catches a pager that re-requests page 1 while
        relabelling itself.
        """
        cards = self.page.get_by_role("button", name=re.compile(r"^More actions for"))
        assert self._poll(lambda: cards.count() > 0, timeout_ms=30000), (
            "the plans tab drew no cards"
        )
        total = self._total()
        first_page = sorted(self._plan_names())
        log.info("Plans page 1 shows %s of %s -- %s", len(first_page), total,
                 self._footer())

        assert self.next_page.is_enabled(), (
            f"{total} plan(s) over a page of {len(first_page)} should give more "
            "than one page"
        )
        self._park_mouse()
        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2\b"), timeout=20000)
        assert self._poll(
            lambda: cards.count() > 0
            and sorted(self._plan_names()) != first_page,
            timeout_ms=25000,
        ), (
            f"stepping to page 2 left the same {len(first_page)} plan(s) on "
            f"screen; the remaining {total - len(first_page)} cannot be reached"
        )
        second_page = sorted(self._plan_names())
        assert not set(first_page) & set(second_page), (
            "page 2 repeats plans from page 1"
        )
        log.info("Plans page 2 shows %s more plan(s)", len(second_page))

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def maintenance_page(self):
        self.open_page()
        self.check_table_structure()
        self.check_row_actions_present()
        self.check_delete_is_offered_but_untouched()
        self.check_default_list_is_open_work()
        self.check_tiles()
        self.check_completed_events_are_locked()
        self.search_events()
        self.filter_by_category()
        self.filter_by_status()
        self.filter_by_deal_type()
        self.combine_filters()
        self.clear_filters_control()
        self.sort_columns()
        self.paginate()
        self.check_add_event_panel()
        self.check_edit_event_panel()
        self.check_schedule_dialog()
        self.check_complete_panel()
        self.check_calendar()
        self.check_plans_tab()
        log.info("Maintenance workflow completed")
