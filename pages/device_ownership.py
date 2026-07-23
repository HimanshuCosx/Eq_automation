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

REASON = "Automated regression check of the ownership correction flow"


class device_ownership:
    """Device Ownership (/admin/device-ownership)."""

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

        # Table + pagination
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

    def _poll(self, predicate, timeout_ms=8000, interval_ms=150):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        The device table reloads asynchronously after a search or a write, so
        the row count is polled until it settles instead of racing a fixed
        sleep -- fast when the row appears at once, patient when staging lags.
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
        # couple of times before giving up so a dropped keystroke is not a
        # failure.
        for attempt in range(3):
            self.search.fill("")
            self.search.fill(device_id)
            if self._poll(lambda: row.count() == 1, timeout_ms=6000):
                break
            log.info("Device %s not listed yet (attempt %d) -- retrying the search",
                     device_id, attempt + 1)
        assert row.count() == 1, (
            f"expected exactly 1 row for device {device_id!r}, found {row.count()}"
        )
        return row.first

    def _read_ownership(self, device_id=DEVICE_ID):
        """Current ownership ("Owned"/"Managed") from the row's Ownership cell."""
        row = self._device_row(device_id).first
        value = (row.locator("td").nth(2).text_content() or "").strip()
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
        value = (row.first.locator("td").nth(2).text_content() or "").strip()
        return value if value in ("Owned", "Managed") else None

    def open_page(self):
        log.info("Opening the Device Ownership page")
        self.do_link.click()
        self.page.wait_for_timeout(1500)
        self.heading.wait_for(state="visible", timeout=10000)

    # ----------------------------------------------------------------- #
    # Read-only browsing
    # ----------------------------------------------------------------- #
    def browse_list(self):
        log.info("Searching devices, then clearing the search")
        self.search.fill("Capurro")
        self.page.wait_for_timeout(1250)
        log.info("Search narrowed the list to %s device(s)", self._data_rows().count())
        self.search_clear.click()
        self.page.wait_for_timeout(1000)

        log.info("Filtering by CPO")
        self.cpo_filter.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name="Capurro Garage", exact=True).click()
        self.page.wait_for_timeout(1000)
        log.info("CPO filter left %s device(s)", self._data_rows().count())

        log.info("Filtering by verification status (Unverified only, then All)")
        self.verified_filter.click()
        self.page.wait_for_timeout(600)
        self.opt_unverified.click()
        self.page.wait_for_timeout(1000)
        # The filter button is relabelled to the selected value.
        self.page.get_by_role("button", name="Unverified only", exact=True).click()
        self.page.wait_for_timeout(600)
        self.opt_all.click()
        self.page.wait_for_timeout(1000)

        log.info("Clearing all filters")
        self.clear_all_filters.click()
        self.page.wait_for_timeout(1000)
        self.cpo_filter.wait_for(state="visible", timeout=5000)

        log.info("Filtering by site")
        self.site_filter.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name="Capurro Garage", exact=True).click()
        self.page.wait_for_timeout(1000)
        self.clear_all_filters.click()
        self.page.wait_for_timeout(1000)

    def paginate(self):
        log.info("Paging forward and back through the device list")
        assert self.next_page.is_enabled(), "expected more than one page of devices"
        self.next_page.click()
        self.page.wait_for_timeout(1000)
        self.page.wait_for_url(re.compile(r"[?&]page=2"), timeout=10000)
        self.prev_page.click()
        self.page.wait_for_timeout(1000)
        self.page.wait_for_url(re.compile(r"[?&]page=1"), timeout=10000)

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
        self.page.wait_for_timeout(1250)
        count = self._data_rows().count()
        assert count > before, (
            f"expected more than {before} rows after resizing to {target}, got {count}"
        )
        log.info("Page size %s shows %s devices", target, count)

        # Put it back so the rest of the run sees the original page size.
        self.page_size.click()
        self.page.wait_for_timeout(600)
        self.page.get_by_role("option", name=current, exact=True).click()
        self.page.wait_for_timeout(1250)

    # ----------------------------------------------------------------- #
    # Edit dialog: validation only, nothing saved
    # ----------------------------------------------------------------- #
    def check_edit_validation(self):
        log.info("Checking Edit Ownership validation on device %s", DEVICE_ID)
        row = self._find_device()
        before = self._read_ownership()

        row.get_by_role("button", name="Edit ownership").click()
        self.page.wait_for_timeout(900)
        self.dialog.first.wait_for(state="visible", timeout=10000)

        # Reason is mandatory and must be 10-500 characters. These use expect()
        # rather than is_enabled() so they poll while the form revalidates
        # instead of racing it.
        expect(self.save_changes, "Save should be disabled with no reason").to_be_disabled()
        self.reason_input.fill("short")
        expect(
            self.save_changes,
            "Save should be disabled for a reason under 10 characters",
        ).to_be_disabled()
        self.reason_input.fill(REASON)
        expect(
            self.save_changes, "Save should be enabled for a valid reason"
        ).to_be_enabled()

        log.info("Cancelling the dialog without saving")
        self.dialog_cancel.click()
        self.page.wait_for_timeout(750)
        assert self.dialog.count() == 0, "dialog should close on Cancel"
        assert self._read_ownership() == before, "Cancel must not change ownership"

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
            self._set_ownership(original, f"{REASON} (revert)")

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
        self.browse_list()
        self.paginate()
        self.check_edit_validation()
        self.toggle_ownership_and_revert()
        log.info("Device Ownership workflow completed")
