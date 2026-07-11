import re


class reconciliation:
    def __init__(self, page):
        self.page = page
        # self.menu_btn = page.get_by_role("button", name="Open navigation menu")
        self.recon_link = page.get_by_role("link", name="Reconciliation")
        self.heading = page.locator("//h1[normalize-space()='Reconciliation']")
        self.calander=page.locator("(//button[@class='inline-flex w-fit items-center justify-center whitespace-nowrap cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-teal focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-60 [&_svg]:pointer-events-none [&_svg]:shrink-0 hover:bg-bg-success hover:text-brand-teal disabled:bg-bg-subtle disabled:text-text-muted py-2 typo-title-1-m h-[34px] gap-2 rounded-sm border border-border-subtle bg-bg-surface px-[13px] text-[13.5px] font-semibold text-text-primary sm:gap-2 sm:px-[13px]'])[1]")
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

    def reconciliation_page(self):
        # self.menu_btn.hover()
        self.recon_link.click()
        self.page.wait_for_timeout(2000)
        self.prev_period.click()
        self.next_period.click()
        self.calander.click()
        self.page.wait_for_timeout(2000)
        self.cal_feb.click()
        self.page.wait_for_timeout(2000)
        self.import_data_btn.click()
        self.page.wait_for_timeout(1000)

        self.import_close.click()
        self.page.wait_for_timeout(1000)

        self.all_cpos_tab.click()
        self.page.wait_for_timeout(2000)
        self.discrepancies_tab.click()

        self.suborg_dropdown.click()
        self.page.wait_for_timeout(2000)
        self.suborg_option.click()
        self.page.wait_for_timeout(2000)

        self.filter_delete.click()

        self.search.fill("east of england")
        self.page.wait_for_timeout(2000)
        self.search_clear.click()
        self.page.wait_for_timeout(2000)

        # Drill into a CPO -> site -> sessions, then expand rows
        self.cpo_mulberry.click()
        self.page.wait_for_timeout(2000)
        self.site_moulton.click()
        self.page.wait_for_timeout(2000)
        self.expand_all_btn.click()
        self.page.wait_for_timeout(2000)
        self.first_row_toggle.click()
        self.page.wait_for_timeout(2000)
