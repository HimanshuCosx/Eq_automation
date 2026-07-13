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
        self.tab_monthly = page.get_by_role("button", name="Monthly")
        self.prev_period = page.get_by_role("button", name="Previous period")
        self.next_period = page.get_by_role("button", name="Next period")
        self.cal_feb = page.get_by_role("button", name="Feb", exact=True)
        self.import_data_btn = page.get_by_role("button", name="Import Data")
        self.import_close = page.get_by_role("dialog").get_by_role("button", name="Close")
        self.search = page.get_by_placeholder("Search CPOs by name or ID...")
        self.suborg_dropdown = page.get_by_role("button", name="All sub-organisations")
        self.suborg_option = page.get_by_role("option", name="Plug-N-Go Gibraltar Limited")
        self.filter_delete = page.get_by_role("button", name="Clear all filters")
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)
        self.all_cpos_tab = page.locator("(//button[normalize-space()='All CPOs'])[1]")
        self.discrepancies_tab = page.locator("(//button[normalize-space()='Discrepancies only'])[1]")
        self.cpo_mulberry = page.get_by_text("Mulberry Homes", exact=True).first
        self.site_moulton = page.get_by_text("Moulton", exact=True).first
        self.expand_all_btn = page.get_by_role("button", name="Expand all")
        self.first_row_toggle = page.get_by_role(
            "button", name=re.compile(r"^(Expand|Collapse) row$")
        ).first

    def _open_calendar(self):
        # On Firefox/WebKit the first click on the period button only focuses it
        # (a focus/blur race) while Chromium opens the popover on the first click.
        # Click until the Monthly tab becomes visible so it works on every engine.
        for _ in range(4):
            if self.tab_monthly.is_visible():
                return
            self.calander.click()
            self.page.wait_for_timeout(800)
        self.tab_monthly.wait_for(state="visible", timeout=5000)

    def reconciliation_page(self):
        log.info("Opening the Reconciliation page")
        self.recon_link.click()
        self.page.wait_for_timeout(1000)

        log.info("Stepping through previous / next period controls")
        self.prev_period.click()
        self.next_period.click()

        log.info("Opening the calendar and selecting February")
        self._open_calendar()
        self.page.wait_for_timeout(1000)
        self.cal_feb.click()
        self.page.wait_for_timeout(1000)

        log.info("Opening and closing the Import Data dialog")
        self.import_data_btn.click()
        self.page.wait_for_timeout(1000)
        self.import_close.click()
        self.page.wait_for_timeout(1000)

        log.info("Switching between All CPOs and Discrepancies-only tabs")
        self.all_cpos_tab.click()
        self.page.wait_for_timeout(1000)
        self.discrepancies_tab.click()

        log.info("Filtering by sub-organisation, then clearing all filters")
        self.suborg_dropdown.click()
        self.page.wait_for_timeout(1000)
        self.suborg_option.click()
        self.page.wait_for_timeout(1000)
        self.filter_delete.click()

        log.info("Searching for a CPO, then clearing the search")
        self.search.fill("east of england")
        self.page.wait_for_timeout(1000)
        self.search_clear.click()
        self.page.wait_for_timeout(1000)

        # Drill into a CPO -> site -> sessions, then expand rows
        log.info("Drilling into CPO -> site -> sessions and expanding rows")
        self.cpo_mulberry.click()
        self.page.wait_for_timeout(1000)
        self.site_moulton.click()
        self.page.wait_for_timeout(1000)
        self.expand_all_btn.click()
        self.page.wait_for_timeout(1000)
        self.first_row_toggle.click()
        self.page.wait_for_timeout(1000)
        log.info("Reconciliation workflow completed")
