import re


class self_billing:
    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.sb_link = page.get_by_role("link", name="Self Billing")
        self.heading = page.locator("//h1[normalize-space()='Self Billing']")

        # Period (month) navigation
        self.prev_period = page.get_by_role("button", name="Previous period")
        self.next_period = page.get_by_role("button", name="Next period")
        # The period picker button shows dynamic text like "Jul 2026" and has no
        # aria-label, so match it by the 4-digit year it always contains.
        self.month_button = page.get_by_role("button", name=re.compile(r"20\d{2}"))

        # Period picker popover (Monthly / Quarterly / Yearly views)
        self.tab_monthly = page.get_by_role("button", name="Monthly")
        self.tab_quarterly = page.get_by_role("button", name="Quarterly")
        self.tab_yearly = page.get_by_role("button", name="Yearly")
        self.prev_year = page.get_by_role("button", name="Previous year")
        self.next_year = page.get_by_role("button", name="Next year")
        self.cal_feb = page.get_by_role("button", name="Feb", exact=True)
        self.this_month = page.get_by_role("button", name="This month")
        self.last_month = page.get_by_role("button", name="Last month")

        # Search
        self.search = page.get_by_placeholder("Search CPO by name or ID...")
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)

        # Table – expand/collapse rows
        self.collapse_all = page.get_by_role(
            "button", name=re.compile(r"^(Expand|Collapse) all rows$")
        )
        self.first_row_toggle = page.get_by_role(
            "button", name=re.compile(r"^(Expand|Collapse) row$")
        ).first

        # Pagination
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

    def _open_period_picker(self):
        # On Firefox/WebKit the first click on the period button only focuses it
        # (a focus/blur race) while Chromium opens the popover on the first click.
        # Click until the Monthly tab becomes visible so it works on every engine.
        for _ in range(4):
            if self.tab_monthly.is_visible():
                return
            self.month_button.click()
            self.page.wait_for_timeout(800)
        self.tab_monthly.wait_for(state="visible", timeout=5000)

    def self_billing_page(self):
        # Open the Self Billing screen
        self.sb_link.click()
        self.page.wait_for_timeout(3000)

        # Step through the previous / next period controls
        self.prev_period.click()
        self.page.wait_for_timeout(1500)
        self.next_period.click()
        self.page.wait_for_timeout(1500)

        # Open the period picker and exercise its Monthly / Quarterly / Yearly tabs
        self._open_period_picker()
        self.tab_quarterly.click()
        self.page.wait_for_timeout(1000)
        self.tab_yearly.click()
        self.page.wait_for_timeout(1000)
        self.tab_monthly.click()
        self.page.wait_for_timeout(1000)
        # Pick a past month (data reloads for the selected period)
        self.cal_feb.click()
        self.page.wait_for_timeout(2500)

        # Search a CPO, then clear the search
        self.search.fill("Snape")
        self.page.wait_for_timeout(2000)
        self.search_clear.click()
        self.page.wait_for_timeout(2000)

        # Drill into the first CPO row to reveal its sub-organisation rows
        self.first_row_toggle.click()
        self.page.wait_for_timeout(2000)

        # Collapse every expanded row again
        self.collapse_all.click()
        self.page.wait_for_timeout(1500)

        # Page through the CPO list
        self.next_page.click()
        self.page.wait_for_timeout(2000)
        self.prev_page.click()
        self.page.wait_for_timeout(2000)
