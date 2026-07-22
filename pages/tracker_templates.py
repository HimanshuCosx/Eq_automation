import logging
import re
import time

log = logging.getLogger("eq_automation.tracker_templates")


class tracker_templates:
    """Tracker Templates (/admin/workflow-templates).

    The workflow creates its own uniquely-named template, edits it and then
    deletes it again, so the suite leaves staging exactly as it found it.
    Every destructive action is scoped to that generated name -- see
    `_own_row()` -- so a locator drift can never take out a template a real
    person owns.
    """

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.tt_link = page.get_by_role("link", name="Tracker Templates")
        self.heading = page.locator("//h1[normalize-space()='Tracker Templates']")

        # List controls
        self.add_template_btn = page.get_by_role("button", name="Add Template")
        self.search = page.get_by_placeholder("Search templates by name…")
        self.search_clear = page.get_by_role("button", name="Clear", exact=True)

        # Filters. "Clear all filters" only renders once a filter is applied.
        self.status_filter = page.get_by_role("button", name="All Status")
        self.deal_type_filter = page.get_by_role("button", name="Deal Type")
        self.clear_all_filters = page.get_by_role("button", name="Clear all filters")
        # "Active" is a substring of "Inactive", so this option needs exact=True.
        self.opt_active = page.get_by_role("option", name="Active", exact=True)
        self.opt_inactive = page.get_by_role("option", name="Inactive", exact=True)
        self.opt_all_status = page.get_by_role("option", name="All Status", exact=True)
        self.opt_owned_cpo = page.get_by_role("option", name="Owned CPO", exact=True)

        # Table + pagination
        self.table = page.locator("table")
        self.rows = page.locator("table tbody tr")
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Create / edit form (/admin/workflow-templates/new and /<id>/edit)
        self.name_input = page.get_by_placeholder("e.g. Preventive Maintenance Template")
        self.desc_input = page.get_by_placeholder("Describe what this template is used for")
        # "Compatible Deal Types" is now a set of checkboxes, each wrapped in a
        # <label> carrying the deal-type name -- clicking the label toggles it.
        self.deal_type_owned = page.locator("label").filter(has_text="Owned CPO")
        self.add_step_btn = page.get_by_role("button", name="Add Step")
        self.step_name = page.get_by_placeholder("Step name")
        self.step_desc = page.get_by_placeholder("Add step description")
        self.step_days = page.get_by_placeholder("e.g. 14")
        # Owner is a listbox-trigger button that shows the current owner as its
        # label; match on the visible "Ops Team" text rather than a role name.
        self.step_owner = page.locator("button", has_text="Ops Team")
        self.add_item_btn = page.get_by_role("button", name="Add Item")
        self.checklist_item = page.get_by_placeholder("Checklist item")
        self.cancel_btn = page.get_by_role("button", name="Cancel")
        self.save_draft_btn = page.get_by_role("button", name="Save As Draft")
        self.publish_btn = page.get_by_role("button", name="Publish Template")

        # Delete confirmation dialog
        self.dialog = page.get_by_role("dialog")
        self.dialog_delete = self.dialog.get_by_role("button", name="Delete", exact=True)
        self.dialog_cancel = self.dialog.get_by_role("button", name="Cancel", exact=True)

        # Leaving the form with unsaved edits raises an "Unsaved Changes" prompt.
        self.unsaved_dialog = page.get_by_role("dialog").filter(has_text="Unsaved Changes")
        self.stay_btn = page.get_by_role("button", name="Stay")
        self.discard_btn = page.get_by_role("button", name="Discard Changes")

        # Unique per run so the row we create can never collide with real data.
        self.template_name = f"Automation Template {int(time.time())}"
        self.edited_name = f"{self.template_name} (edited)"

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _own_row(self, name):
        """The single table row for the template this run created.

        Matched on the row's name link rather than its text: when nothing
        matches, the table still renders one "No templates match <query>" row
        that quotes the search term back, so a text match would find the
        template in an empty table. Only real rows carry a link.
        """
        return self.page.locator("table tbody tr").filter(
            has=self.page.get_by_role("link", name=name, exact=True)
        )

    def _find_by_name(self, name):
        """Search for `name` and return its row once the table has settled."""
        self.search.fill(name)
        self.page.wait_for_timeout(2500)
        return self._own_row(name)

    def open_page(self):
        log.info("Opening the Tracker Templates page")
        self.tt_link.click()
        self.page.wait_for_timeout(2500)
        self.heading.wait_for(state="visible", timeout=10000)

    # ----------------------------------------------------------------- #
    # Read-only browsing
    # ----------------------------------------------------------------- #
    def browse_list(self):
        log.info("Searching templates by name, then clearing the search")
        self.search.fill("Staging")
        self.page.wait_for_timeout(2000)
        log.info("Search narrowed the list to %s row(s)", self.rows.count())
        self.search_clear.click()
        self.page.wait_for_timeout(1500)

        log.info("Filtering by status (Active, then Inactive)")
        self.status_filter.click()
        self.page.wait_for_timeout(1000)
        self.opt_active.click()
        self.page.wait_for_timeout(1500)
        # The filter button is relabelled to the selected value.
        self.page.get_by_role("button", name="Active", exact=True).click()
        self.page.wait_for_timeout(1000)
        self.opt_inactive.click()
        self.page.wait_for_timeout(1500)

        log.info("Filtering by deal type, then clearing all filters")
        self.deal_type_filter.click()
        self.page.wait_for_timeout(1000)
        self.opt_owned_cpo.click()
        self.page.wait_for_timeout(1500)

        # Only rendered while at least one filter is active.
        self.clear_all_filters.click()
        self.page.wait_for_timeout(1500)
        self.status_filter.wait_for(state="visible", timeout=5000)
        log.info("Filters cleared, back to %s row(s)", self.rows.count())

        # Staging usually holds a single page of templates, so only page when
        # there is somewhere to go.
        if self.next_page.is_enabled():
            log.info("Paging forward and back through the template list")
            self.next_page.click()
            self.page.wait_for_timeout(1500)
            self.prev_page.click()
            self.page.wait_for_timeout(1500)
        else:
            log.info("Only one page of templates, skipping pagination")

    def cancel_add_template(self):
        """Open the create form, then back out via the unsaved-changes prompt.

        Cancelling a dirty form raises an "Unsaved Changes" dialog; this
        exercises both branches -- Stay keeps the form, Discard Changes returns
        to the list without creating anything.
        """
        log.info("Opening the Add Template form and cancelling out of it")
        self.add_template_btn.click()
        self.page.wait_for_timeout(2000)
        self.name_input.fill("Discarded by automation")
        self.page.wait_for_timeout(500)

        log.info("Cancelling a dirty form, then choosing Stay")
        self.cancel_btn.click()
        self.page.wait_for_timeout(1500)
        self.unsaved_dialog.wait_for(state="visible", timeout=10000)
        self.stay_btn.click()
        self.page.wait_for_timeout(1500)
        assert self.name_input.input_value() == "Discarded by automation", (
            "Stay should have kept the form as it was"
        )

        log.info("Cancelling again, this time discarding the changes")
        self.cancel_btn.click()
        self.page.wait_for_timeout(1500)
        self.discard_btn.click()
        self.page.wait_for_timeout(2500)
        self.heading.wait_for(state="visible", timeout=10000)
        self.add_template_btn.wait_for(state="visible", timeout=10000)

    # ----------------------------------------------------------------- #
    # Create
    # ----------------------------------------------------------------- #
    def create_template(self):
        log.info("Creating a new template: %s", self.template_name)
        self.add_template_btn.click()
        self.page.wait_for_timeout(2000)

        self.name_input.fill(self.template_name)
        self.desc_input.fill("Created by the automated regression suite")
        self.deal_type_owned.click()
        self.page.wait_for_timeout(500)

        log.info("Adding a step with a checklist item")
        self.add_step_btn.click()
        self.page.wait_for_timeout(1500)
        self.step_name.fill("Automated Step 1")
        self.step_desc.fill("Step added by the automated suite")
        self.step_days.fill("3")

        # Owner dropdown defaults to "Ops Team"; pick a different owner.
        self.step_owner.click()
        self.page.wait_for_timeout(1000)
        self.page.get_by_role("option", name="Internal Team", exact=True).click()
        self.page.wait_for_timeout(800)

        # A checklist item left blank fails validation and silently blocks the
        # save, so always give it text.
        self.add_item_btn.click()
        self.page.wait_for_timeout(800)
        self.checklist_item.fill("Automated checklist item")
        self.page.wait_for_timeout(500)

        log.info("Saving the template as a draft")
        self.save_draft_btn.click()
        self.page.wait_for_timeout(4000)
        # A successful save redirects to the edit screen for the new template.
        self.page.wait_for_url(re.compile(r"/workflow-templates/[0-9a-f-]+/edit"), timeout=15000)
        log.info("Template saved, now on: %s", self.page.url)

    def verify_template_listed(self):
        log.info("Verifying the new template appears in the list")
        self.open_page()
        row = self._find_by_name(self.template_name)
        assert row.count() == 1, (
            f"expected exactly 1 row for {self.template_name!r}, found {row.count()}"
        )
        log.info("Found the new template in the list: %s", self.template_name)

    # ----------------------------------------------------------------- #
    # Edit
    # ----------------------------------------------------------------- #
    def edit_template(self):
        log.info("Opening the new template for editing")
        self.page.get_by_role("link", name=self.template_name, exact=True).first.click()
        self.page.wait_for_timeout(2500)
        self.page.wait_for_url(re.compile(r"/workflow-templates/[0-9a-f-]+/edit"), timeout=15000)

        assert self.name_input.input_value() == self.template_name, (
            "edit form did not load the template we created"
        )

        log.info("Renaming the template to: %s", self.edited_name)
        self.name_input.fill(self.edited_name)
        self.page.wait_for_timeout(500)
        self.save_draft_btn.click()
        self.page.wait_for_timeout(4000)
        log.info("Edit saved")

    # ----------------------------------------------------------------- #
    # Delete (scoped to the template this run created)
    # ----------------------------------------------------------------- #
    def delete_template(self):
        self.open_page()
        row = self._find_by_name(self.edited_name)
        assert row.count() == 1, (
            f"refusing to delete: expected exactly 1 row for {self.edited_name!r}, "
            f"found {row.count()}"
        )

        log.info("Opening the delete confirmation, then cancelling it")
        row.first.get_by_role("button", name="Delete template").click()
        self.page.wait_for_timeout(1500)
        self.dialog_cancel.click()
        self.page.wait_for_timeout(1500)
        assert self._own_row(self.edited_name).count() == 1, "cancel should not delete"

        log.info("Deleting the template created by this run")
        row.first.get_by_role("button", name="Delete template").click()
        self.page.wait_for_timeout(1500)

        # Final guard: only confirm once the dialog names *our* template.
        dialog_text = self.dialog.first.text_content() or ""
        assert self.edited_name in dialog_text, (
            f"refusing to delete: confirmation names something else -> {dialog_text!r}"
        )
        self.dialog_delete.click()
        self.page.wait_for_timeout(3000)

        gone = self._find_by_name(self.edited_name)
        assert gone.count() == 0, "template still listed after delete"
        log.info("Template deleted, staging left clean")

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def tracker_templates_page(self):
        self.open_page()
        self.browse_list()
        self.cancel_add_template()
        self.create_template()
        self.verify_template_listed()
        self.edit_template()
        self.delete_template()
        log.info("Tracker Templates workflow completed")
