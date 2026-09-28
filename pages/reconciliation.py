import logging
import re
import time

log = logging.getLogger("eq_automation.reconciliation")


class reconciliation:
    def __init__(self, page):
        self.page = page
        # self.menu_btn = page.get_by_role("button", name="Open navigation menu")
        self.recon_link = page.get_by_role("link", name="Reconciliation")
        self.heading = page.locator("//h1[normalize-space()='Reconciliation']")
        # The period/calendar button shows dynamic text like "Jul 2026" and has no
        # aria-label, so match it by the 4-digit year it always contains instead of
        # a brittle class-based XPath.
        self.calander = page.get_by_role("button", name=re.compile(r"20\d{2}"))
        self.prev_period = page.get_by_role("button", name="Previous period")
        self.next_period = page.get_by_role("button", name="Next period")
        self.cal_feb = page.get_by_role("button", name="Feb", exact=True)
        self.import_data_btn = page.get_by_role("button", name="Import Data")
        self.import_close = page.get_by_role("dialog").get_by_role("button", name="Close")
        self.search = page.get_by_placeholder("Search CPOs by name or ID...")
        self.suborg_name = "Plug-N-Go Gibraltar Limited"
        # The filter trigger renders its "Sub-Organisation" label above its
        # current value, so its accessible name is the two run together
        # ("Sub-Organisation All sub-organisations"); match on the value half.
        self.suborg_dropdown = page.get_by_role("button", name="All sub-organisations")
        self.suborg_option = page.get_by_role("option", name=self.suborg_name)
        # Once a sub-org is picked the trigger is relabelled to
        # "Sub-Organisation <name>" and grows a nested "Remove <name>" button
        # (the trigger's own accessible name then ends in "Remove <name>" too).
        # That nested button is the dedicated way to drop the filter, so it is
        # what the workflow clears it with.
        self.suborg_remove = page.get_by_role(
            "button", name=f"Remove {self.suborg_name}", exact=True
        )
        # The CPO column header carries the row count ("CPO (5)").
        self.cpo_header = page.get_by_role("columnheader", name=re.compile(r"^CPO \(\d+\)$"))
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)
        self.all_cpos_tab = page.locator("(//button[normalize-space()='All CPOs'])[1]")
        self.discrepancies_tab = page.locator("(//button[normalize-space()='Discrepancies only'])[1]")
        self.cpo_mulberry = page.get_by_text("Mulberry Homes", exact=True).first
        self.site_moulton = page.get_by_text("Moulton", exact=True).first
        # Sessions level (the deepest drill-down) is a flat table with its own
        # "All sessions" / "Discrepancies only" toggle -- there are no expandable
        # rows any more.
        self.sessions_all_tab = page.get_by_role("button", name="All sessions")

    def _poll(self, predicate, timeout_ms=10000, interval_ms=200):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        The CPO table refetches asynchronously after a filter change, so its
        state is polled until it settles rather than racing a fixed sleep.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _cpo_count(self):
        """The row count printed in the CPO column header, or -1 mid-render."""
        try:
            m = re.search(r"\((\d+)\)", self.cpo_header.first.text_content(timeout=2000) or "")
        except Exception:
            return -1
        return int(m.group(1)) if m else -1

    def _open_calendar(self):
        # Clicking the period button toggles a month-picker popover. The popover
        # now shows a month grid (Jan..Dec under a year header) rather than the
        # old Monthly/Weekly tab set, so wait on a month cell instead. On
        # Firefox/WebKit the first click can only focus the button (a focus/blur
        # race), so click until the grid is showing so it works on every engine.
        for _ in range(4):
            if self.cal_feb.is_visible():
                return
            self.calander.click()
            self.page.wait_for_timeout(400)
        self.cal_feb.wait_for(state="visible", timeout=5000)

    def reconciliation_page(self):
        log.info("Opening the Reconciliation page")
        self.recon_link.click()
        self.page.wait_for_timeout(500)

        log.info("Stepping through previous / next period controls")
        self.prev_period.click()
        self.next_period.click()

        log.info("Opening the calendar and selecting February")
        self._open_calendar()
        self.page.wait_for_timeout(500)
        self.cal_feb.click()
        self.page.wait_for_timeout(500)

        log.info("Opening and closing the Import Data dialog")
        self.import_data_btn.click()
        self.page.wait_for_timeout(500)
        self.import_close.click()
        self.page.wait_for_timeout(500)

        log.info("Switching between All CPOs and Discrepancies-only tabs")
        self.all_cpos_tab.click()
        self.page.wait_for_timeout(500)
        self.discrepancies_tab.click()

        log.info("Filtering by sub-organisation, then clearing the filter")
        # Go back to All CPOs so the before/after counts compare like with like.
        self.all_cpos_tab.click()
        self.cpo_header.wait_for(state="visible", timeout=15000)
        before = self._cpo_count()
        self.suborg_dropdown.click()
        self.page.wait_for_timeout(500)
        self.suborg_option.click()
        # Single-select: the popover closes on pick and the trigger shows the
        # chosen sub-org with its own Remove button. Poll for the narrowed
        # list rather than trusting the relabel alone -- the refetch lands
        # after the trigger updates. In the pinned period (Feb 2026) only one
        # of the five CPOs belongs to this sub-org, so the list must shrink.
        self.suborg_remove.wait_for(state="visible", timeout=10000)
        assert self._poll(lambda: 0 < self._cpo_count() < before), (
            f"the {self.suborg_name!r} filter left {self._cpo_count()} CPO(s) "
            f"(was {before})"
        )
        log.info("Sub-org %r shows %s of %s CPO(s)",
                 self.suborg_name, self._cpo_count(), before)

        self.suborg_remove.click()
        self.suborg_dropdown.wait_for(state="visible", timeout=10000)
        assert self._poll(lambda: self._cpo_count() == before), (
            f"removing the sub-org filter should restore {before} CPO(s), "
            f"got {self._cpo_count()}"
        )

        log.info("Searching for a CPO, then clearing the search")
        self.search.fill("east of england")
        assert self._poll(lambda: self._cpo_count() == 1), (
            f"searching 'east of england' should leave 1 CPO, got {self._cpo_count()}"
        )
        self.search_clear.click()
        assert self._poll(lambda: self._cpo_count() == before), (
            f"clearing the search should restore {before} CPO(s), got {self._cpo_count()}"
        )

        # Drill into a CPO -> site -> sessions. The sessions view is now a flat
        # table (no expandable rows), so confirm it loaded and exercise its
        # All sessions / Discrepancies-only toggle instead.
        log.info("Drilling into CPO -> site -> sessions")
        self.cpo_mulberry.click()
        self.page.wait_for_timeout(750)
        self.site_moulton.click()
        self.page.wait_for_timeout(750)
        self.page.wait_for_url(re.compile(r"/sessions"), timeout=10000)

        log.info("Toggling the sessions Discrepancies-only / All sessions views")
        self.discrepancies_tab.click()
        self.page.wait_for_timeout(600)
        self.sessions_all_tab.click()
        self.page.wait_for_timeout(600)
        log.info("Reconciliation workflow completed")
