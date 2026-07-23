import logging
import re
import time

log = logging.getLogger("eq_automation.reports")


class reports:
    """Reports (/admin/reports).

    The workflow browses the list (search, category / frequency filters and the
    tab bar), opens a report read-only, then creates its own uniquely-named
    custom report, verifies it appears in the list and deletes it again -- so
    the suite leaves staging exactly as it found it. Every destructive action is
    scoped to that generated name (see `_own_row`), so a locator drift can never
    delete a report a real person owns. The delete confirmation dialog does not
    name the report, which is exactly why the row is search-scoped to our own
    name *before* the dialog is ever opened.
    """

    def __init__(self, page):
        self.page = page

        # Sidebar navigation. The breadcrumb also carries a "Reports" link on
        # detail pages, so pin this to the first match (the sidebar item).
        self.reports_link = page.get_by_role("link", name="Reports").first

        # List controls
        self.new_report_btn = page.get_by_role("button", name="New custom report")
        self.search = page.get_by_placeholder("Search reports…")

        # Filters. "Clear all filters" only renders once a filter is applied.
        # Both filter triggers are custom listbox buttons whose label changes to
        # the selected value, so the reset always goes back through the button's
        # original text.
        self.category_filter = page.get_by_role("button", name="All Categories")
        self.frequency_filter = page.get_by_role("button", name="Frequency", exact=True)
        self.clear_all_filters = page.get_by_role("button", name="Clear all filters")

        # Tabs. The accessible name carries a live count ("All (05)"), so these
        # are matched on the leading label as a (default) substring.
        self.tab_all = page.get_by_role("tab", name="All")
        self.tab_mine = page.get_by_role("tab", name="My Reports")
        self.tab_shared = page.get_by_role("tab", name="Shared")
        self.tab_favorites = page.get_by_role("tab", name="Favorites")
        self.tab_board_pack = page.get_by_role("tab", name="Board Pack")
        self.tab_scheduled = page.get_by_role("tab", name="Scheduled")

        # Table + pagination
        self.rows = page.locator("table tbody tr")
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Report builder (/admin/reports/new) -> "Save" opens the save dialog.
        self.save_btn = page.get_by_role("button", name="Save", exact=True)
        self.customise_btn = page.get_by_role("button", name="Customise")

        # Save report dialog
        self.save_dialog = page.get_by_role("dialog").filter(has_text="Save report")
        self.report_name_input = page.get_by_placeholder(
            "e.g. Q3 Revenue by Organization"
        )
        self.dialog_save_report = page.get_by_role(
            "button", name="Save report", exact=True
        )

        # Delete confirmation dialog (intentionally does NOT name the report).
        self.delete_dialog = page.get_by_role("dialog").filter(
            has_text="Delete this report"
        )
        self.dialog_delete = self.delete_dialog.get_by_role(
            "button", name="Delete", exact=True
        )
        self.delete_cancel = self.delete_dialog.get_by_role(
            "button", name="Cancel", exact=True
        )

        # Unique per run so the row we create can never collide with real data.
        self.report_name = f"Automation Report {int(time.time())}"

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _own_row(self, name):
        """The single table row for a report with this exact name.

        Matched on the row's name button rather than its text: an empty result
        renders a "No reports found" state instead of a row, and only real rows
        carry a clickable name button, so a text match can never find a report
        in an empty table.
        """
        return self.page.locator("table tbody tr").filter(
            has=self.page.get_by_role("button", name=name, exact=True)
        )

    def _find_by_name(self, name):
        """Search for `name` and return its row once the table has settled."""
        self.search.fill(name)
        # The table re-renders asynchronously after a search; this settle is the
        # critical sync point for the create/verify/delete assertions, so it is
        # kept generous (the same helper checks both "row present" and "row gone").
        self.page.wait_for_timeout(2000)
        return self._own_row(name)

    def open_page(self):
        log.info("Opening the Reports page")
        self.reports_link.click()
        self.page.wait_for_timeout(1250)
        self.new_report_btn.wait_for(state="visible", timeout=10000)

    # ----------------------------------------------------------------- #
    # Read-only browsing
    # ----------------------------------------------------------------- #
    def browse_list(self):
        log.info("Searching reports by name, then clearing the search")
        self.search.fill("Finance")
        self.page.wait_for_timeout(1000)
        log.info("Search narrowed the list to %s row(s)", self.rows.count())
        self.search.fill("")
        self.page.wait_for_timeout(750)

        log.info("Filtering by category (Financial), then clearing all filters")
        self.category_filter.click()
        self.page.wait_for_timeout(500)
        self.page.get_by_role("option", name="Financial", exact=True).click()
        self.page.wait_for_timeout(750)
        # Only rendered while at least one filter is active.
        self.clear_all_filters.click()
        self.page.wait_for_timeout(750)
        self.category_filter.wait_for(state="visible", timeout=5000)

        log.info("Filtering by frequency (Monthly), then clearing all filters")
        self.frequency_filter.click()
        self.page.wait_for_timeout(500)
        self.page.get_by_role("option", name="Monthly", exact=True).click()
        self.page.wait_for_timeout(750)
        self.clear_all_filters.click()
        self.page.wait_for_timeout(750)
        self.frequency_filter.wait_for(state="visible", timeout=5000)
        log.info("Filters cleared, back to %s row(s)", self.rows.count())

    def browse_tabs(self):
        log.info("Switching through the report tabs")
        for tab in (
            self.tab_mine,
            self.tab_shared,
            self.tab_favorites,
            self.tab_board_pack,
            self.tab_scheduled,
            self.tab_all,
        ):
            tab.click()
            self.page.wait_for_timeout(750)
        log.info("Back on the All tab with %s row(s)", self.rows.count())

    def view_report(self):
        """Open the first listed report read-only, then return to the list."""
        log.info("Opening a report read-only via the View action")
        self.open_page()
        self.rows.first.wait_for(state="visible", timeout=10000)
        self.rows.first.get_by_role("button", name="View report").click()
        self.page.wait_for_timeout(1250)
        # A report detail URL is /admin/reports/<uuid>.
        self.page.wait_for_url(
            re.compile(r"/admin/reports/[0-9a-f-]{36}"), timeout=15000
        )
        # The detail view exposes the builder controls the list does not.
        self.customise_btn.wait_for(state="visible", timeout=10000)
        log.info("Report detail loaded: %s", self.page.url)

    # ----------------------------------------------------------------- #
    # Create
    # ----------------------------------------------------------------- #
    def create_report(self):
        log.info("Creating a new custom report: %s", self.report_name)
        self.open_page()
        self.new_report_btn.click()
        self.page.wait_for_timeout(1250)
        self.page.wait_for_url(re.compile(r"/admin/reports/new"), timeout=15000)

        # The builder opens with a default Organization / Sessions grouping,
        # which is enough to save -- we keep it as-is and just name and store
        # the report.
        self.save_btn.click()
        self.page.wait_for_timeout(750)
        self.save_dialog.wait_for(state="visible", timeout=10000)

        self.report_name_input.fill(self.report_name)
        self.page.wait_for_timeout(400)

        # Category and Visibility are custom listbox-trigger buttons whose label
        # is the current value; scope them to the dialog so they can't collide
        # with the list's own category filter.
        log.info("Setting category (Financial) and visibility (My organisation)")
        self.save_dialog.get_by_role("button", name="Select a category").click()
        self.page.wait_for_timeout(400)
        self.page.get_by_role("option", name="Financial", exact=True).click()
        self.page.wait_for_timeout(400)

        self.save_dialog.get_by_role("button", name="Only me").click()
        self.page.wait_for_timeout(400)
        self.page.get_by_role("option", name="My organisation", exact=True).click()
        self.page.wait_for_timeout(400)

        self.dialog_save_report.click()
        self.page.wait_for_timeout(1500)
        # A successful save redirects to the saved report's detail page.
        self.page.wait_for_url(
            re.compile(r"/admin/reports/[0-9a-f-]{36}"), timeout=15000
        )
        log.info("Report saved, now on: %s", self.page.url)

    def verify_report_listed(self):
        log.info("Verifying the new report appears in the list")
        self.open_page()
        row = self._find_by_name(self.report_name)
        assert row.count() == 1, (
            f"expected exactly 1 row for {self.report_name!r}, found {row.count()}"
        )
        log.info("Found the new report in the list: %s", self.report_name)

    # ----------------------------------------------------------------- #
    # Delete (scoped to the report this run created)
    # ----------------------------------------------------------------- #
    def delete_report(self):
        self.open_page()
        row = self._find_by_name(self.report_name)
        assert row.count() == 1, (
            f"refusing to delete: expected exactly 1 row for {self.report_name!r}, "
            f"found {row.count()}"
        )

        log.info("Opening the delete confirmation, then cancelling it")
        row.first.get_by_role("button", name="Delete report").click()
        self.page.wait_for_timeout(750)
        self.delete_dialog.wait_for(state="visible", timeout=10000)
        self.delete_cancel.click()
        self.page.wait_for_timeout(750)
        assert self._own_row(self.report_name).count() == 1, "cancel should not delete"

        log.info("Deleting the report created by this run")
        row.first.get_by_role("button", name="Delete report").click()
        self.page.wait_for_timeout(750)
        self.delete_dialog.wait_for(state="visible", timeout=10000)
        self.dialog_delete.click()
        self.page.wait_for_timeout(1500)

        gone = self._find_by_name(self.report_name)
        assert gone.count() == 0, "report still listed after delete"
        log.info("Report deleted, staging left clean")

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def reports_page(self):
        self.open_page()
        self.browse_list()
        self.browse_tabs()
        self.view_report()
        self.create_report()
        self.verify_report_listed()
        self.delete_report()
        log.info("Reports workflow completed")
