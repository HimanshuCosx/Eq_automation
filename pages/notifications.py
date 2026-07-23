import logging
import re

from playwright.sync_api import expect

from config import BASE_URL

log = logging.getLogger("eq_automation.notifications")


class notifications:
    """Notifications (/notifications).

    The page is reached from the header bell rather than the sidebar: the bell
    opens a popover carrying a "View all" link to the full list. The list
    groups notifications by day (TODAY / YESTERDAY / …) and is driven by a tab
    bar (All / Unread / Read), a search box, a multi-select Category filter and
    a Date filter, with page-size and pagination controls at the foot.

    Almost everything here is read-only and self-resetting -- every filter is
    cleared again so the list is left exactly as it was found. The exception is
    marking notifications read: the app exposes no "mark as unread", so that
    action is IRREVERSIBLE. It is therefore run last and only when unread
    notifications actually exist; when the inbox is already all-read the step
    logs and skips instead of failing, so the suite stays repeatable.
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

        # Search
        self.search = page.get_by_placeholder("Search notifications...")

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
        self.empty_any = page.get_by_text(
            re.compile(r"No .*notifications|caught up", re.I)
        ).first

        # A row or an empty state -- the list is "ready" once either is showing.
        self.list_ready = self.items.first.or_(self.empty_any)

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
    # Tabs
    # ----------------------------------------------------------------- #
    def browse_tabs(self):
        # Wait for each tab to settle into rows or an empty state -- the data is
        # live, so any tab can be empty on a given run.
        log.info("Switching to the Unread tab")
        self.tab_unread.click()
        expect(self.list_ready).to_be_visible(timeout=10000)
        log.info("Unread tab: %s row(s)", self.items.count())

        log.info("Switching to the Read tab")
        self.tab_read.click()
        expect(self.list_ready).to_be_visible(timeout=10000)
        log.info("Read tab: %s row(s)", self.items.count())

        log.info("Switching back to the All tab")
        self.tab_all.click()
        expect(self.list_ready).to_be_visible(timeout=10000)

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def browse_search(self):
        before = self.items.count()

        # A term that cannot match anything cleanly proves the search filters
        # the list down to the empty state. The input is debounced, so poll for
        # the empty list rather than sampling the count after a fixed wait.
        log.info("Searching for a term that matches nothing")
        self.search.fill("zzznomatchzzz")
        expect(self.items).to_have_count(0, timeout=10000)

        log.info("Clearing the search")
        self.search.fill("")
        expect(self.items).to_have_count(before, timeout=10000)
        log.info("Search cleared, back to %s row(s)", before)

    # ----------------------------------------------------------------- #
    # Category filter (multi-select with Clear / Apply)
    # ----------------------------------------------------------------- #
    def filter_by_category(self, category="Alert"):
        before = self.items.count()

        log.info("Opening the Category filter and selecting %r", category)
        self.category_btn.click()
        self.dialog.wait_for(state="visible", timeout=8000)
        opt = self.page.get_by_role("option", name=category, exact=True)
        opt.wait_for(state="visible", timeout=8000)
        opt.click()
        self.apply_btn.wait_for(state="visible", timeout=8000)
        self.apply_btn.click()
        # Wait for the filter to actually take effect (list re-renders).
        self.dialog.wait_for(state="hidden", timeout=8000)
        self.page.wait_for_timeout(600)
        log.info("Category %r applied, list now shows %s row(s)",
                 category, self.items.count())

        log.info("Clearing the Category filter")
        self.category_btn.click()
        self.dialog.wait_for(state="visible", timeout=8000)
        self.clear_btn.wait_for(state="visible", timeout=8000)
        self.clear_btn.click()
        self.page.wait_for_timeout(400)
        # Clear empties the selection; Apply commits the now-empty filter.
        if self.apply_btn.count():
            self.apply_btn.click()
        # Poll for the list to come back rather than sampling once.
        expect(self.items).to_have_count(before, timeout=10000)
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
        first.click()
        # Each notification links to the resource it is about (a site, device,
        # network status page, …), so the URL leaves /notifications.
        self.page.wait_for_url(
            lambda u: "/notifications" not in u, timeout=15000
        )
        log.info("Notification opened its target: %s", self.page.url)

        # Return to the list for the remaining steps.
        self.open_page()

    # ----------------------------------------------------------------- #
    # Page size + pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        current = (self.page_size.text_content() or "").strip()
        target = "20" if current != "20" else "50"
        log.info("Switching the page size from %s to %s", current, target)
        self.page_size.click()
        opt = self.page.get_by_role("option", name=target, exact=True)
        opt.wait_for(state="visible", timeout=8000)
        opt.click()
        self.page.wait_for_timeout(1000)

        log.info("Restoring the page size to %s", current)
        self.page_size.click()
        opt = self.page.get_by_role("option", name=current, exact=True)
        opt.wait_for(state="visible", timeout=8000)
        opt.click()
        expect(self.list_ready).to_be_visible(timeout=10000)

        if self.next_page.is_enabled():
            log.info("Paging forward and back through the notification list")
            self.next_page.click()
            expect(self.list_ready).to_be_visible(timeout=10000)
            self.prev_page.click()
            expect(self.list_ready).to_be_visible(timeout=10000)
        else:
            log.info("Only one page of notifications, skipping pagination")

    # ----------------------------------------------------------------- #
    # Mark as read (IRREVERSIBLE -- guarded, runs last)
    # ----------------------------------------------------------------- #
    def mark_as_read(self):
        """Exercise the mark-as-read actions when unread notifications exist.

        There is no way to mark a notification unread again, so this only acts
        when there is something unread to act on -- it marks one item read via
        its per-item control, confirms the unread list shrank, then clears the
        rest with "Mark all as read". On an already-read inbox it logs and
        skips so the suite still passes and never mutates state pointlessly.
        """
        log.info("Switching to the Unread tab to check for unread notifications")
        self.tab_unread.click()
        self.page.wait_for_timeout(900)

        unread = self.items.count()
        if unread == 0:
            log.info("No unread notifications -- skipping the (irreversible) "
                     "mark-as-read actions")
            expect(self.caught_up).to_be_visible()
            self.tab_all.click()
            self.page.wait_for_timeout(600)
            return

        log.info("%s unread notification(s); marking the first one as read", unread)
        self.item_mark_read.first.click()
        expect(self.items).to_have_count(unread - 1, timeout=8000)
        after_one = unread - 1
        log.info("One notification marked read, %s unread remaining", after_one)

        if after_one > 0:
            # Wait for the control to be actionable (it re-renders after the
            # per-item mark) before clicking, rather than racing it.
            expect(self.mark_all_read).to_be_enabled(timeout=8000)
            log.info("Marking all remaining notifications as read")
            self.mark_all_read.click()
            expect(self.items).to_have_count(0, timeout=8000)
            expect(self.caught_up).to_be_visible()
            log.info("Inbox is now all caught up")

        self.tab_all.click()
        self.page.wait_for_timeout(600)

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
        self.browse_search()
        self.filter_by_category()
        self.filter_by_date()
        self.open_notification_target()
        self.paginate()
        self.mark_as_read()
        self.open_from_bell()
        log.info("Notifications workflow completed")
