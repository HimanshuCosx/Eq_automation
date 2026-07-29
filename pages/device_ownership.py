import logging
import re

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.device_ownership")

# The ownership correction rewrites historical CDRs and can move invoice
# totals, so the write test is pinned to one deliberately chosen device rather
# than "whichever row sorts first".  GIB012 (Capurro Garage) was picked by
# scanning the list for the smallest impact: it rewrites 1 CDR across 1 day,
# where other devices on page 1 rewrite 32-284.  The test flips its ownership
# and always flips it back.  If you repoint this at another device, check its
# CDR count in the Edit Ownership dialog first.
DEVICE_ID = "GIB012"

# The CPO / site used for the read-only filter and search checks. Both the CPO
# and the site are named "Capurro Garage" on staging, which is why one term
# exercises both filters.
CPO = "Capurro Garage"

REASON = "Automated regression check of the ownership correction flow"


class device_ownership:
    """Device Ownership (/admin/device-ownership).

    The admin screen that lists every charging device with its CPO, site,
    ownership type (Owned/Managed), verification state, socket count and
    created date, and lets an admin correct a device's ownership.

    The workflow exercises everything the page exposes: the table's column set,
    its sortable and non-sortable columns, the search box (by name and by CPMS
    ID, plus the no-match empty state), the CPO / Site / verification filters,
    "Clear all filters", the page-size selector and pagination (step and direct
    page jump), the Edit Ownership dialog's validation rules and both of its
    dismiss controls, and finally one real ownership correction.

    Everything except that last step is read-only. The correction itself is
    written and then *always* reverted -- in a `finally`, so even a failed
    assertion mid-flight still restores the device -- leaving staging exactly
    as it was found.
    """

    # The table's full column set, in order.
    EXPECTED_COLUMNS = [
        "Device", "Site", "Ownership", "Verified", "Sockets", "Created", "Actions",
    ]

    # Sortable columns and the `sort_by` query parameter each one drives. The
    # page pushes the sort into the URL, so the parameter is the unambiguous
    # proof that the click reached the backend rather than just reshuffling
    # what was already on screen.
    SORTABLE_COLUMNS = {
        "Device": "name",
        "Ownership": "ownership_type",
        "Verified": "verified",
        "Created": "created_at",
    }

    # Columns that carry no sort control -- clicking them must NOT reorder the
    # table. Used as a negative control so a regression that makes everything
    # sortable (or nothing) is caught.
    UNSORTABLE_COLUMNS = ["Site", "Sockets"]

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.do_link = page.get_by_role("link", name="Device Ownership")
        self.heading = page.locator("//h1[normalize-space()='Device Ownership']")

        # Search
        self.search = page.get_by_placeholder(
            "Search by name, manufacturer, model or CPMS ID..."
        )
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)

        # Filters. "Clear all filters" only renders once a filter is applied.
        self.cpo_filter = page.get_by_role("button", name="CPO", exact=True)
        self.site_filter = page.get_by_role("button", name="Site", exact=True)
        self.verified_filter = page.get_by_role("button", name="All", exact=True)
        self.clear_all_filters = page.get_by_role("button", name="Clear all filters")
        self.opt_unverified = page.get_by_role("option", name="Unverified only", exact=True)
        self.opt_all = page.get_by_role("option", name="All", exact=True)

        # Empty state, shown in place of the rows when nothing matches. It
        # offers its own "Clear filters" button to recover.
        self.empty_state = page.get_by_text("No charging devices found", exact=True)
        self.empty_clear_filters = page.get_by_role("button", name="Clear filters", exact=True)

        # Table + pagination
        self.table = page.locator("table")
        self.rows = page.locator("table tbody tr")
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")
        # Page-size trigger. Its label is the current size (10/20/50/100) and it
        # changes as the size changes, so match any of those rather than a fixed
        # "10" -- staging now defaults to 20. Pagination page buttons are named
        # "Go to page N", so this only ever matches the page-size trigger.
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|20|50|100)$")
        )

        # Edit Ownership dialog
        self.dialog = page.get_by_role("dialog")
        self.reason_input = page.get_by_placeholder(
            "Why is this correction needed? (10–500 chars)"
        )
        self.save_changes = self.dialog.get_by_role("button", name="Save Changes")
        self.dialog_cancel = self.dialog.get_by_role("button", name="Cancel", exact=True)
        # The dialog's own "X" control, a second way to dismiss it.
        self.dialog_close = self.dialog.get_by_role("button", name="Close", exact=True)

        # "Save Changes" only runs a dry run and then raises a second, final
        # confirmation -- nothing is written until "Yes, save changes".
        self.confirm_dialog = page.get_by_role("dialog").filter(
            has_text="Confirm ownership change"
        )
        self.confirm_save = page.get_by_role("button", name="Yes, save changes")

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _data_rows(self):
        """Real device rows only.

        When nothing matches, the table still renders a "No devices match
        <query>" row that quotes the search term back, so a plain text match
        would find a device in an empty table. Only real rows carry an edit
        button.
        """
        return self.page.locator("table tbody tr").filter(
            has=self.page.get_by_role("button", name="Edit ownership")
        )

    def _device_row(self, device_id=DEVICE_ID):
        """The single row for `device_id`, matched on the printed 'ID: xxx'."""
        return self._data_rows().filter(has_text=f"ID: {device_id}")

    def _row_order(self):
        """The device name + ID of each row, top to bottom.

        Sorting is asserted by watching this list change, which is engine- and
        locale-independent -- it never assumes a particular sort direction.
        """
        return [
            (r.locator("td").first.inner_text() or "").strip()
            for r in self._data_rows().all()
        ]

    def _header(self, col):
        """The clickable column header cell for `col`."""
        return self.page.locator(f"//th[normalize-space()='{col}']")

    def _column_index(self, col):
        """The 0-based position of column `col`, read from the live header.

        Resolved from the rendered header rather than hard-coded, so a column
        being reordered moves every column-level check with it instead of
        silently asserting against the wrong cell.
        """
        headers = [
            (h.inner_text() or "").strip()
            for h in self.page.locator("table thead th").all()
        ]
        assert col in headers, f"the table has no {col!r} column (has {headers})"
        return headers.index(col)

    def _cell(self, row, col):
        """The text of `row`'s `col` cell."""
        return (row.locator("td").nth(self._column_index(col)).text_content() or "").strip()

    def _column_values(self, col):
        """The `col` cell of every device row, top to bottom."""
        idx = self._column_index(col)
        return [
            (r.locator("td").nth(idx).inner_text() or "").strip()
            for r in self._data_rows().all()
        ]

    def _column_all_equal(self, col, expected):
        """True when every row's `col` cell is exactly `expected`.

        An empty list counts as true -- a filter that legitimately matches
        nothing has not returned a wrong row. Callers assert the row count
        separately where a non-empty result is required.
        """
        return all(v == expected for v in self._column_values(col))

    def _assert_column(self, col, expected, applied, exact=True):
        """Assert every row's `col` cell matches `expected` after a filter.

        This is the column-level filter check: rather than matching the filter
        term anywhere in the row -- which "Capurro Garage" would satisfy via the
        Device name even if the Site column were wrong -- it reads the one
        column the filter is supposed to drive and holds every row to it.
        """
        values = self._column_values(col)
        offenders = [
            v for v in values
            if (v != expected if exact else expected.lower() not in v.lower())
        ]
        assert not offenders, (
            f"{applied}: the {col} column should "
            f"{'be' if exact else 'contain'} {expected!r} on every row, "
            f"but {len(offenders)} of {len(values)} row(s) differ -> "
            f"{offenders[:3]}"
        )
        log.info("%s: all %s row(s) have %s %s %r",
                 applied, len(values), col, "=" if exact else "containing", expected)

    def _poll(self, predicate, timeout_ms=8000, interval_ms=150):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        The device table reloads asynchronously after a search, a sort or a
        write, so state is polled until it settles instead of racing a fixed
        sleep -- fast when it lands at once, patient when staging lags.
        """
        elapsed = 0
        while elapsed < timeout_ms:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
            elapsed += interval_ms
        return predicate()

    def _find_device(self, device_id=DEVICE_ID):
        row = self._device_row(device_id)
        # Under load the controlled search input can drop a fill before its
        # onChange is wired, leaving the table unfiltered; re-fill and re-poll a
        # few times before giving up so a dropped keystroke is not a failure.
        #
        # The retries are generous (4 x 10s) because this also runs straight
        # after an ownership write, when staging is still rewriting CDRs and can
        # take tens of seconds to serve the device list again. Giving up early
        # there aborts the test *before* the revert runs and leaves the device on
        # the wrong owner, so patience here is what keeps staging clean.
        for attempt in range(4):
            self.search.fill("")
            self.search.fill(device_id)
            if self._poll(lambda: row.count() == 1, timeout_ms=10000):
                break
            log.info("Device %s not listed yet (attempt %d/4) -- retrying the search",
                     device_id, attempt + 1)
        assert row.count() == 1, (
            f"expected exactly 1 row for device {device_id!r}, found {row.count()}"
        )
        return row.first

    def _read_ownership(self, device_id=DEVICE_ID):
        """Current ownership ("Owned"/"Managed") from the row's Ownership cell."""
        row = self._device_row(device_id).first
        value = self._cell(row, "Ownership")
        assert value in ("Owned", "Managed"), f"unexpected ownership value: {value!r}"
        return value

    def _ownership_value(self, device_id=DEVICE_ID):
        """Ownership cell value, or None if the row is not (yet) rendered.

        A non-asserting read for polling: after a write the table reloads and
        the row briefly disappears, so this returns None instead of raising
        until the row is back and carries a valid value.
        """
        row = self._device_row(device_id)
        if row.count() != 1:
            return None
        value = self._cell(row.first, "Ownership")
        return value if value in ("Owned", "Managed") else None

    def _only_unverified_listed(self):
        """True when the Verified column reads "Unverified" on every row.

        Read from the Verified column rather than the row text: "Unverified"
        contains "Verified", so a row-text check is easy to get backwards. An
        empty list counts as true.
        """
        return self._column_all_equal("Verified", "Unverified")

    def _clear_search(self):
        """Empty the search box via its Clear button, falling back to a fill."""
        if self.search_clear.count():
            self.search_clear.click()
        else:
            self.search.fill("")

    def open_page(self):
        log.info("Opening the Device Ownership page")
        self.do_link.click()
        self.page.wait_for_timeout(1500)
        self.heading.wait_for(state="visible", timeout=10000)
        # Wait for the first real device row before touching any control -- the
        # table renders skeleton placeholder rows while it loads.
        self._data_rows().first.wait_for(state="visible", timeout=20000)

    # ----------------------------------------------------------------- #
    # Table structure
    # ----------------------------------------------------------------- #
    def check_table_structure(self):
        """Confirm the table renders with its full column set and a full page.

        Catches a dropped or renamed column, and a page that renders its header
        but no data.
        """
        self.table.wait_for(state="visible", timeout=10000)
        headers = [
            (h.inner_text() or "").strip()
            for h in self.page.locator("table thead th").all()
        ]
        log.info("Table columns: %s", headers)
        assert headers == self.EXPECTED_COLUMNS, (
            f"unexpected column set: {headers} != {self.EXPECTED_COLUMNS}"
        )

        # A full first page means exactly `page size` device rows.
        size = int((self.page_size.text_content() or "0").strip())
        count = self._data_rows().count()
        assert count == size, f"expected {size} device rows on page 1, got {count}"
        log.info("Table shows %s device row(s) at page size %s", count, size)

        # Every row names its device and prints its CPMS ID underneath, and the
        # ownership cell always carries one of the two known values.
        first = self._data_rows().first
        assert re.search(r"ID:\s*\S+", first.inner_text() or ""), (
            "the first row does not print a CPMS 'ID: ...'"
        )
        assert self._cell(first, "Ownership") in ("Owned", "Managed"), (
            "the first row has no valid ownership value"
        )
        # Every column renders a value on every row -- an all-blank column is a
        # regression the row-level checks would otherwise walk straight past.
        for col in ("Device", "Site", "Ownership", "Verified", "Sockets", "Created"):
            blank = [i for i, v in enumerate(self._column_values(col)) if not v]
            assert not blank, f"the {col} column is empty on row(s) {blank[:5]}"
        log.info("All %s columns render a value on every row",
                 len(self.EXPECTED_COLUMNS) - 1)

    # ----------------------------------------------------------------- #
    # Search
    # ----------------------------------------------------------------- #
    def browse_search(self):
        """Search by name and by CPMS ID, and check the no-match empty state."""
        before = self._data_rows().count()
        log.info("Device list is showing %s device(s)", before)

        log.info("Searching devices for %r", CPO)
        self.search.fill(CPO)
        assert self._poll(lambda: 0 < self._data_rows().count() < before), (
            f"expected the search to narrow the list from {before}, "
            f"got {self._data_rows().count()}"
        )
        narrowed = self._data_rows().count()
        log.info("Search narrowed the list to %s device(s)", narrowed)
        # Every surviving row really does match the term -- checked against the
        # Device and Site columns specifically, not just the row text.
        self._assert_column("Device", CPO, f"search {CPO!r}", exact=False)
        self._assert_column("Site", CPO, f"search {CPO!r}")

        log.info("Clearing the search")
        self._clear_search()
        assert self._poll(lambda: self._data_rows().count() == before), (
            f"expected {before} device(s) after clearing the search, "
            f"got {self._data_rows().count()}"
        )

        # The box also searches CPMS IDs, which must resolve to exactly one row.
        log.info("Searching by CPMS ID %r", DEVICE_ID)
        self.search.fill(DEVICE_ID)
        assert self._poll(lambda: self._data_rows().count() == 1), (
            f"expected exactly 1 device for ID {DEVICE_ID!r}, "
            f"got {self._data_rows().count()}"
        )
        # The ID is printed in the Device column, under the device name.
        self._assert_column("Device", f"ID: {DEVICE_ID}", f"search {DEVICE_ID!r}",
                            exact=False)

        self._check_empty_state()

        assert self._poll(lambda: self._data_rows().count() == before), (
            f"expected {before} device(s) once the search is cleared, "
            f"got {self._data_rows().count()}"
        )
        log.info("Search cleared, back to %s device(s)", self._data_rows().count())

    def _check_empty_state(self):
        """A term that matches nothing shows the empty state, not a stale list."""
        term = "zzzz-no-such-device"
        log.info("Searching for %r to check the no-match empty state", term)
        self.search.fill(term)
        assert self._poll(lambda: self._data_rows().count() == 0), (
            f"expected no devices for {term!r}, got {self._data_rows().count()}"
        )
        self.empty_state.wait_for(state="visible", timeout=10000)
        # The empty state quotes the term back so the user can see what was searched.
        body = self.page.locator("table tbody").inner_text() or ""
        assert term in body, f"the empty state does not quote the search term: {body[:120]!r}"
        log.info("Empty state shown for %r", term)

        log.info("Recovering via the empty state's own 'Clear filters' button")
        self.empty_clear_filters.click()
        self.page.wait_for_timeout(1250)

    # ----------------------------------------------------------------- #
    # Column sorting
    # ----------------------------------------------------------------- #
    def sort_columns(self):
        """Sort every sortable column both ways, and prove the rest are not.

        Each sortable header must reorder the rows *and* push its own `sort_by`
        parameter into the URL, so a header that only looks clickable is caught.
        Site and Sockets are the negative control: clicking them must change
        nothing.
        """
        for col, param in self.SORTABLE_COLUMNS.items():
            before = self._row_order()
            log.info("Sorting the table by %r", col)
            self._header(col).click()
            assert self._poll(lambda b=before: self._row_order() != b, timeout_ms=10000), (
                f"sorting by {col!r} did not reorder the table"
            )
            self.page.wait_for_url(re.compile(rf"[?&]sort_by={param}\b"), timeout=10000)
            first_dir = self._row_order()

            log.info("Reversing the %r sort", col)
            self._header(col).click()
            assert self._poll(lambda a=first_dir: self._row_order() != a, timeout_ms=10000), (
                f"toggling the {col!r} sort did not change the order"
            )
            log.info("Column %-10s sorts both ways (sort_by=%s)", col, param)

        for col in self.UNSORTABLE_COLUMNS:
            log.info("Confirming the %r column is not sortable", col)
            before = self._row_order()
            self._header(col).click()
            # Give an (unexpected) reorder a chance to happen, then assert it did not.
            self.page.wait_for_timeout(1500)
            assert self._row_order() == before, f"{col} should not be sortable"

        # Drop the sort parameters so the rest of the run sees the default order.
        log.info("Resetting the table to its default sort order")
        self.page.goto(self.page.url.split("?")[0])
        self._data_rows().first.wait_for(state="visible", timeout=20000)

    # ----------------------------------------------------------------- #
    # Filters
    # ----------------------------------------------------------------- #
    def browse_filters(self):
        """Apply the CPO, verification and Site filters, then clear them all."""
        before = self._data_rows().count()

        log.info("Filtering by CPO %r", CPO)
        self.cpo_filter.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name=CPO, exact=True).click()
        assert self._poll(lambda: 0 < self._data_rows().count() < before), (
            f"the CPO filter should narrow the list from {before}, "
            f"got {self._data_rows().count()}"
        )
        log.info("CPO filter left %s device(s)", self._data_rows().count())
        # Every remaining device belongs to the chosen CPO -- read off the Site
        # column rather than the row text, so a device from another CPO that
        # merely mentions the name cannot pass.
        self._assert_column("Site", CPO, f"CPO filter {CPO!r}")
        # The other columns must still be intact -- a filter should narrow the
        # list, not blank out the cells it does not drive.
        self._assert_column("Device", CPO, f"CPO filter {CPO!r}", exact=False)
        for value in self._column_values("Ownership"):
            assert value in ("Owned", "Managed"), (
                f"the CPO filter left an unreadable Ownership cell: {value!r}"
            )

        cpo_filtered = self._data_rows().count()

        log.info("Filtering by verification status (Unverified only, then All)")
        self.verified_filter.click()
        self.page.wait_for_timeout(600)
        self.opt_unverified.click()
        # The filter button is relabelled to the selected value -- that is the
        # proof the filter applied.
        unverified_trigger = self.page.get_by_role(
            "button", name="Unverified only", exact=True
        )
        expect(unverified_trigger).to_be_visible()
        # The trigger relabels as soon as the click lands, but the table only
        # updates when the refetch comes back -- so poll for the *rows* to agree
        # with the filter rather than reading them straight away, or the stale
        # pre-filter rows get asserted against. Staging usually has every device
        # verified, so an empty list is a legitimate (and common) result.
        assert self._poll(self._only_unverified_listed, timeout_ms=10000), (
            "the 'Unverified only' filter still lists a device whose Verified "
            f"column is not 'Unverified' -> {self._column_values('Verified')[:3]}"
        )
        log.info("Unverified only shows %s device(s)", self._data_rows().count())
        if self._data_rows().count():
            self._assert_column("Verified", "Unverified", "Unverified only filter")

        unverified_trigger.click()
        self.page.wait_for_timeout(600)
        self.opt_all.click()
        expect(self.verified_filter).to_be_visible()
        assert self._poll(lambda: self._data_rows().count() == cpo_filtered,
                          timeout_ms=10000), (
            f"expected {cpo_filtered} device(s) back on the 'All' verification "
            f"filter, got {self._data_rows().count()}"
        )

        log.info("Clearing all filters")
        self.clear_all_filters.click()
        assert self._poll(lambda: self._data_rows().count() == before), (
            f"expected {before} device(s) after clearing the filters, "
            f"got {self._data_rows().count()}"
        )
        self.cpo_filter.wait_for(state="visible", timeout=5000)

        log.info("Filtering by site %r", CPO)
        self.site_filter.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name=CPO, exact=True).click()
        assert self._poll(lambda: 0 < self._data_rows().count() < before), (
            f"the Site filter should narrow the list from {before}, "
            f"got {self._data_rows().count()}"
        )
        log.info("Site filter left %s device(s)", self._data_rows().count())
        # The Site filter drives the Site column, so hold every row to it.
        self._assert_column("Site", CPO, f"Site filter {CPO!r}")

        self.clear_all_filters.click()
        assert self._poll(lambda: self._data_rows().count() == before), (
            f"expected {before} device(s) after clearing the site filter, "
            f"got {self._data_rows().count()}"
        )
        log.info("Filters cleared, back to %s device(s)", self._data_rows().count())

    # ----------------------------------------------------------------- #
    # Page size + pagination
    # ----------------------------------------------------------------- #
    def paginate(self):
        log.info("Paging forward and back through the device list")
        assert self.next_page.is_enabled(), "expected more than one page of devices"
        first_page = self._row_order()

        self.next_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=2"), timeout=10000)
        assert self._poll(lambda: self._row_order() and self._row_order() != first_page,
                          timeout_ms=10000), "page 2 shows the same devices as page 1"
        log.info("Page 2 shows %s different device(s)", self._data_rows().count())

        self.prev_page.click()
        self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=10000)
        assert self._poll(lambda: self._row_order() == first_page, timeout_ms=10000), (
            "going back did not restore page 1"
        )

        # Jumping straight to a numbered page, rather than stepping. These are
        # matched exactly: "Go to page 1" is a prefix of "Go to page 15", and a
        # substring match would resolve to both.
        page_3 = self.page.get_by_role("button", name="Go to page 3", exact=True)
        if page_3.count():
            log.info("Jumping straight to page 3")
            page_3.click()
            self.page.wait_for_url(re.compile(r"[?&]page=3"), timeout=10000)
            assert self._poll(lambda: self._row_order() and self._row_order() != first_page,
                              timeout_ms=10000), "page 3 shows the same devices as page 1"
            self.page.get_by_role("button", name="Go to page 1", exact=True).click()
            self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=10000)
            assert self._poll(lambda: self._row_order() == first_page, timeout_ms=10000), (
                "returning to page 1 did not restore it"
            )

        # Read the current size off the trigger and switch to a larger one, so
        # this holds whatever the page defaults to (10 or 20). The options are
        # 10 / 20 / 50 / 100.
        current = (self.page_size.text_content() or "").strip()
        before = self._data_rows().count()
        target = "50" if current != "50" else "100"
        log.info("Switching the page size from %s to %s", current, target)
        self.page_size.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name=target, exact=True).click()
        assert self._poll(lambda: self._data_rows().count() > before, timeout_ms=10000), (
            f"expected more than {before} rows after resizing to {target}, "
            f"got {self._data_rows().count()}"
        )
        log.info("Page size %s shows %s devices", target, self._data_rows().count())

        # Put it back so the rest of the run sees the original page size.
        log.info("Restoring the page size to %s", current)
        self.page_size.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name=current, exact=True).click()
        assert self._poll(lambda: self._data_rows().count() == before, timeout_ms=10000), (
            f"expected {before} rows after restoring page size {current}, "
            f"got {self._data_rows().count()}"
        )

    # ----------------------------------------------------------------- #
    # Edit dialog: validation only, nothing saved
    # ----------------------------------------------------------------- #
    def check_edit_validation(self):
        """Exercise every validation rule on the reason field, and both exits.

        Nothing is saved here: the dialog is opened twice and dismissed once via
        its "X" and once via Cancel, and the device's ownership is asserted
        unchanged afterwards.
        """
        log.info("Checking Edit Ownership validation on device %s", DEVICE_ID)
        row = self._find_device()
        before = self._read_ownership()

        row.get_by_role("button", name="Edit ownership").click()
        self.page.wait_for_timeout(900)
        dlg = self.dialog.first
        dlg.wait_for(state="visible", timeout=10000)

        # The dialog names the device it is about, and warns about the billing
        # impact of a correction -- both must be there before anything is typed.
        dialog_text = dlg.text_content() or ""
        assert f"ID: {DEVICE_ID}" in dialog_text, (
            f"the dialog is not for {DEVICE_ID} -> {dialog_text[:120]!r}"
        )
        assert "rewrites historical CDR ownership" in dialog_text, (
            "the dialog is missing its billing-impact warning"
        )

        # Reason is mandatory and must be 10-500 characters. These use expect()
        # rather than is_enabled() so they poll while the form revalidates
        # instead of racing it.
        expect(self.save_changes, "Save should be disabled with no reason").to_be_disabled()
        self.reason_input.fill("short")
        expect(
            self.save_changes,
            "Save should be disabled for a reason under 10 characters",
        ).to_be_disabled()

        # Exactly one character short of the minimum -- the boundary itself.
        self.reason_input.fill("x" * 9)
        expect(
            self.save_changes, "Save should be disabled at 9 characters"
        ).to_be_disabled()
        expect(dlg).to_contain_text("9 / 10–500")

        # And one over the maximum. The field does not truncate, it just refuses
        # to submit, so the counter overshoots and Save stays disabled.
        self.reason_input.fill("x" * 501)
        expect(
            self.save_changes, "Save should be disabled at 501 characters"
        ).to_be_disabled()
        expect(dlg).to_contain_text("501 / 10–500")

        self.reason_input.fill(REASON)
        expect(
            self.save_changes, "Save should be enabled for a valid reason"
        ).to_be_enabled()

        log.info("Dismissing the dialog with its Close (X) control")
        self.dialog_close.click()
        self.page.wait_for_timeout(750)
        assert self.dialog.count() == 0, "the dialog should close on X"

        # Re-open and dismiss the other way, to prove Cancel discards too.
        log.info("Re-opening the dialog and cancelling it")
        self._device_row().first.get_by_role("button", name="Edit ownership").click()
        self.page.wait_for_timeout(900)
        self.dialog.first.wait_for(state="visible", timeout=10000)
        self.reason_input.fill(REASON)
        expect(self.save_changes).to_be_enabled()
        self.dialog_cancel.click()
        self.page.wait_for_timeout(750)
        assert self.dialog.count() == 0, "dialog should close on Cancel"
        assert self._read_ownership() == before, "Cancel must not change ownership"
        log.info("Dialog dismissed both ways, %s is still %s", DEVICE_ID, before)

    # ----------------------------------------------------------------- #
    # Edit dialog: real ownership correction, always reverted
    # ----------------------------------------------------------------- #
    def _set_ownership(self, target, reason):
        """Open the dialog for DEVICE_ID and save `target` ownership."""
        row = self._find_device()
        row.get_by_role("button", name="Edit ownership").click()
        self.page.wait_for_timeout(900)
        dlg = self.dialog.first
        dlg.wait_for(state="visible", timeout=10000)

        # Guard: the dialog must be the one for our chosen device.
        dialog_text = dlg.text_content() or ""
        assert f"ID: {DEVICE_ID}" in dialog_text, (
            f"refusing to save: dialog is not for {DEVICE_ID} -> {dialog_text[:120]!r}"
        )

        # Read the dropdown's own label rather than reusing the table's value:
        # the table column and the dialog disagree after a correction (the
        # dialog reflects the API's current_ownership for the CDR date range),
        # and assuming they match is what broke the revert.
        dropdown = dlg.get_by_role("button", name=re.compile(r"^(Owned|Managed)$"))
        label = (dropdown.text_content() or "").strip()
        if label != target:
            dropdown.click()
            self.page.wait_for_timeout(600)
            self.page.get_by_role("option", name=target, exact=True).click()
            self.page.wait_for_timeout(400)

        # Fill the reason *after* switching type: Save only enables once both
        # are valid, and this waits for that rather than assuming it.
        self.reason_input.fill(reason)
        expect(self.save_changes).to_be_enabled()

        impact = re.search(r"rewrite\s+([\d,]+)\s+CDRs?\s+across\s+([\d,]+)\s+days?",
                           dlg.text_content() or "")
        if impact:
            log.info("Saving %s -> %s (rewrites %s CDRs across %s days)",
                     label, target, impact.group(1), impact.group(2))

        # First click only dry-runs the correction and raises the final
        # confirmation; the write happens on "Yes, save changes".
        self.save_changes.click()
        self.confirm_dialog.wait_for(state="visible", timeout=15000)
        log.info("Confirming the correction")
        self.confirm_save.click()
        self.page.wait_for_timeout(1500)
        self.confirm_dialog.wait_for(state="hidden", timeout=15000)
        self.page.wait_for_timeout(1000)

    def toggle_ownership_and_revert(self):
        """Flip the device's ownership, then always put it back."""
        self._find_device()
        original = self._read_ownership()
        target = "Managed" if original == "Owned" else "Owned"
        log.info("Device %s is currently %s; correcting it to %s",
                 DEVICE_ID, original, target)

        self._set_ownership(target, REASON)
        try:
            self._find_device()
            # The write rewrites CDRs server-side, so the table can take a moment
            # to reflect the new owner -- poll for it rather than reading once.
            assert self._poll(lambda: self._ownership_value() == target,
                              timeout_ms=15000), (
                f"ownership did not change to {target} "
                f"(still {self._ownership_value()!r})"
            )
            log.info("Ownership correction saved: %s is now %s", DEVICE_ID, target)
        finally:
            # Always restore, even if the assertion above failed, so a broken
            # run never leaves the device on the wrong owner.
            log.info("Reverting %s back to %s", DEVICE_ID, original)
            try:
                self._set_ownership(original, f"{REASON} (revert)")
            except Exception:
                # The revert itself failed (staging down mid-run, say). Say so
                # loudly and name the state to restore -- otherwise this is
                # buried under whatever error triggered the finally, and the
                # device is quietly left corrected.
                log.error(
                    "REVERT FAILED -- device %s may still be %s instead of %s. "
                    "Restore it by hand on the Device Ownership page.",
                    DEVICE_ID, target, original,
                )
                raise

        self._find_device()
        assert self._poll(lambda: self._ownership_value() == original,
                          timeout_ms=15000), (
            f"FAILED TO REVERT: {DEVICE_ID} left as {self._ownership_value()!r}, "
            f"expected {original}"
        )
        log.info("Device %s restored to %s", DEVICE_ID, original)

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def device_ownership_page(self):
        self.open_page()
        self.check_table_structure()
        self.browse_search()
        self.sort_columns()
        self.browse_filters()
        self.paginate()
        self.check_edit_validation()
        self.toggle_ownership_and_revert()
        log.info("Device Ownership workflow completed")
