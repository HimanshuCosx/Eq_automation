import logging
import re

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
        self.suborg_dropdown = page.get_by_role("button", name="All sub-organisations")
        self.suborg_option = page.get_by_role("option", name=self.suborg_name)
        # Once a sub-org is picked the dropdown button is relabelled to that name.
        self.suborg_dropdown_filtered = page.get_by_role("button", name=self.suborg_name)
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)
        self.all_cpos_tab = page.locator("(//button[normalize-space()='All CPOs'])[1]")
        self.discrepancies_tab = page.locator("(//button[normalize-space()='Discrepancies only'])[1]")
        self.cpo_mulberry = page.get_by_text("Mulberry Homes", exact=True).first
        self.site_moulton = page.get_by_text("Moulton", exact=True).first
        # Sessions level (the deepest drill-down) is a flat table with its own
        # "All sessions" / "Discrepancies only" toggle -- there are no expandable
        # rows any more.
        self.sessions_all_tab = page.get_by_role("button", name="All sessions")

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
        self.suborg_dropdown.click()
        self.page.wait_for_timeout(500)
        self.suborg_option.click()
        self.page.wait_for_timeout(500)

        # The "Clear filters" button only renders inside the "No CPOs match the
        # current filters" empty state, so it is absent whenever the filter
        # matches rows. Re-selecting the already-selected option toggles the
        # sub-org filter off regardless of how many rows matched.
        self.suborg_dropdown_filtered.click()
        self.page.wait_for_timeout(500)
        self.suborg_option.click()
        self.page.wait_for_timeout(500)
        self.suborg_dropdown.wait_for(state="visible", timeout=5000)

        log.info("Searching for a CPO, then clearing the search")
        self.search.fill("east of england")
        self.page.wait_for_timeout(500)
        self.search_clear.click()
        self.page.wait_for_timeout(500)

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
