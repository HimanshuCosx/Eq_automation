import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.command_center")

# The CPO and owner the filter checks are pinned to. Capurro Garage is a small
# CPO (1 site), so applying it visibly collapses every KPI -- which is what
# makes the filter's effect checkable rather than a no-op on a large estate.
CPO = "Capurro Garage"
OWNER = "Henbury Lodge"


class command_center:
    """Command Center (/operations/overview).

    The operations dashboard: eight hero KPI tiles, a live "Needs Action Now"
    alert feed, a criticality triage breakdown, the availability time-series
    chart, an OCPP error-code panel, an onboarding panel, and the Network
    Command Map. Three filters (Ownership, CPO and a rolling time window) drive
    the whole page, and a Refresh control re-pulls every panel.

    The workflow is entirely read-only -- the page offers no create, edit or
    delete anywhere -- so it always leaves staging exactly as it found it. It
    exercises every control the page exposes: all eight tiles and their
    tooltips, each of the three filters (applied, verified and cleared),
    Refresh, the alert feed and its "See All" link, the two drill-outs to
    Network Status and Notifications, the triage panel, both chart panels, and
    the map's legend, markers, zoom and search.
    """

    # The eight hero tiles, in the order they render, with the pattern each
    # one's value must match. Asserting the *shape* catches a tile that renders
    # a label with no number -- a common way for a dashboard to break -- without
    # pinning the test to figures that legitimately change every day.
    TILES = {
        "Network uptime": r"\d+\.\d+%",
        "Chargers down": r"\d+\s*/\s*\d+",
        "Failed sessions": r"(\d[\d,.]*%?|—)",
        "Onboarding": r"\d+",
        "Utilisation": r"\d+\.\d+%",
        "Live Sites": r"\d+",
        "At-Risk Sites": r"\d+",
        "Overdue": r"\d+",
    }

    # Every panel heading on the page.
    SECTIONS = [
        "Needs Action Now",
        "Criticality triage",
        "Performance & diagnostics",
        "Availability & failed sessions",
        "Top OCPP error codes",
        "Onboarding",
        "Network Command Map",
        "Geographic Distribution",
    ]

    # The rolling windows behind the time filter, and the `window_days` value
    # each one sends.
    WINDOWS = {
        "Last 7 days": 7,
        "Last 30 days": 30,
        "Last 90 days": 90,
        "Last 365 days": 365,
    }
    DEFAULT_WINDOW = "Last 30 days"

    # The five endpoints that back the dashboard. Refresh must re-pull all of
    # them, and a filter must re-pull them carrying its parameter.
    ENDPOINTS = [
        "hero-kpis",
        "kpis",
        "criticality-triage",
        "error-codes",
        "availability-timeseries",
    ]

    # The criticality triage buckets.
    CRITICALITIES = ["High", "Medium", "Low"]

    # The map legend's maintenance states (shared with the Operations Hub map).
    MAP_STATES = ["Overdue", "In Progress", "Scheduled", "Upcoming",
                  "Completed", "No Maintenance"]

    # Everything on the page that carries an info tooltip, mapped to a phrase
    # its text must contain. Note only the first four hero tiles have one --
    # Utilisation, Live Sites, At-Risk Sites and Overdue deliberately do not --
    # so this is the authoritative list rather than "one per tile".
    #
    # The phrases matter: without them a tooltip check passes as long as *some*
    # text appears, which hides an icon wired to the wrong explanation.
    TOOLTIP_SECTIONS = {
        "Network uptime": "Usable device-time",
        "Chargers down": "faulted or offline",
        "Failed sessions": "failed to start or complete",
        "Onboarding": "mid-onboarding",
        "Needs Action Now": "operations notifications",
        "Criticality triage": "highest deal criticality",
        "Availability & failed sessions": "Daily unavailable chargers",
        "Top OCPP error codes": "StatusNotification",
        "Needs Action": "onboarding notifications",
        "Geographic Distribution": "maintenance events",
    }

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.cc_link = page.get_by_role("link", name="Command Center")
        self.heading = page.locator("//h1[normalize-space()='Command Center']")

        # Every KPI tile, alert card and the map panel share this card class;
        # tiles are picked out of it by their label.
        self.cards = page.locator("div.bg-card-bg")

        # Header controls
        self.refresh = page.get_by_role("button", name="Refresh data")
        self.tooltip_buttons = page.get_by_role("button", name="More information")

        # Filters. Each trigger relabels itself to "<Filter>: <value>" once a
        # value is picked, which is how the applied state is asserted.
        self.ownership_filter = page.get_by_role("button", name="Ownership", exact=True)
        self.cpo_filter = page.get_by_role("button", name="CPO", exact=True)
        self.window_filter = page.get_by_role(
            "button", name=re.compile(r"^Last \d+ days$")
        )
        self.clear_filters = page.get_by_role("button", name="Clear all filters")

        # Needs Action Now feed. Every alert card is a button whose accessible
        # name is the alert itself ("Offline (prolonged): ...").
        self.alert_cards = page.get_by_role(
            "button", name=re.compile(r"^(Offline|Out of service|Update|Faulted)")
        )
        self.see_all = page.get_by_role("button", name="See All")

        # Map
        self.map_search = page.get_by_placeholder(
            "Search Sites, charging devices, sockets, IDs, locations.."
        )
        self.zoom_in = page.get_by_role("button", name="Zoom in")
        self.zoom_out = page.get_by_role("button", name="Zoom out")

        # Geographic Distribution: a drill-down list overlaid on the map. Each
        # entry is a clickable card stating "ID: n", a site count and a
        # maintenance state; clicking one descends a level.
        self.geo_cards = page.get_by_role("button", name=re.compile(r"ID:\s*\d+"))
        # Once below the top level the panel shows the parent as a header card;
        # clicking it ascends one level. It is the only "back" control.
        self.geo_crumb = page.get_by_role(
            "button", name=re.compile(r"Organization$")
        )

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _poll(self, predicate, timeout_ms=15000, interval_ms=250):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        The dashboard pulls five endpoints in parallel and each panel paints as
        its own call lands, so state is polled until it settles rather than
        raced with a fixed sleep. The deadline is wall-clock, not a count of
        sleeps -- some predicates here read several tiles and take a moment.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _tile(self, label):
        """The KPI tile card carrying `label`.

        "Overdue" is both a tile and a map-legend entry, so this takes the
        first match -- the tiles render above the map.
        """
        return self.cards.filter(
            has=self.page.get_by_text(label, exact=True)
        ).first

    def _tile_text(self, label):
        try:
            return (self._tile(label).inner_text() or "").strip()
        except Exception:
            # The card re-renders as its endpoint lands; treat a read that
            # lands mid-repaint as "not settled yet".
            return ""

    def _tile_value(self, label):
        """The tile's value, i.e. its text with the label stripped off."""
        return self._tile_text(label).replace(label, "", 1).strip()

    def _tiles_loaded(self):
        """True once every tile has painted a value matching its pattern."""
        for label, pattern in self.TILES.items():
            if not re.search(pattern, self._tile_value(label)):
                return False
        return True

    def _watch_api(self):
        """Record (endpoint, status, url) for every dashboard call.

        Filters and Refresh are asserted on the API rather than only on the
        rendered numbers: the figures are live and legitimately identical
        between two windows, so "did a number change?" is not a reliable signal,
        whereas "did the panel refetch with this parameter and get a 200?" is.
        """
        seen = []

        def on_response(response):
            if "/operations/overview/" in response.url:
                endpoint = response.url.split("/overview/")[-1].split("?")[0]
                seen.append((endpoint, response.status, response.url))

        self.page.on("response", on_response)
        return seen, on_response

    def _wait_for_endpoints(self, seen, expect_param=None, timeout_ms=25000):
        """Wait until every dashboard endpoint has answered, then assert 200s."""
        assert self._poll(
            lambda: {e for e, _, _ in seen} >= set(self.ENDPOINTS),
            timeout_ms=timeout_ms,
        ), (
            f"only {sorted({e for e, _, _ in seen})} refetched, "
            f"expected all of {self.ENDPOINTS}"
        )
        bad = [(e, s) for e, s, _ in seen if s != 200]
        assert not bad, f"dashboard endpoints returned non-200: {bad}"
        if expect_param:
            missing = [
                e for e in self.ENDPOINTS
                if not any(e == ep and expect_param in url for ep, _, url in seen)
            ]
            assert not missing, (
                f"{missing} did not carry {expect_param!r} in the request"
            )

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        log.info("Opening the Command Center")
        self.cc_link.click()
        self.page.wait_for_url(
            re.compile(r"/operations/overview"), timeout=20000
        )
        self.heading.wait_for(state="visible", timeout=20000)
        # The tiles paint as their endpoints land; wait for real numbers rather
        # than the skeleton state before touching any control.
        assert self._poll(self._tiles_loaded, timeout_ms=40000), (
            "the KPI tiles never finished loading: "
            f"{ {l: self._tile_value(l) for l in self.TILES} }"
        )
        log.info("Command Center loaded with all %s KPI tiles populated",
                 len(self.TILES))

    # ----------------------------------------------------------------- #
    # Hero KPI tiles
    # ----------------------------------------------------------------- #
    def check_hero_tiles(self):
        """Every tile renders, and its value has the right shape."""
        for label, pattern in self.TILES.items():
            tile = self._tile(label)
            expect(tile).to_be_visible()
            value = self._tile_value(label)
            assert re.search(pattern, value), (
                f"the {label!r} tile shows {value!r}, which does not match "
                f"the expected shape {pattern!r}"
            )
            log.info("Tile %-16s -> %s", label, value.replace("\n", " "))

        # The two window-scoped tiles state the window they cover.
        for label in ("Network uptime", "Failed sessions"):
            assert self.DEFAULT_WINDOW in self._tile_text(label), (
                f"the {label!r} tile does not state the {self.DEFAULT_WINDOW!r} window"
            )
        log.info("All %s hero tiles render a well-formed value", len(self.TILES))

    def check_info_tooltips(self):
        """Hover every info icon on the page and read its tooltip.

        Covers all ten -- the four hero tiles that have one, plus the six
        section headers (Needs Action Now, Criticality triage, the two chart
        panels, the onboarding feed and Geographic Distribution). Each is
        required to produce real explanatory text, so an icon wired to an empty
        or missing tooltip is caught.
        """
        count = self.tooltip_buttons.count()
        assert count == len(self.TOOLTIP_SECTIONS), (
            f"expected {len(self.TOOLTIP_SECTIONS)} info icons, found {count}"
        )

        tooltip = self.page.get_by_role("tooltip")
        seen = []
        for index in range(count):
            button = self.tooltip_buttons.nth(index)
            section = self._tooltip_section(button)

            # Wait for the previous tooltip to leave the DOM before hovering the
            # next one. It lingers while it fades, and reading through that
            # window returns the *previous* icon's text -- which silently shifts
            # every result by one and still passes a "is there any text?" check.
            assert self._poll(lambda: tooltip.count() == 0, timeout_ms=8000), (
                f"the previous tooltip never closed before hovering {section!r}"
            )
            # Scroll the icon into view and let the sidebar collapse before
            # reaching for it. The nav is fixed to the left edge and widens
            # from 125px to 294px while the pointer is anywhere over it, which
            # covers the leading edge of the lower panels -- Playwright then
            # reports the hover as intercepted by <nav> and retries until it
            # times out, which is exactly how this check used to fail.
            button.scroll_into_view_if_needed()
            self._park_pointer()
            button.hover()
            assert self._poll(lambda: tooltip.count() > 0, timeout_ms=8000), (
                f"the info icon on {section!r} raised no tooltip"
            )
            text = (tooltip.first.inner_text() or "").strip()

            expected = self.TOOLTIP_SECTIONS.get(section)
            assert expected, f"unexpected info icon next to {section!r}"
            assert expected.lower() in text.lower(), (
                f"the {section!r} tooltip should mention {expected!r} but reads "
                f"{text!r} -- the icons and their explanations are mismatched"
            )
            log.info("Tooltip %-30s -> %s", section, text[:64])
            seen.append(section)
            self._dismiss_tooltip()

        missing = [s for s in self.TOOLTIP_SECTIONS if s not in seen]
        assert not missing, f"no info tooltip found for {missing} (saw {seen})"
        log.info("All %s info tooltips explain the right section", count)

    def _park_pointer(self):
        """Move the pointer somewhere inert and let the sidebar collapse.

        The nav is fixed to the left edge and widens from 125px to 294px while
        hovered, covering the leading edge of everything below it. Any click or
        hover aimed there is then intercepted by <nav> and retried until it
        times out. The middle of the page header is clear of both the sidebar
        and every tooltip trigger.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] // 2, 30)
        # The width transition is 500ms; give it time to finish retracting.
        self.page.wait_for_timeout(600)

    def _dismiss_tooltip(self):
        """Close the open tooltip and let it leave the DOM.

        Hovering a different element is not enough -- the pointer has to leave
        the trigger entirely -- so this parks the mouse somewhere inert and
        presses Escape, which these tooltips honour.

        The parking spot matters: the corner (0, 0) sits over the sidebar, which
        expands on hover and then covers the icons further down the page, so
        every later hover is intercepted and times out. Middle of the page
        header is clear of the sidebar and of every tooltip trigger.
        """
        self._park_pointer()
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(250)

    def _tooltip_section(self, button):
        """The heading/label an info icon sits next to."""
        return button.evaluate("""e => {
            let n = e.closest('div');
            for (let i = 0; i < 4 && n; i++) {
                if (n.innerText && n.innerText.trim()) break;
                n = n.parentElement;
            }
            return n ? n.innerText.trim().split('\\n')[0] : '';
        }""")

    # ----------------------------------------------------------------- #
    # Sections
    # ----------------------------------------------------------------- #
    def check_sections(self):
        """Every panel heading is present."""
        body = self.page.locator("body").inner_text() or ""
        missing = [s for s in self.SECTIONS if s not in body]
        assert not missing, f"the dashboard is missing the panel(s) {missing}"
        log.info("All %s dashboard panels render", len(self.SECTIONS))

    def check_criticality_triage(self):
        """The triage panel breaks sites down by criticality."""
        body = self.page.locator("body").inner_text() or ""
        start = body.find("Criticality triage")
        panel = body[start:start + 400]
        for level in self.CRITICALITIES:
            assert level in panel, f"the triage panel is missing the {level!r} bucket"
        # Each bucket states a site count, and the panel totals the rest.
        assert re.search(r"\d+\s*\n?\s*Sites", panel), (
            "the triage panel shows no site counts"
        )
        assert re.search(r"\d+ uncategorized site", panel), (
            "the triage panel does not report uncategorized sites"
        )
        log.info("Criticality triage shows High / Medium / Low buckets with counts")

    def check_charts(self):
        """The availability chart and the OCPP error-code panel."""
        body = self.page.locator("body").inner_text() or ""

        # The availability chart plots a series and labels its axis with dates.
        assert "Unavailable Chargers" in body, (
            "the availability chart is missing its 'Unavailable Chargers' series"
        )
        assert re.search(r"\d{1,2} [A-Z][a-z]{2}", body), (
            "the availability chart has no dated x-axis"
        )
        log.info("Availability chart plots 'Unavailable Chargers' over a dated axis")

        # The OCPP panel is a declared placeholder on staging, not a blank box.
        start = body.find("Top OCPP error codes")
        panel = body[start:start + 200]
        assert "Coming soon" in panel, (
            f"the OCPP panel is neither populated nor marked coming soon: {panel!r}"
        )
        log.info("OCPP error-code panel is marked 'Coming soon'")

    # ----------------------------------------------------------------- #
    # Needs Action feed
    # ----------------------------------------------------------------- #
    def check_needs_action(self):
        """The alert feed lists real alerts, and each drills into a site."""
        count = self.alert_cards.count()
        assert count > 0, "the Needs Action Now feed is empty"
        log.info("Needs Action Now lists %s alert(s)", count)

        # Every alert names its site and says when it started.
        first = self.alert_cards.first
        text = (first.inner_text() or "").strip()
        assert "Alert" in text, f"the first alert card is not labelled: {text[:80]!r}"
        assert re.search(r"since \d{1,2} [A-Z][a-z]{2}", text), (
            f"the first alert does not state when it started: {text[:120]!r}"
        )
        log.info("First alert: %r", text.replace("\n", " ")[:90])

        log.info("Opening the first alert (drills through to Network Status)")
        first.click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-status"), timeout=20000
        )
        log.info("Alert drilled through to Network Status")
        self._return_to_dashboard()

    def check_see_all(self):
        """"See All" on the alert feed opens the notifications list."""
        assert self.see_all.count() > 0, "the dashboard offers no 'See All' link"
        log.info("Following 'See All' from the alert feed")
        self.see_all.first.click()
        self.page.wait_for_url(re.compile(r"/notifications"), timeout=20000)
        expect(
            self.page.get_by_role("button", name="Mark all as read")
        ).to_be_visible()
        log.info("'See All' opened the notifications list")
        self._return_to_dashboard()

    def _return_to_dashboard(self):
        """Go back to the Command Center and wait for it to repopulate.

        Uses the browser's back button rather than the sidebar, because that is
        how someone actually returns from a drill-out -- and it also proves the
        drill-out pushed a history entry instead of replacing one.
        """
        self.page.go_back()
        self.page.wait_for_url(re.compile(r"/operations/overview"), timeout=20000)
        assert self._poll(self._tiles_loaded, timeout_ms=40000), (
            "the dashboard did not repopulate after going back"
        )
        log.info("Back on the Command Center, tiles repopulated")

    # ----------------------------------------------------------------- #
    # Filters
    # ----------------------------------------------------------------- #
    def filter_by_window(self):
        """Step through every rolling window, then restore the default.

        Asserted three ways: the URL carries `window_days`, every panel refetches
        with that parameter and returns 200, and the window-scoped tiles restate
        the window in their own subtitle.
        """
        seen, listener = self._watch_api()
        try:
            for window, days in self.WINDOWS.items():
                if window == self.DEFAULT_WINDOW:
                    continue
                seen.clear()
                log.info("Switching the time window to %r", window)
                self.window_filter.first.click()
                self.page.wait_for_timeout(900)
                self.page.get_by_role("option", name=window, exact=True).click()

                self.page.wait_for_url(
                    re.compile(rf"[?&]window_days={days}\b"), timeout=15000
                )
                self._wait_for_endpoints(seen, expect_param=f"window_days={days}")
                expect(
                    self.page.get_by_role("button", name=window, exact=True)
                ).to_be_visible()
                assert self._poll(
                    lambda w=window: w in self._tile_text("Network uptime")
                ), (
                    f"the Network uptime tile still does not state {window!r}: "
                    f"{self._tile_text('Network uptime')!r}"
                )
                assert self._poll(self._tiles_loaded), (
                    f"the tiles did not repopulate for {window!r}"
                )
                log.info("Window %-14s -> uptime %s", window,
                         self._tile_value("Network uptime").split("\n")[0])

            # Restoring the default is asserted on the rendered state, not on a
            # refetch: the page caches each window's payload, so returning to a
            # window it already pulled is served from cache with no network call
            # at all. Requiring one here fails on correct caching behaviour.
            log.info("Restoring the default window (%s)", self.DEFAULT_WINDOW)
            self.window_filter.first.click()
            self.page.wait_for_timeout(900)
            self.page.get_by_role(
                "option", name=self.DEFAULT_WINDOW, exact=True
            ).click()
            expect(
                self.page.get_by_role(
                    "button", name=self.DEFAULT_WINDOW, exact=True
                )
            ).to_be_visible()
            assert self._poll(
                lambda: self.DEFAULT_WINDOW in self._tile_text("Network uptime")
            ), "the tiles did not return to the default window"
            assert self._poll(self._tiles_loaded)
        finally:
            self.page.remove_listener("response", listener)

    def filter_by_cpo(self):
        """Apply the CPO filter, verify it drove the whole page, then clear it."""
        self._apply_filter(
            trigger=self.cpo_filter,
            value=CPO,
            param="cpo_id",
            label="CPO",
        )

    def filter_by_ownership(self):
        """Apply the Ownership filter, verify it, then clear it."""
        self._apply_filter(
            trigger=self.ownership_filter,
            value=OWNER,
            param="organization_id",
            label="Ownership",
        )

    def _apply_filter(self, trigger, value, param, label):
        """Open a filter, pick `value`, assert it applied, then clear it.

        The filter is confirmed three ways -- the URL gains its parameter, the
        trigger relabels itself to "<label>: <value>", and every panel refetches
        carrying the parameter -- and the tiles are required to repopulate
        afterwards, so a filter that blanks the dashboard is caught.
        """
        before = {l: self._tile_value(l) for l in self.TILES}
        seen, listener = self._watch_api()
        try:
            log.info("Applying the %s filter %r", label, value)
            trigger.click()
            self.page.wait_for_timeout(1000)
            self.page.get_by_role("option", name=value, exact=True).click()

            self.page.wait_for_url(re.compile(rf"[?&]{param}="), timeout=15000)
            self._wait_for_endpoints(seen, expect_param=f"{param}=")
            expect(
                self.page.get_by_role("button", name=f"{label}: {value}")
            ).to_be_visible()
            assert self._poll(self._tiles_loaded), (
                f"the tiles did not repopulate under the {label} filter: "
                f"{ {l: self._tile_value(l) for l in self.TILES} }"
            )
            after = {l: self._tile_value(l) for l in self.TILES}
            log.info("%s filter applied -> Live Sites %s (was %s)",
                     label, after["Live Sites"], before["Live Sites"])

            # As with the window filter, clearing returns the page to a state it
            # has already pulled, which is served from cache -- so this asserts
            # the rendered result rather than demanding a refetch.
            log.info("Clearing all filters")
            self.clear_filters.click()
            expect(trigger).to_be_visible()
            assert self._poll(lambda: param not in self.page.url), (
                f"{param} is still in the URL after clearing: {self.page.url}"
            )
            assert self._poll(
                lambda: {l: self._tile_value(l) for l in self.TILES} == before,
                timeout_ms=20000,
            ), (
                "the tiles did not return to their unfiltered values: "
                f"{ {l: self._tile_value(l) for l in self.TILES} } != {before}"
            )
            log.info("Filters cleared, tiles back to their unfiltered values")
        finally:
            self.page.remove_listener("response", listener)

    # ----------------------------------------------------------------- #
    # Refresh
    # ----------------------------------------------------------------- #
    def refresh_data(self):
        """Refresh must re-pull every panel, not just restamp the clock.

        The visible "Last Updated hh:mm" label only changes when the minute
        rolls over, so it is useless as an assertion -- the check is that all
        five dashboard endpoints are called again and every one returns 200.
        """
        expect(self.refresh).to_be_visible()
        stamp = (self.refresh.inner_text() or "").strip()
        assert re.search(r"Last Updated\s+\d{1,2}:\d{2}", stamp), (
            f"the refresh control does not show a last-updated time: {stamp!r}"
        )

        seen, listener = self._watch_api()
        try:
            log.info("Clicking Refresh data (%s)", stamp)
            self.refresh.click()
            self._wait_for_endpoints(seen)
            log.info("Refresh re-pulled all %s panels (HTTP 200)",
                     len(self.ENDPOINTS))
            assert self._poll(self._tiles_loaded), (
                "the tiles did not repopulate after refreshing"
            )
        finally:
            self.page.remove_listener("response", listener)

    # ----------------------------------------------------------------- #
    # Network Command Map
    # ----------------------------------------------------------------- #
    def browse_map(self):
        """The map's legend, entries, zoom and search."""
        body = self.page.locator("body").inner_text() or ""
        for state in self.MAP_STATES:
            assert state in body, f"the map legend is missing {state!r}"
        log.info("Map legend lists all %s maintenance states", len(self.MAP_STATES))

        # One card per organization, each naming its ID and site count.
        assert self._poll(lambda: self.geo_cards.count() > 0, timeout_ms=30000), (
            "the Geographic Distribution panel lists nothing"
        )
        cards = self.geo_cards.count()
        log.info("Geographic Distribution lists %s organization(s)", cards)
        self._assert_geo_card(self.geo_cards.first)

        log.info("Zooming the map in and back out")
        self.zoom_in.click()
        self.page.wait_for_timeout(1200)
        self.zoom_out.click()
        self.page.wait_for_timeout(1200)
        assert self.geo_cards.count() == cards, (
            "zooming changed the Geographic Distribution list"
        )

        # The map's own search box. It does not filter the distribution list on
        # staging, so the check is that it accepts a query and leaves the panel
        # intact -- not a claim about filtering it does not do.
        log.info("Typing into the map search box")
        expect(self.map_search).to_be_visible()
        self.map_search.fill(CPO)
        self.page.wait_for_timeout(2500)
        assert self.geo_cards.count() > 0, (
            "the Geographic Distribution list emptied while searching"
        )
        self.map_search.fill("")
        self.page.wait_for_timeout(1500)
        assert self._poll(lambda: self.geo_cards.count() == cards), (
            f"expected {cards} entr(ies) after clearing the map search, "
            f"got {self.geo_cards.count()}"
        )

    def _assert_geo_card(self, card):
        """A distribution card states an ID, a site count and a state."""
        text = (card.inner_text() or "").replace("\n", " ")
        assert re.search(r"ID:\s*\d+", text), (
            f"a distribution card has no ID: {text[:80]!r}"
        )
        assert re.search(r"\d+\s+Sites?", text), (
            f"a distribution card has no site count: {text[:80]!r}"
        )
        assert any(state in text for state in self.MAP_STATES), (
            f"a distribution card states no maintenance state: {text[:80]!r}"
        )
        return text

    def drill_geographic_distribution(self):
        """Descend the Geographic Distribution hierarchy and climb back out.

        The panel is three levels deep -- organization, sub-organization, then
        CPO -- and each card drills one level down. The only way back up is the
        parent header card the panel shows once below the top, so this descends
        two levels and then climbs back with it, checking the list actually
        changes at every step.
        """
        assert self.geo_crumb.count() == 0, (
            "the distribution panel is not at its top level to begin with"
        )
        top = [
            (c.inner_text() or "").split("\n")[0]
            for c in self.geo_cards.all()
        ]
        log.info("Distribution level 0: %s organization(s) -> %s", len(top), top)

        # --- descend to the sub-organizations -------------------------- #
        parent = top[0]
        log.info("Drilling into %r", parent)
        self.geo_cards.first.click()
        assert self._poll(lambda: self.geo_crumb.count() == 1, timeout_ms=15000), (
            f"drilling into {parent!r} showed no parent header to go back with"
        )
        assert self._poll(
            lambda: self.geo_cards.count() > 0
            and [(c.inner_text() or "").split("\n")[0]
                 for c in self.geo_cards.all()] != top,
            timeout_ms=15000,
        ), f"drilling into {parent!r} did not change the list"
        expect(self.geo_crumb).to_contain_text(parent)
        level1 = [
            (c.inner_text() or "").split("\n")[0]
            for c in self.geo_cards.all()
        ]
        self._assert_geo_card(self.geo_cards.first)
        log.info("Level 1 under %r: %s sub-org(s) -> %s", parent, len(level1), level1)

        # --- descend again, to the CPOs -------------------------------- #
        child = level1[0]
        log.info("Drilling into %r", child)
        self.geo_cards.first.click()
        assert self._poll(
            lambda: self.geo_cards.count() > 0
            and [(c.inner_text() or "").split("\n")[0]
                 for c in self.geo_cards.all()] != level1,
            timeout_ms=15000,
        ), f"drilling into {child!r} did not change the list"
        expect(self.geo_crumb).to_contain_text(child)
        self._assert_geo_card(self.geo_cards.first)
        log.info("Level 2 under %r: %s CPO(s)", child, self.geo_cards.count())

        # --- climb back out, one level per click ----------------------- #
        log.info("Climbing back up with the parent header")
        self.geo_crumb.first.click()
        assert self._poll(
            lambda: [(c.inner_text() or "").split("\n")[0]
                     for c in self.geo_cards.all()] == level1,
            timeout_ms=15000,
        ), "going back did not restore the sub-organization level"

        self.geo_crumb.first.click()
        assert self._poll(
            lambda: self.geo_crumb.count() == 0
            and [(c.inner_text() or "").split("\n")[0]
                 for c in self.geo_cards.all()] == top,
            timeout_ms=15000,
        ), "going back did not restore the top level"
        log.info("Distribution panel back at level 0 with %s organization(s)",
                 self.geo_cards.count())

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def command_center_page(self):
        self.open_page()
        self.check_hero_tiles()
        self.check_info_tooltips()
        self.check_sections()
        self.check_criticality_triage()
        self.check_charts()
        self.refresh_data()
        self.filter_by_window()
        self.filter_by_cpo()
        self.filter_by_ownership()
        self.browse_map()
        self.drill_geographic_distribution()
        self.check_needs_action()
        self.check_see_all()
        log.info("Command Center workflow completed")
