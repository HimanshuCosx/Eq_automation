import logging
import re
import time

from playwright.sync_api import expect

from config import BASE_URL

log = logging.getLogger("eq_automation.notifications")


class notifications:
    """Notifications (/notifications).

    The page is reached from the header bell rather than the sidebar: the bell
    opens a popover carrying a "View all" link to the full list. The list
    groups notifications by day (Today / Yesterday / Earlier) and is driven by
    a tab bar (All / Unread / Read), a multi-select Category filter and a Date
    filter, with page-size and pagination controls at the foot. (Earlier builds
    also had a "Search notifications..." box; it has been removed from the page
    and its check was dropped with it.)

    The workflow is entirely read-only and self-resetting -- every filter is
    cleared again so the list is left exactly as it was found. Marking
    notifications read is deliberately *not* clicked: the app exposes no "mark
    as unread", so it is an irreversible write against shared staging data.
    Its controls are validated in place instead (see `mark_as_read`).
    """

    def __init__(self, page):
        self.page = page

        # Navigation: header bell -> "View all" popover link. The app renders
        # both a desktop and a mobile header, so two "Notifications" bells exist
        # in the DOM; scope to the visible one to avoid a strict-mode clash.
        self.bell = page.locator("button[aria-label='Notifications']:visible").first
        self.view_all = page.get_by_role("link", name="View all").first
        self.heading = page.locator("//h1[normalize-space()='Notifications']")

        # Tab bar
        self.tab_all = page.get_by_role("button", name="All", exact=True)
        self.tab_unread = page.get_by_role("button", name=re.compile(r"^Unread"))
        self.tab_read = page.get_by_role("button", name=re.compile(r"^Read\b"))

        # Filters
        self.category_btn = page.get_by_role("button", name=re.compile(r"^Category"))
        # The date trigger's label is "Select date" until a preset is chosen,
        # after which it becomes the preset name. Anchored so it matches only the
        # trigger, never the "Remove Date filter …" chip that appears alongside.
        self.date_btn = page.get_by_role(
            "button",
            name=re.compile(r"^(Select date|Last 7 days|Last 30 days|"
                            r"Last 3 months|Today|Custom)$"),
        )
        # The applied date filter renders a removable chip; clicking its X is the
        # intended way to clear the filter again.
        self.date_remove = page.get_by_role(
            "button", name=re.compile(r"^Remove Date filter")
        )
        # Both filter popovers render as a dialog carrying Clear / Apply; only
        # one is ever open at a time, so these are scoped to the open dialog.
        self.dialog = page.get_by_role("dialog")
        self.apply_btn = self.dialog.get_by_role("button", name="Apply", exact=True)
        self.clear_btn = self.dialog.get_by_role("button", name="Clear", exact=True)

        # Notification rows. Each is a button carrying the notification title as
        # its accessible name; the utility-class combo is unique to these rows
        # and never matches the page's control buttons.
        self.items = page.locator("button.w-full.items-start.text-left")

        # Unread items expose a per-item "Mark \"<title>\" as read" button (the
        # quotes distinguish it from the global "Mark all as read").
        self.item_mark_read = page.get_by_role(
            "button", name=re.compile(r'^Mark ".*" as read$')
        )
        self.mark_all_read = page.get_by_role("button", name="Mark all as read")

        # Empty state for the Unread tab.
        self.caught_up = page.get_by_text("You're all caught up", exact=False)
        # Any empty state (the Unread "caught up" copy, or a "No … notifications"
        # message on other tabs/filters). Notification data is live, so a tab or
        # filter can legitimately resolve to an empty list between runs; this lets
        # the list be considered "settled" on either rows or an empty state.
        # The Read tab's empty copy is "Nothing read yet", and a filter that
        # matches nothing reads "No matching notifications".
        self.empty_any = page.get_by_text(
            re.compile(r"No .*notifications|caught up|Nothing read yet", re.I)
        ).first

        # A row or an empty state -- the list is "ready" once either is showing.
        self.list_ready = self.items.first.or_(self.empty_any)

        # "Showing 1–10 of 255" -- the footer carries the tab's total.
        self.showing = page.get_by_text(re.compile(r"^Showing \d+.\d+ of [\d,]+$"))

        # Page size + pagination
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$"), exact=True
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

    # ----------------------------------------------------------------- #
    # Navigation
    # ----------------------------------------------------------------- #
    def open_page(self):
        """Open the notifications list.

        Navigates directly to /notifications. The app renders both a desktop and
        a mobile header, so the header bell is duplicated and can be overlaid by
        a control from whatever page the shared session was last on; deep-linking
        is how the route is reached in the browser and is far more robust. The
        bell -> "View all" popover flow is covered separately by
        `open_from_bell()` from a known-clean page.
        """
        log.info("Opening the Notifications page")
        # Always do a full navigation: a client-side hop to /notifications loads
        # the page shell but not always the list, whereas a direct load renders
        # it reliably.
        self.page.goto(BASE_URL.rstrip("/") + "/notifications")
        self.page.wait_for_url(re.compile(r"/notifications"), timeout=15000)
        self.page.wait_for_load_state("domcontentloaded")
        self.heading.wait_for(state="visible", timeout=20000)
        # Make sure we start on the All tab with the list rendered.
        self.tab_all.click()
        # Wait for the list itself, not a fixed delay -- the rows load async.
        expect(self.list_ready).to_be_visible(timeout=20000)
        log.info("Notifications page loaded with %s row(s)", self.items.count())

    def open_from_bell(self):
        """Verify the header bell -> "View all" popover reaches the list.

        The bell lives in the global header on every page *except* the
        notifications list itself, so this starts from a clean page that shows
        it. The fresh navigation also clears any popover/dialog a prior test
        left open, so the bell is never sitting under an overlay.
        """
        log.info("Verifying the header bell -> 'View all' navigation")
        self.page.goto(BASE_URL.rstrip("/") + "/finance/overview")
        self.page.wait_for_load_state("load")
        self.bell.wait_for(state="visible", timeout=15000)
        self.bell.click()
        self.view_all.wait_for(state="visible", timeout=10000)
        self.view_all.click()
        # Landing on /notifications with its heading is the proof the bell got us
        # here; open_page() then loads the list from a normalised state.
        self.page.wait_for_url(re.compile(r"/notifications"), timeout=15000)
        self.heading.wait_for(state="visible", timeout=15000)
        log.info("Header bell navigation reached the notifications page")

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _poll(self, predicate, timeout_ms=15000, interval_ms=250):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        The list refetches on every tab and filter change, and the URL moves
        before the new rows land, so expected *content* is polled for rather
        than a fixed sleep. The deadline is wall-clock.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _total(self):
        """The current list's total from its footer, 0 on an empty state.

        None while neither is showing (mid-refetch).
        """
        try:
            if self.showing.count():
                text = self.showing.first.inner_text()
                return int(text.rsplit(" of ", 1)[1].replace(",", ""))
            if self.empty_any.count() and not self.items.count():
                return 0
        except Exception:
            # The footer re-renders as the refetch lands; a read that catches
            # it mid-repaint is simply "not settled yet".
            pass
        return None

    def _unread_badge(self):
        """The count on the Unread tab's badge (absent -> 0)."""
        digits = re.sub(r"\D", "", self.tab_unread.inner_text() or "")
        return int(digits) if digits else 0

    def _switch_tab(self, tab, state, expected_total=None):
        """Click a tab and wait until the list actually shows that tab.

        The tab is carried in the URL (`?state=unread` / `?state=read`, absent
        for All), but the rows only swap once the refetch lands -- and the
        previous tab's rows satisfy a bare "rows or empty state" wait, so two
        quick switches could otherwise leave the page on the wrong tab. When
        the tab's total is known it is polled for as the proof of arrival.
        """
        tab.click()
        if state:
            self.page.wait_for_url(re.compile(rf"[?&]state={state}\b"), timeout=10000)
        else:
            self.page.wait_for_url(lambda u: "state=" not in u, timeout=10000)
        if expected_total is not None:
            assert self._poll(lambda: self._total() == expected_total), (
                f"the {state or 'all'} tab should total {expected_total}, "
                f"shows {self._total()}"
            )
        expect(self.list_ready).to_be_visible(timeout=10000)

    # ----------------------------------------------------------------- #
    # Tabs
    # ----------------------------------------------------------------- #
    def browse_tabs(self):
        """Step through the tabs, checking their totals agree.

        The data is live, so any tab can be empty on a given run -- but the
        three totals must always reconcile: Unread matches its tab badge, and
        Read is whatever of All is not Unread.
        """
        assert self._poll(lambda: self._total() is not None), (
            "the All tab never showed a total"
        )
        total = self._total()
        # The badge paints from its own /notifications/counts call, which can
        # land after the list. Give it a chance to appear; a genuinely empty
        # unread inbox shows no badge, and the Unread tab's own total below
        # then confirms the 0.
        self._poll(lambda: self._unread_badge() > 0, timeout_ms=8000)
        unread = self._unread_badge()
        log.info("All tab: %s notification(s), badge says %s unread", total, unread)

        log.info("Switching to the Unread tab")
        self._switch_tab(self.tab_unread, "unread", unread)
        log.info("Unread tab: %s notification(s)", unread)

        log.info("Switching to the Read tab")
        self._switch_tab(self.tab_read, "read", total - unread)
        log.info("Read tab: %s notification(s)", total - unread)

        log.info("Switching back to the All tab")
        self._switch_tab(self.tab_all, None, total)

    # ----------------------------------------------------------------- #
    # Category filter (multi-select with Clear / Apply)
    # ----------------------------------------------------------------- #
    def filter_by_category(self, category="Socket alert", param="socket_alert"):
        """Filter to one category, then clear it again.

        Pinned to "Socket alert" because that is the category staging's
        notifications actually carry, so the filter is proven to *keep* the
        matching rows rather than only to empty the list. The row badge is not
        relied on: the backend gives socket_alert the same "Alert" pill as the
        separate "alert" category, so the badge cannot tell the two apart, and
        filtering on the plain "Alert" option returns nothing on staging.
        """
        before = self.items.count()

        log.info("Opening the Category filter and selecting %r", category)
        self.category_btn.click()
        self.dialog.wait_for(state="visible", timeout=8000)
        opt = self.page.get_by_role("option", name=category, exact=True)
        opt.wait_for(state="visible", timeout=8000)
        opt.click()
        expect(self.apply_btn).to_be_enabled(timeout=8000)
        self.apply_btn.click()
        self.dialog.wait_for(state="hidden", timeout=8000)
        # The URL updates before the refetch lands, so wait on the query and
        # then on the list itself, not a fixed delay.
        self.page.wait_for_url(re.compile(rf"[?&]category={param}\b"), timeout=10000)
        expect(self.items.first).to_be_visible(timeout=10000)
        expect(self.category_btn).to_contain_text(category, timeout=8000)
        log.info("Category %r applied, list now shows %s row(s)",
                 category, self.items.count())

        log.info("Clearing the Category filter")
        self.category_btn.click()
        self.dialog.wait_for(state="visible", timeout=8000)
        self.clear_btn.wait_for(state="visible", timeout=8000)
        self.clear_btn.click()
        # Clear now commits the empty selection and closes the popover in one
        # step; older builds kept it open and needed Apply as well.
        self.page.wait_for_timeout(400)
        if self.apply_btn.count() and self.apply_btn.is_enabled():
            self.apply_btn.click()
        if self.dialog.count():
            self.page.keyboard.press("Escape")
        self.page.wait_for_url(lambda u: "category=" not in u, timeout=10000)
        # Poll for the list to come back rather than sampling once.
        expect(self.items).to_have_count(before, timeout=15000)
        log.info("Category filter cleared, back to %s row(s)", before)

    # ----------------------------------------------------------------- #
    # Date filter (presets + Clear)
    # ----------------------------------------------------------------- #
    def filter_by_date(self, preset="Last 7 days"):
        before = self.items.count()

        log.info("Opening the Date filter and selecting %r", preset)
        self.date_btn.first.click()
        self.dialog.wait_for(state="visible", timeout=8000)
        opt = self.page.get_by_text(preset, exact=False).first
        opt.wait_for(state="visible", timeout=8000)
        opt.click()
        # The trigger is relabelled to the chosen preset and a removable
        # "Remove Date filter …" chip appears -- that chip is the proof the
        # filter is active (the trigger keeps the preset label even once cleared).
        expect(self.page.get_by_role("button", name=preset, exact=True)).to_be_visible(timeout=8000)
        expect(self.date_remove).to_have_count(1, timeout=8000)
        log.info("Date filter %r applied, list now shows %s row(s)",
                 preset, self.items.count())

        log.info("Clearing the Date filter via its remove chip")
        self.date_remove.first.click()
        # The chip disappears once the filter is removed and the full list returns.
        expect(self.date_remove).to_have_count(0, timeout=8000)
        expect(self.items).to_have_count(before, timeout=10000)
        log.info("Date filter cleared, back to %s row(s)", before)

    # ----------------------------------------------------------------- #
    # Item navigation
    # ----------------------------------------------------------------- #
    def open_notification_target(self):
        """Click a notification and follow it to its linked resource."""
        if self.items.count() == 0:
            log.info("No notifications to open -- skipping item navigation")
            return
        first = self.items.first
        first.wait_for(state="visible", timeout=10000)
        first.scroll_into_view_if_needed()
        title = first.get_attribute("aria-label")
        log.info("Opening the notification %r", title)
        # Opening a notification also marks it read (POST
        # /notifications/<id>/read) -- an irreversible write, since there is
        # no "mark as unread". That one request is aborted for the duration of
        # the click: navigation does not wait on it, so the target is still
        # reached while staging's unread inbox is left untouched. The attempt
        # is recorded, which still proves opening is wired to mark-as-read.
        mark_read = re.compile(r"/notifications/[^/?]+/read(\?|$)")
        attempted = []

        def block(route):
            attempted.append(route.request.url)
            route.abort()

        self.page.route(mark_read, block)
        try:
            first.click()
            # Each notification links to the resource it is about (a site,
            # device, network status page, …), so the URL leaves /notifications.
            self.page.wait_for_url(
                lambda u: "/notifications" not in u, timeout=15000
            )
        finally:
            self.page.unroute(mark_read, block)
        log.info("Notification opened its target: %s", self.page.url)
        assert attempted, (
            "opening a notification did not try to mark it read, though the "
            "Read tab promises opened notifications show up there"
        )
        log.info("Opening tried to mark it read (blocked, not sent): %s",
                 attempted[0])

        # Return to the list for the remaining steps.
        self.open_page()

    # ----------------------------------------------------------------- #
    # Page size + pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        current = (self.page_size.text_content() or "").strip()
        target = "20" if current != "20" else "50"
        total = self._total() or 0
        log.info("Switching the page size from %s to %s", current, target)
        self.page_size.click()
        opt = self.page.get_by_role("option", name=target, exact=True)
        opt.wait_for(state="visible", timeout=8000)
        opt.click()
        # Poll for the page to actually hold the new number of rows (capped by
        # the total) before switching back -- restoring while the first change
        # is still in flight can leave the list on the wrong size.
        expect(self.items).to_have_count(min(int(target), total), timeout=10000)
        log.info("Page size %s shows %s row(s)", target, self.items.count())

        log.info("Restoring the page size to %s", current)
        self.page_size.click()
        opt = self.page.get_by_role("option", name=current, exact=True)
        opt.wait_for(state="visible", timeout=8000)
        opt.click()
        expect(self.items).to_have_count(min(int(current), total), timeout=10000)

        if self.next_page.is_enabled():
            log.info("Paging forward and back through the notification list")
            # The footer's range ("Showing 11–20 of …") is what proves the page
            # actually turned; the old rows would satisfy a bare "rows" wait.
            size = int(current)
            self.next_page.click()
            self.page.wait_for_url(re.compile(r"[?&]page=2\b"), timeout=10000)
            expect(self.showing).to_contain_text(
                re.compile(rf"^Showing {size + 1}\D"), timeout=10000
            )
            self.prev_page.click()
            self.page.wait_for_url(re.compile(r"[?&]page=1\b"), timeout=10000)
            expect(self.showing).to_contain_text(
                re.compile(r"^Showing 1\D"), timeout=10000
            )
            expect(self.items).to_have_count(min(size, total), timeout=10000)
        else:
            log.info("Only one page of notifications, skipping pagination")

    # ----------------------------------------------------------------- #
    # Mark as read (IRREVERSIBLE -- validated, never clicked)
    # ----------------------------------------------------------------- #
    def mark_as_read(self):
        """Validate the mark-as-read controls without using them.

        There is no way to mark a notification unread again, so clicking either
        control would permanently change shared staging data (and "Mark all as
        read" would wipe the whole unread inbox in one go). Instead this checks,
        on the Unread tab, that every unread row carries its own per-item
        "Mark "<title>" as read" control and that "Mark all as read" is on offer
        and enabled. On an already-read inbox it checks the empty state instead.
        """
        log.info("Switching to the Unread tab to check the mark-as-read controls")
        self._switch_tab(self.tab_unread, "unread", self._unread_badge())

        unread = self.items.count()
        if unread == 0:
            log.info("No unread notifications -- checking the empty state only")
            expect(self.caught_up).to_be_visible()
        else:
            # One per-item control per unread row: a row without one could not
            # be marked read on its own.
            expect(self.item_mark_read).to_have_count(unread, timeout=8000)
            expect(self.mark_all_read).to_be_enabled(timeout=8000)
            log.info("%s unread row(s) each offer a per-item mark-as-read, and "
                     "'Mark all as read' is enabled (neither clicked -- "
                     "irreversible)", unread)

        self._switch_tab(self.tab_all, None)

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def notifications_page(self):
        # Load the page directly for the bulk of the run (a direct load renders
        # the list reliably), then cover the bell -> "View all" navigation last,
        # from a clean page, so its client-side hop never collides with the
        # direct loads the earlier steps depend on.
        self.open_page()
        self.browse_tabs()
        self.filter_by_category()
        self.filter_by_date()
        self.open_notification_target()
        self.paginate()
        self.mark_as_read()
        self.open_from_bell()
        log.info("Notifications workflow completed")
