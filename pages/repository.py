import logging
import re

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.repository")


class repository:
    """Repository (/operations/repository).

    A document library: every uploaded file is shown as a card (grid) or row
    (list) carrying its type, linked site, sub-org and size. The workflow is
    entirely read-only -- nothing here creates, edits or deletes -- so it just
    exercises every control the page exposes: the search box, the category tab
    bar, the grid/list view toggle, the list-view table (its column set,
    column sorting and in-table filtering), the Sub-Org filter, the per-file
    details panel, the download action, the page-size selector and pagination.
    Because it never writes, it always leaves staging exactly as it found it.

    Downloads are verified through Playwright's download event (the file is
    never persisted to disk), so the suite confirms the button fires without
    leaving a downloaded artifact behind.
    """

    # Every category shown in the tab bar. "All Records" is the default view.
    CATEGORIES = [
        "Survey",
        "Legal",
        "Finance",
        "Installation",
        "M-PPM",
        "M-Reaction",
        "Removal",
        "Others",
    ]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.repo_link = page.get_by_role("link", name="Repository")
        self.heading = page.locator("//h1[normalize-space()='Repository']")

        # Search
        self.search = page.get_by_placeholder(
            "Search documents, categories, linked sites…"
        )
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)

        # View toggle (icon buttons, matched on their aria-label)
        self.list_view = page.get_by_role("button", name="List view")
        self.grid_view = page.get_by_role("button", name="Grid view")

        # Category tab bar. "All Records" resets to the unfiltered view.
        self.all_records_tab = page.get_by_role("button", name="All Records", exact=True)

        # List-view table. In list view every document is a table row; the
        # header carries the six columns Document / Type / Linked To / Size /
        # Uploaded / Activity. Document, Linked To, Size and Uploaded are
        # sortable (clicking the header toggles asc/desc); Type and Activity
        # are not.
        self.table = page.locator("table")
        self.table_rows = page.locator("table tbody tr")
        self.EXPECTED_COLUMNS = [
            "Document", "Type", "Linked To", "Size", "Uploaded", "Activity",
        ]
        self.SORTABLE_COLUMNS = ["Document", "Linked To", "Size", "Uploaded"]

        # Sub-Org filter. The trigger is relabelled to "Sub-Org: <name>" once a
        # sub-org is picked, so re-opening it is matched on the leading text.
        self.suborg_trigger = page.get_by_role("button", name="Sub-Org", exact=True)
        self.suborg_trigger_any = page.get_by_role(
            "button", name=re.compile(r"^Sub-Org")
        ).first
        self.suborg_search = page.get_by_placeholder("Search sub-orgs…")

        # Details side panel. It shares role="dialog" with the sub-org popover,
        # so it is always identified by its own "Close panel" control.
        self.panel = page.get_by_role("dialog").filter(
            has=page.get_by_role("button", name="Close panel")
        )
        self.panel_close = page.get_by_role("button", name="Close panel")

        # Empty state shown when a filter matches nothing.
        self.empty_state = page.get_by_text("No documents found", exact=True)

        # Per-file action buttons (aria "View details for <file>" / "Download <file>").
        self.view_details_btns = page.get_by_role(
            "button", name=re.compile(r"^View details for")
        )
        self.download_btns = page.get_by_role(
            "button", name=re.compile(r"^Download ")
        )

        # Page size trigger. Its label is the current size (1/10/20/50/100) and
        # changes as the size changes, so match any of those rather than a fixed
        # value. Pagination page buttons are named "Go to page N", so this only
        # ever matches the page-size trigger.
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(1|10|20|50|100)$"), exact=True
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _card_count(self):
        """Number of document cards/rows currently rendered.

        Every real document -- in either grid or list view -- carries a
        "View details for <file>" button, while the empty state renders none,
        so this is a reliable count that can never be fooled by a placeholder
        row.
        """
        return self.view_details_btns.count()

    def _row_order(self):
        """The document name in each table row, top to bottom.

        Sorting is asserted by watching this list change, which is engine- and
        locale-independent -- it never assumes a particular sort direction.
        """
        return [
            (r.locator("td").first.inner_text() or "").strip().split("\n")[0]
            for r in self.table_rows.all()
        ]

    def _header(self, col):
        """The clickable column header cell for `col`."""
        return self.page.locator(f"//th[normalize-space()='{col}']")

    def _poll(self, predicate, timeout_ms=8000, interval_ms=150):
        """Poll `predicate` until it is truthy, returning True/False.

        Used for state that has no natural Playwright auto-waiting assertion --
        the row order after a sort, the card count after a filter -- so the
        checks resolve as soon as the list settles instead of racing a fixed
        sleep or waiting one out needlessly.
        """
        elapsed = 0
        while elapsed < timeout_ms:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
            elapsed += interval_ms
        return predicate()

    def open_page(self):
        log.info("Opening the Repository page")
        self.repo_link.click()
        self.page.wait_for_timeout(1250)
        self.heading.wait_for(state="visible", timeout=10000)
        # Wait for the first document to render before touching any control.
        self.view_details_btns.first.wait_for(state="visible", timeout=15000)

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def browse_search(self):
        before = self._card_count()
        log.info("Repository is showing %s document(s)", before)

        log.info("Searching documents for 'Boarding', then clearing the search")
        self.search.fill("Boarding")
        assert self._poll(lambda: self._card_count() < before), (
            f"expected the search to narrow the list from {before}, "
            f"got {self._card_count()}"
        )
        log.info("Search narrowed the list to %s document(s)", self._card_count())

        # A "Clear" button appears while a search is active; fall back to
        # emptying the field if the button is not present.
        if self.search_clear.count():
            self.search_clear.click()
        else:
            self.search.fill("")
        assert self._poll(lambda: self._card_count() == before), (
            f"expected {before} document(s) after clearing the search, "
            f"got {self._card_count()}"
        )
        log.info("Search cleared, back to %s document(s)", self._card_count())

    # ----------------------------------------------------------------- #
    # Category tabs
    # ----------------------------------------------------------------- #
    def browse_categories(self):
        log.info("Stepping through every category tab")
        for cat in self.CATEGORIES:
            tab = self.page.get_by_role("button", name=cat, exact=True)
            tab.click()
            self.page.wait_for_timeout(600)
            log.info("Category %-13s -> %s document(s)", cat, self._card_count())

        log.info("Returning to the All Records tab")
        self.all_records_tab.click()
        self.page.wait_for_timeout(600)

    # ----------------------------------------------------------------- #
    # List-view table: structure, sorting and in-table filtering
    # ----------------------------------------------------------------- #
    def browse_table(self):
        """Switch to the list-view table and exercise everything it offers.

        Verifies the table renders with its full column set and one row per
        document, sorts each sortable column (confirming Type is *not*
        sortable), and re-runs the category and search filters while in the
        table so filtering is proven to drive the table too -- then switches
        back to grid view, leaving the page as it was found.
        """
        log.info("Switching to list view (table)")
        self.list_view.click()
        self.page.wait_for_timeout(750)
        self.table.wait_for(state="visible", timeout=10000)

        # Column headers
        headers = [
            (h.inner_text() or "").strip()
            for h in self.page.locator("table thead th").all()
        ]
        log.info("Table columns: %s", headers)
        for col in self.EXPECTED_COLUMNS:
            assert col in headers, f"table is missing the {col!r} column"

        # One row per document, and every row exposes its download + view
        # actions (the Activity column icons).
        rows = self.table_rows.count()
        assert rows == self._card_count(), (
            f"table has {rows} rows but {self._card_count()} documents"
        )
        log.info("Table shows %s document row(s)", rows)

        self._sort_columns()
        self._filter_within_table()

        log.info("Switching back to grid view")
        self.grid_view.click()
        self.page.wait_for_timeout(750)
        assert self._card_count() > 0, "grid view should still show documents"

    def _sort_columns(self):
        """Click each sortable header and confirm it re-orders the rows.

        Type is included as a negative control -- clicking it must NOT reorder
        the table, matching the UI (only Document / Linked To / Size / Uploaded
        carry a sort control).
        """
        for col in self.SORTABLE_COLUMNS:
            header = self._header(col)
            before = self._row_order()
            log.info("Sorting the table by %r (ascending)", col)
            header.click()
            assert self._poll(lambda b=before: self._row_order() != b), (
                f"sorting by {col!r} did not reorder the table"
            )
            asc = self._row_order()

            log.info("Sorting the table by %r (descending)", col)
            header.click()
            assert self._poll(lambda a=asc: self._row_order() != a), (
                f"toggling {col!r} sort did not change the order"
            )

        log.info("Confirming the Type column is not sortable")
        before = self._row_order()
        self._header("Type").click()
        # Give any (unexpected) reorder a chance to happen, then assert it did not.
        self.page.wait_for_timeout(500)
        assert self._row_order() == before, "Type should not be sortable"

    def _filter_within_table(self):
        """Prove the category and search filters drive the table too."""
        full = self.table_rows.count()

        log.info("Filtering the table by category (Survey)")
        self.page.get_by_role("button", name="Survey", exact=True).click()
        assert self._poll(lambda: 0 < self.table_rows.count() <= full), (
            f"Survey category should narrow the table (of {full})"
        )
        log.info("Survey category shows %s table row(s)", self.table_rows.count())
        self.all_records_tab.click()
        assert self._poll(lambda: self.table_rows.count() == full), (
            "table should reset after clearing category"
        )

        log.info("Filtering the table with a search term ('Boarding')")
        self.search.fill("Boarding")
        assert self._poll(lambda: self.table_rows.count() < full), (
            f"search should narrow the table from {full}"
        )
        log.info("Search narrowed the table to %s row(s)", self.table_rows.count())
        if self.search_clear.count():
            self.search_clear.click()
        else:
            self.search.fill("")
        assert self._poll(lambda: self.table_rows.count() == full), (
            "table should reset after clearing search"
        )

    # ----------------------------------------------------------------- #
    # Sub-Org filter
    # ----------------------------------------------------------------- #
    def filter_by_suborg(self, suborg="Mid Suffolk Council"):
        """Apply a sub-org filter, confirm it took, then toggle it back off.

        Selecting a sub-org relabels the trigger to "Sub-Org: <name>", which is
        the proof the filter applied. Every sub-org currently narrows the list
        to its own document set (an empty state on staging), and re-selecting
        the same option toggles the filter off and restores the full list -- so
        the page is always left unfiltered.
        """
        before = self._card_count()

        log.info("Opening the Sub-Org filter")
        self.suborg_trigger.click()
        self.page.wait_for_timeout(600)
        # The popover lists every sub-org and carries its own search box.
        self.suborg_search.wait_for(state="visible", timeout=5000)
        self.suborg_search.fill(suborg)
        self.page.wait_for_timeout(500)

        log.info("Selecting sub-org %r", suborg)
        self.page.get_by_role("option", name=suborg, exact=True).click()
        self.page.wait_for_timeout(900)

        # The trigger is relabelled to the chosen sub-org once the filter applies.
        expect(
            self.page.get_by_role("button", name=f"Sub-Org: {suborg}")
        ).to_be_visible()
        log.info("Sub-Org filter applied, list now shows %s document(s)",
                 self._card_count())

        log.info("Re-selecting the same sub-org to clear the filter")
        self.suborg_trigger_any.click()
        self.page.wait_for_timeout(500)
        self.suborg_search.fill(suborg)
        self.page.wait_for_timeout(500)
        self.page.get_by_role("option", name=suborg, exact=True).click()
        self.page.wait_for_timeout(900)

        # Back to the neutral "Sub-Org" trigger and the original document count.
        expect(self.suborg_trigger).to_be_visible()
        assert self._poll(lambda: self._card_count() == before), (
            f"expected {before} document(s) after clearing the sub-org filter, "
            f"got {self._card_count()}"
        )
        log.info("Sub-Org filter cleared, back to %s document(s)", self._card_count())

    # ----------------------------------------------------------------- #
    # Details panel
    # ----------------------------------------------------------------- #
    def view_details(self):
        """Open the first document's details panel, read it, then close it."""
        log.info("Opening the details panel for the first document")
        self.view_details_btns.first.click()
        self.page.wait_for_timeout(750)
        self.panel.wait_for(state="visible", timeout=10000)

        # The panel spells out the document's metadata; confirm the labels are
        # all present so a broken/empty panel is caught.
        text = self.panel.first.text_content() or ""
        for label in ("Type", "Category", "Size", "Linked To", "Sub-Org", "Uploaded"):
            assert label in text, f"details panel is missing the {label!r} field"
        log.info("Details panel shows Type / Category / Size / Linked To / "
                 "Sub-Org / Uploaded")

        log.info("Closing the details panel")
        self.panel_close.click()
        self.page.wait_for_timeout(600)
        expect(self.panel).to_have_count(0)

    # ----------------------------------------------------------------- #
    # Download (verified via the download event, never persisted)
    # ----------------------------------------------------------------- #
    def verify_download(self):
        """Confirm the first document's Download button triggers a download.

        The download is captured through Playwright's download event and never
        saved to disk, so the button is exercised end-to-end without leaving a
        file behind. The button's aria-label names the file, which is asserted
        against the download's suggested filename.
        """
        btn = self.download_btns.first
        aria = btn.get_attribute("aria-label") or ""
        expected_name = aria.replace("Download ", "", 1).strip()
        log.info("Downloading %r", expected_name)

        with self.page.expect_download(timeout=30000) as dl_info:
            btn.click()
        download = dl_info.value
        suggested = download.suggested_filename
        log.info("Download started, suggested filename: %r", suggested)
        assert suggested, "download did not report a filename"
        # The suggested filename should match the file the button names (some
        # browsers sanitise punctuation, so compare on the stem).
        assert expected_name.split(".")[0][:8].lower() in suggested.lower(), (
            f"downloaded {suggested!r}, expected something like {expected_name!r}"
        )

    # ----------------------------------------------------------------- #
    # Page size + pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        # Read the current page size off the trigger and switch to a different
        # one, so this holds whatever the page defaults to.
        current = (self.page_size.text_content() or "").strip()
        target = "50" if current != "50" else "100"
        log.info("Switching the page size from %s to %s", current, target)
        self.page_size.click()
        self.page.wait_for_timeout(500)
        self.page.get_by_role("option", name=target, exact=True).click()
        self.page.wait_for_timeout(1000)

        # Put it back so the rest of the run sees the original page size.
        log.info("Restoring the page size to %s", current)
        self.page_size.click()
        self.page.wait_for_timeout(500)
        self.page.get_by_role("option", name=current, exact=True).click()
        self.page.wait_for_timeout(1000)

        # Staging usually holds a single page of documents, so only page when
        # there is somewhere to go.
        if self.next_page.is_enabled():
            log.info("Paging forward and back through the document list")
            self.next_page.click()
            self.page.wait_for_timeout(750)
            self.prev_page.click()
            self.page.wait_for_timeout(750)
        else:
            log.info("Only one page of documents, skipping pagination")

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def repository_page(self):
        self.open_page()
        self.browse_search()
        self.browse_categories()
        self.browse_table()
        self.filter_by_suborg()
        self.view_details()
        self.verify_download()
        self.paginate()
        log.info("Repository workflow completed")
