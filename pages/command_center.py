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

    The operations dashboard: eight hero KPI tiles, a "Needs action" feed of
    sites with sockets down or maintenance overdue, a criticality breakdown and
    a fault-age histogram, the unavailable-chargers chart, an OCPP error-code
    panel, the onboarding pipeline, and the Geographic distribution map. Three
    filters (Ownership, CPO and a rolling time window) drive the whole page.

    The workflow is entirely read-only -- nothing here is submitted, and the
    per-socket "Diagnose" action is deliberately never pressed -- so it always
    leaves staging exactly as it found it. It exercises every control the page
    exposes: all eight tiles and every info tooltip, each of the three filters
    (applied, verified and cleared through both "Clear all filters" and the
    per-filter remove chip), the feed's tabs, "Show more" and drill-out, the
    onboarding drill-out, the triage and fault-age panels, both chart panels,
    and the map's legend, zoom, fullscreen and drill-down list.
    """

    # The eight hero tiles, in the order they render, with the pattern each
    # one's value (the tile's text minus its label) must *start* with, and the
    # caption it must end with. Asserting the shape catches a tile that renders
    # a label with no number -- a common way for a dashboard to break -- without
    # pinning the test to figures that legitimately change every day.
    TILES = {
        "Network uptime": r"\d+(\.\d+)?%",
        "Chargers down": r"\d+\s*/\s*\d+",
        "Failed sessions": r"(\d[\d,.]*%?|—)",
        "Onboarding": r"\d+",
        "Utilisation": r"\d+(\.\d+)?%",
        "Live Sites": r"\d+",
        "At-Risk Sites": r"\d+",
        "Overdue": r"\d+",
    }
    TILE_CAPTIONS = {
        "Chargers down": "Live",
        "Onboarding": "Pipeline",
        "Live Sites": re.compile(r"Across \d+ CPOs?"),
        "At-Risk Sites": "SLA breach within 7d",
        "Overdue": "Maintenance past due",
    }
    # Tiles whose caption restates the active window instead.
    WINDOW_TILES = ["Network uptime", "Failed sessions", "Utilisation"]

    # Every panel title on the page. Several are CSS-uppercased (the h2
    # "PERFORMANCE" is "Performance" in the DOM), so they are matched
    # case-insensitively.
    SECTIONS = [
        "Needs action",
        "Criticality",
        "Fault age",
        "Performance",
        "Unavailable Chargers",
        "Top OCPP error codes",
        "Onboarding",
        "Pipeline",
        "Network",
        "Geographic distribution",
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

    # The endpoints that are scoped to the time window -- a window change must
    # re-pull all of them carrying `window_days`.
    WINDOWED_ENDPOINTS = [
        "hero-kpis",
        "kpis",
        "criticality-triage",
        "error-codes",
        "availability-timeseries",
    ]
    # The Needs action feed is not windowed (it is "open right now"), but it is
    # scoped by Ownership / CPO -- so a filter must re-pull it as well.
    FILTERED_ENDPOINTS = WINDOWED_ENDPOINTS + ["needs-action"]

    # The criticality buckets and the fault-age histogram's bins.
    CRITICALITIES = ["High", "Medium", "Low"]
    FAULT_AGES = ["Under 1h", "1–4h", "4–12h", "12–24h", "Over a day"]

    # The map legend's maintenance states (shared with the Operations Hub map).
    MAP_STATES = ["Overdue", "In Progress", "Scheduled", "Upcoming",
                  "Completed", "No Maintenance"]

    # The Needs action feed's tabs. "All" must equal the other two combined.
    FEED_TABS = ["All", "Sockets down", "Maintenance overdue"]

    # Every info icon on the page, keyed by what its aria-label names
    # ("About <section>"), mapped to a phrase its tooltip must contain. All
    # eight hero tiles carry one now, plus seven panel titles.
    #
    # The phrases matter: without them a tooltip check passes as long as *some*
    # text appears, which hides an icon wired to the wrong explanation.
    TOOLTIP_SECTIONS = {
        "Network uptime": "Usable device-time",
        "Chargers down": "faulted or offline right now",
        "Failed sessions": "ended in an error",
        "Onboarding": "mid-onboarding",
        "Utilisation": "socket-hours",
        "Live Sites": "commissioned, reporting device",
        "At-Risk Sites": "SLA breach",
        "Overdue": "past their due date",
        "Needs action": "Sockets down and overdue maintenance",
        "Criticality": "criticality band",
        "Fault age": "open faults",
        "Unavailable Chargers": "simultaneously unavailable",
        "Top OCPP error codes": "StatusNotification",
        "Pipeline": "not yet fully commissioned",
        "Geographic distribution": "maintenance state",
    }

    def __init__(self, page):
        self.page = page

        # Sidebar navigation
        self.cc_link = page.get_by_role("link", name="Command Center", exact=True).first
        self.heading = page.locator("//h1[normalize-space()='Command Center']")

        # Every info icon is a button labelled "About <what it explains>".
        self.tooltip_buttons = page.get_by_role(
            "button", name=re.compile(r"^About ")
        )

        # Filters. Each trigger's accessible name is its label followed by its
        # current value -- and, once a value is set, by that value's remove chip
        # ("CPO Capurro Garage Remove Capurro Garage"). Anchored on the label
        # so the locator keeps resolving whatever the value is.
        self.ownership_filter = page.get_by_role(
            "button", name=re.compile(r"^Ownership\b")
        ).first
        self.cpo_filter = page.get_by_role(
            "button", name=re.compile(r"^CPO\b")
        ).first
        self.window_filter = page.get_by_role(
            "button", name=re.compile(r"^Window\b")
        ).first
        # Only rendered while something is off its default.
        self.clear_filters = page.get_by_role("button", name="Clear all filters")

        # Needs action feed. The section is picked out by its own info icon;
        # every site group in it ends in an "Open <site>" link to that site's
        # Operations Hub page.
        self.feed = page.locator("section").filter(
            has=page.get_by_role("button", name="About Needs action")
        )
        self.feed_links = self.feed.get_by_role(
            "link", name=re.compile(r"^Open ")
        )
        self.show_more = self.feed.get_by_role(
            "button", name=re.compile(r"^Show \d+ more$")
        )
        self.show_less = self.feed.get_by_role("button", name="Show less")

        # Onboarding pipeline drill-out
        self.onboarding_link = page.get_by_role(
            "link", name="Open Deal Onboarding"
        )

        # Map
        self.zoom_in = page.get_by_role("button", name="Zoom in")
        self.zoom_out = page.get_by_role("button", name="Zoom out")
        self.fullscreen = page.get_by_role("button", name="Expand to fullscreen")

        # Geographic distribution: a drill-down list overlaid on the map. Each
        # entry is a clickable card stating "ID: n", a site count and a
        # maintenance state; clicking one descends a level.
        self.geo_cards = page.get_by_role("button", name=re.compile(r"ID:\s*\w+"))
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

        The dashboard pulls its endpoints in parallel and each panel paints as
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
        """The KPI tile carrying `label` -- and nothing but that tile.

        Located through the tile's own info icon ("About <label>"): the icon
        sits in the label row, and the label row's parent is the tile. The
        previous approach (the nearest `div.bg-card-bg` containing the label
        text) now resolves to the card wrapping the *whole* KPI strip and the
        filter bar, so every tile read back the entire strip and the value
        check passed against whichever tile happened to match first.
        """
        icon = self.page.get_by_role("button", name=f"About {label}", exact=True)
        return icon.locator("xpath=../..")

    def _tile_lines(self, label):
        """The tile's rendered lines, label first (uppercased by CSS)."""
        try:
            text = self._tile(label).inner_text(timeout=2000) or ""
        except Exception:
            # The tile re-renders as its endpoint lands; treat a read that
            # lands mid-repaint as "not settled yet".
            return []
        return [l.strip() for l in text.split("\n") if l.strip()]

    def _tile_text(self, label):
        return " ".join(self._tile_lines(label))

    def _tile_value(self, label):
        """The tile's value + caption, i.e. its own text with the label off.

        Returns "" unless the first line really is this tile's label -- so a
        locator that drifts onto a bigger container fails loudly instead of
        returning some other tile's number.
        """
        lines = self._tile_lines(label)
        if not lines or lines[0].lower() != label.lower():
            return ""
        return " ".join(lines[1:])

    def _tiles_loaded(self):
        """True once every tile has painted a value matching its pattern."""
        for label, pattern in self.TILES.items():
            if not re.match(pattern, self._tile_value(label)):
                return False
        return True

    def _tiles(self):
        return {l: self._tile_value(l) for l in self.TILES}

    def _watch_api(self):
        """Record (endpoint, status, url) for every dashboard call.

        Filters are asserted on the API rather than only on the rendered
        numbers: the figures are live and legitimately identical between two
        windows, so "did a number change?" is not a reliable signal, whereas
        "did the panel refetch with this parameter and get a 200?" is.
        """
        seen = []

        def on_response(response):
            if "/operations/overview/" in response.url:
                endpoint = response.url.split("/overview/")[-1].split("?")[0]
                seen.append((endpoint, response.status, response.url))

        self.page.on("response", on_response)
        return seen, on_response

    def _wait_for_endpoints(self, seen, endpoints, expect_param=None,
                            timeout_ms=25000):
        """Wait until every endpoint in `endpoints` answered, then assert 200s."""
        assert self._poll(
            lambda: {e for e, _, _ in seen} >= set(endpoints),
            timeout_ms=timeout_ms,
        ), (
            f"only {sorted({e for e, _, _ in seen})} refetched, "
            f"expected all of {endpoints}"
        )
        bad = [(e, s) for e, s, _ in seen if s != 200]
        assert not bad, f"dashboard endpoints returned non-200: {bad}"
        if expect_param:
            missing = [
                e for e in endpoints
                if not any(e == ep and expect_param in url for ep, _, url in seen)
            ]
            assert not missing, (
                f"{missing} did not carry {expect_param!r} in the request"
            )

    def _blur(self):
        """Drop keyboard focus, so the sidebar stops holding itself open.

        The sidebar widens from 125px to 294px for `:focus-visible` as well as
        for hover, and the nav link that opened the page keeps focus. The first
        key press of the run -- the Escape that closes a tooltip -- promotes
        that focus to `:focus-visible`, the nav stays expanded, and every
        control within 294px of the left edge becomes unhoverable. That is the
        left-hand column of tiles (Utilisation is the first casualty).
        """
        self.page.evaluate(
            "() => document.activeElement && document.activeElement.blur()"
        )

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
        self._blur()
        # The width transition is 500ms; give it time to finish retracting.
        self.page.wait_for_timeout(600)

    def _dismiss_tooltip(self):
        """Close the open tooltip and let it leave the DOM.

        Hovering a different element is not enough -- the pointer has to leave
        the trigger entirely -- so this parks the mouse somewhere inert and
        presses Escape, which these tooltips honour. Parking also blurs, which
        undoes the `:focus-visible` that the Escape itself would otherwise
        leave on the sidebar.
        """
        self.page.keyboard.press("Escape")
        self._park_pointer()

    def _close_popover(self):
        """Dismiss a filter popover if it is still open, without applying."""
        if self.page.get_by_role("listbox").count():
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(400)
        self._park_pointer()

    def _body(self):
        return self.page.locator("body").inner_text() or ""

    def _panel(self, title, length=400):
        """The rendered text following a panel title (case-insensitive)."""
        body = self._body()
        start = body.lower().find(title.lower())
        assert start >= 0, f"no {title!r} panel on the page"
        return body[start:start + length]

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
            f"the KPI tiles never finished loading: {self._tiles()}"
        )
        self._park_pointer()
        log.info("Command Center loaded with all %s KPI tiles populated",
                 len(self.TILES))

    # ----------------------------------------------------------------- #
    # Hero KPI tiles
    # ----------------------------------------------------------------- #
    def check_hero_tiles(self):
        """Every tile renders its own label, a well-formed value and caption."""
        for label, pattern in self.TILES.items():
            tile = self._tile(label)
            expect(tile).to_be_visible()
            lines = self._tile_lines(label)
            # The tile is only its own label + value + caption. More than a
            # handful of lines means the locator has climbed into a container.
            assert lines and lines[0].lower() == label.lower(), (
                f"the {label!r} tile does not lead with its own label: {lines}"
            )
            assert len(lines) <= 4, (
                f"the {label!r} tile reads {len(lines)} lines -- that is not a "
                f"single tile: {lines}"
            )
            value = self._tile_value(label)
            assert re.match(pattern, value), (
                f"the {label!r} tile shows {value!r}, which does not match "
                f"the expected shape {pattern!r}"
            )
            caption = self.TILE_CAPTIONS.get(label)
            if isinstance(caption, re.Pattern):
                assert caption.search(value), (
                    f"the {label!r} tile caption {value!r} does not match "
                    f"{caption.pattern!r}"
                )
            elif caption:
                assert value.endswith(caption), (
                    f"the {label!r} tile should be captioned {caption!r}: {value!r}"
                )
            log.info("Tile %-16s -> %s", label, value)

        # The window-scoped tiles state the window they cover.
        for label in self.WINDOW_TILES:
            assert self._tile_value(label).endswith(self.DEFAULT_WINDOW), (
                f"the {label!r} tile does not state the {self.DEFAULT_WINDOW!r} "
                f"window: {self._tile_value(label)!r}"
            )

        # Chargers down is "down / fleet"; down can never exceed the fleet.
        down, fleet = map(int, re.findall(r"\d+", self._tile_value("Chargers down"))[:2])
        assert down <= fleet, f"{down} chargers down out of a fleet of {fleet}"
        log.info("All %s hero tiles render a well-formed value", len(self.TILES))

    def check_info_tooltips(self):
        """Hover every info icon on the page and read its tooltip.

        Covers all fifteen -- one per hero tile plus the seven panel titles.
        Each is identified by its own aria-label ("About <section>") and must
        produce the explanation for *that* section, so an icon wired to an
        empty, missing or mismatched tooltip is caught.
        """
        labels = [
            (b.get_attribute("aria-label") or "")[len("About "):]
            for b in self.tooltip_buttons.all()
        ]
        assert sorted(labels) == sorted(self.TOOLTIP_SECTIONS), (
            f"expected info icons on {sorted(self.TOOLTIP_SECTIONS)}, "
            f"found {sorted(labels)}"
        )

        tooltip = self.page.get_by_role("tooltip")
        for index, section in enumerate(labels):
            button = self.tooltip_buttons.nth(index)

            # Wait for the previous tooltip to leave the DOM before hovering the
            # next one. It lingers while it fades, and reading through that
            # window returns the *previous* icon's text -- which silently shifts
            # every result by one and still passes a "is there any text?" check.
            assert self._poll(lambda: tooltip.count() == 0, timeout_ms=8000), (
                f"the previous tooltip never closed before hovering {section!r}"
            )
            # Centre the icon in the viewport (well clear of the sidebar pinned
            # to the left edge) and let the sidebar collapse before hovering.
            button.evaluate("e => e.scrollIntoView({block: 'center'})")
            self._park_pointer()
            button.hover()
            assert self._poll(lambda: tooltip.count() > 0, timeout_ms=8000), (
                f"the info icon on {section!r} raised no tooltip"
            )
            text = (tooltip.first.inner_text() or "").strip()

            expected = self.TOOLTIP_SECTIONS[section]
            assert expected.lower() in text.lower(), (
                f"the {section!r} tooltip should mention {expected!r} but reads "
                f"{text!r} -- the icons and their explanations are mismatched"
            )
            log.info("Tooltip %-24s -> %s", section, text[:64])
            self._dismiss_tooltip()
        self._scroll_top()
        log.info("All %s info tooltips explain the right section", len(labels))

    def _scroll_top(self):
        self.page.evaluate(
            "() => { const m = document.querySelector('main');"
            " if (m) m.scrollTo(0, 0); window.scrollTo(0, 0); }"
        )
        self.page.wait_for_timeout(300)

    # ----------------------------------------------------------------- #
    # Sections
    # ----------------------------------------------------------------- #
    def check_sections(self):
        """Every panel title is present."""
        body = self._body().lower()
        missing = [s for s in self.SECTIONS if s.lower() not in body]
        assert not missing, f"the dashboard is missing the panel(s) {missing}"
        for h2 in ("Performance", "Onboarding", "Network"):
            expect(
                self.page.get_by_role("heading", name=h2, exact=True)
            ).to_be_visible()
        log.info("All %s dashboard panels render", len(self.SECTIONS))

    def check_criticality_triage(self):
        """The criticality panel breaks sites down by band, with counts."""
        panel = self._panel("Criticality", 300)
        for level in self.CRITICALITIES:
            assert re.search(rf"\b{level}\s+\d+", panel), (
                f"the criticality panel is missing a {level!r} count: {panel!r}"
            )
        assert re.search(r"\d+ sites? uncategori[sz]ed", panel), (
            f"the criticality panel does not report uncategorised sites: {panel!r}"
        )
        log.info("Criticality shows High / Medium / Low buckets with counts")

    def check_fault_age(self):
        """The fault-age histogram, and that its summary adds up.

        The panel states "N sockets have been down more than 12 hours"; N must
        be the sum of its own 12–24h and "Over a day" bars.
        """
        panel = self._panel("Fault age", 300)
        counts = {}
        for bucket in self.FAULT_AGES:
            m = re.search(rf"{re.escape(bucket)}\s+(\d+)", panel)
            assert m, f"the fault-age panel is missing the {bucket!r} bar: {panel!r}"
            counts[bucket] = int(m.group(1))
        summary = re.search(r"(\d+) sockets? (?:has|have) been down more than 12 hours",
                            panel)
        if summary:
            assert int(summary.group(1)) == counts["12–24h"] + counts["Over a day"], (
                f"the fault-age summary says {summary.group(1)} sockets are over "
                f"12h, but its own bars give {counts}"
            )
        log.info("Fault age -> %s", counts)

    def check_charts(self):
        """The unavailable-chargers chart and the OCPP error-code panel."""
        panel = self._panel("Unavailable Chargers", 600)
        assert re.search(r"Average\s+\d+\s+chargers unavailable", panel), (
            f"the availability chart states no average: {panel[:160]!r}"
        )
        assert re.search(r"\d{1,2} [A-Z][a-z]{2}", panel), (
            "the availability chart has no dated x-axis"
        )
        assert re.search(r"Peak \d+ on \d{1,2} [A-Z][a-z]{2}", panel), (
            "the availability chart does not call out its peak"
        )
        expect(
            self.page.get_by_role("img", name=re.compile(r"^Daily unavailable chargers"))
        ).to_be_visible()
        log.info("Availability chart plots daily unavailable chargers over a dated axis")

        # The OCPP panel is a declared placeholder on staging, not a blank box.
        panel = self._panel("Top OCPP error codes", 200)
        assert "Not yet ingested" in panel, (
            f"the OCPP panel is neither populated nor marked as not ingested: "
            f"{panel!r}"
        )
        log.info("OCPP error-code panel is marked 'Not yet ingested'")

    def check_onboarding(self):
        """The pipeline panel agrees with the Onboarding tile, and drills out."""
        panel = self._panel("In pipeline", 120)
        m = re.search(r"In pipeline\s+(\d+)", panel, re.I)
        assert m, f"the pipeline panel states no count: {panel!r}"
        tile = int(re.match(r"\d+", self._tile_value("Onboarding")).group())
        assert int(m.group(1)) == tile, (
            f"the pipeline panel says {m.group(1)} but the Onboarding tile says {tile}"
        )
        log.info("Pipeline panel and Onboarding tile agree (%s)", tile)

        log.info("Following 'Open Deal Onboarding'")
        self.onboarding_link.scroll_into_view_if_needed()
        self._park_pointer()
        self.onboarding_link.click()
        self.page.wait_for_url(re.compile(r"/operations/deal-onboarding"),
                               timeout=20000)
        log.info("Pipeline drilled through to Deal Onboarding")
        self._return_to_dashboard()

    # ----------------------------------------------------------------- #
    # Needs action feed
    # ----------------------------------------------------------------- #
    def _feed_tab(self, name):
        return self.feed.get_by_role("button", name=re.compile(rf"^{name}\s+\d+$"))

    def _feed_tab_count(self, name):
        return int(re.search(r"\d+", self._feed_tab(name).inner_text()).group())

    def _feed_cards(self):
        """Every visible site group in the feed, read in one snapshot.

        The "Open <site>" link sits in the group's trailing cell; its
        grandparent is the group itself. Read with evaluate_all because the
        feed re-renders on every tab switch and per-row locators go stale.
        """
        return self.feed_links.evaluate_all(
            "els => els.map(e => e.parentElement.parentElement.innerText)"
        )

    def _shown_plus_more(self):
        """Visible groups plus whatever "Show N more" is still holding back."""
        more = 0
        if self.show_more.count():
            more = int(re.search(r"\d+", self.show_more.inner_text()).group())
        return self.feed_links.count() + more

    def check_needs_action(self):
        """The feed's tabs, their counts, "Show more", and a site drill-out."""
        expect(self.feed).to_be_visible()
        counts = {tab: self._feed_tab_count(tab) for tab in self.FEED_TABS}
        log.info("Needs action tabs -> %s", counts)
        assert counts["All"] > 0, "the Needs action feed is empty"
        assert counts["All"] == counts["Sockets down"] + counts["Maintenance overdue"], (
            f"'All' should be the other two tabs combined: {counts}"
        )
        header = re.search(r"(\d+) open", self.feed.inner_text())
        assert header and int(header.group(1)) == counts["All"], (
            f"the feed header does not agree with 'All' ({counts['All']}): "
            f"{header.group(0) if header else None!r}"
        )

        # Each tab lists exactly as many groups as it claims (visible + "Show N
        # more"), and each group belongs to that tab.
        markers = {"Sockets down": r"sockets? down",
                   "Maintenance overdue": r"past due"}
        for tab in ["Sockets down", "Maintenance overdue", "All"]:
            self._feed_tab(tab).click()
            assert self._poll(lambda t=tab: self._shown_plus_more() == counts[t]), (
                f"the {tab!r} tab claims {counts[tab]} but lists "
                f"{self._shown_plus_more()}"
            )
            if tab in markers:
                stray = [c.split("\n")[0] for c in self._feed_cards()
                         if not re.search(markers[tab], c)]
                assert not stray, f"the {tab!r} tab lists unrelated items: {stray}"
            log.info("Tab %-20s lists %s item(s)", tab, counts[tab])

        # "Show N more" expands the feed to the full list; "Show less" folds it.
        if self.show_more.count():
            shown = self.feed_links.count()
            log.info("Expanding the feed with %r", self.show_more.inner_text())
            self.show_more.click()
            assert self._poll(lambda: self.feed_links.count() == counts["All"]), (
                f"'Show more' revealed {self.feed_links.count()} of {counts['All']}"
            )
            self.show_less.click()
            assert self._poll(lambda: self.feed_links.count() == shown), (
                f"'Show less' left {self.feed_links.count()} items (expected {shown})"
            )

        # Every group names its site and says how long it has been open.
        first = self._feed_cards()[0]
        assert re.search(r"\d+d (open|past due)", first), (
            f"the first feed item does not state its age: {first[:120]!r}"
        )
        log.info("First item: %r", first.replace("\n", " ")[:90])

        link = self.feed_links.first
        site = (link.get_attribute("aria-label") or "")[len("Open "):]
        log.info("Opening %r (drills through to its Operations Hub page)", site)
        link.scroll_into_view_if_needed()
        self._park_pointer()
        link.click()
        self.page.wait_for_url(
            re.compile(r"/operations/operations-hub/[^/?]+/[^/?]+"), timeout=20000
        )
        log.info("Feed item drilled through to the site page")
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
        self._park_pointer()
        log.info("Back on the Command Center, tiles repopulated")

    # ----------------------------------------------------------------- #
    # Filters
    # ----------------------------------------------------------------- #
    def filter_by_window(self):
        """Step through every rolling window, then restore the default.

        Asserted three ways: the URL carries `window_days`, every windowed
        panel refetches with that parameter and returns 200, and the
        window-scoped tiles restate the window in their own caption.
        """
        seen, listener = self._watch_api()
        try:
            for window, days in self.WINDOWS.items():
                if window == self.DEFAULT_WINDOW:
                    continue
                seen.clear()
                log.info("Switching the time window to %r", window)
                self._scroll_top()
                self.window_filter.click()
                self.page.get_by_role("option", name=window, exact=True).click()

                self.page.wait_for_url(
                    re.compile(rf"[?&]window_days={days}\b"), timeout=15000
                )
                self._wait_for_endpoints(seen, self.WINDOWED_ENDPOINTS,
                                         expect_param=f"window_days={days}")
                expect(self.window_filter).to_contain_text(window)
                expect(self.clear_filters).to_be_visible()
                for label in self.WINDOW_TILES:
                    assert self._poll(
                        lambda l=label, w=window: self._tile_value(l).endswith(w)
                    ), (
                        f"the {label} tile still does not state {window!r}: "
                        f"{self._tile_value(label)!r}"
                    )
                assert self._poll(self._tiles_loaded), (
                    f"the tiles did not repopulate for {window!r}"
                )
                log.info("Window %-14s -> uptime %s", window,
                         self._tile_value("Network uptime"))
                self._close_popover()

            # Restored with the window's own remove chip, which drops the
            # parameter and falls back to the default. Asserted on the rendered
            # state, not on a refetch: the page caches each window's payload,
            # so returning to one it already pulled makes no network call.
            last = list(self.WINDOWS)[-1]
            log.info("Removing the %r chip to restore the default window", last)
            self.page.get_by_role("button", name=f"Remove {last}", exact=True).click()
            # The chip sits inside the trigger, so the click can also open it.
            self._close_popover()
            assert self._poll(lambda: "window_days" not in self.page.url), (
                f"window_days is still in the URL: {self.page.url}"
            )
            expect(self.window_filter).to_contain_text(self.DEFAULT_WINDOW)
            assert self._poll(
                lambda: self._tile_value("Network uptime").endswith(self.DEFAULT_WINDOW)
            ), "the tiles did not return to the default window"
            assert self._poll(self._tiles_loaded)
            expect(self.clear_filters).to_have_count(0)
        finally:
            self.page.remove_listener("response", listener)
            self._park_pointer()

    def filter_by_cpo(self):
        """Apply the CPO filter, verify it drove the whole page, then clear it.

        CPO is a multi-select: ticking an option changes nothing until Apply
        is pressed, so the check searches, ticks, and then applies.
        """
        def pick():
            popover = self.page.locator("[data-radix-popper-content-wrapper]").last
            search = popover.get_by_role("textbox")
            if search.count():
                search.first.fill(CPO)
            option = self.page.get_by_role("option", name=CPO, exact=True)
            assert self._poll(lambda: option.count() == 1, timeout_ms=10000), (
                f"the CPO filter does not offer {CPO!r}"
            )
            option.click()
            expect(option).to_have_attribute("aria-selected", "true")
            popover.get_by_role("button", name="Apply").click()

        def applied():
            # Live Sites is captioned with the CPO spread, which must follow
            # the filter down to the one CPO it now covers.
            expect(self._tile("Live Sites")).to_contain_text("Across 1 CPO")

        self._apply_filter(self.cpo_filter, CPO, "cpo_id", "CPO", pick,
                           clear_with_chip=False, check_applied=applied)

    def filter_by_ownership(self):
        """Apply the (single-select) Ownership filter, verify it, clear it."""
        def pick():
            self.page.get_by_role("option", name=OWNER, exact=True).click()

        self._apply_filter(self.ownership_filter, OWNER, "organization_id",
                           "Ownership", pick, clear_with_chip=True)

    def _apply_filter(self, trigger, value, param, label, pick, clear_with_chip,
                      check_applied=None):
        """Open a filter, pick `value`, assert it applied, then clear it.

        The filter is confirmed three ways -- the URL gains its parameter, the
        trigger relabels itself to "<label> <value>", and every panel
        (including the Needs action feed) refetches carrying the parameter --
        and the tiles are required to repopulate afterwards, so a filter that
        blanks the dashboard is caught. It is then cleared either through
        "Clear all filters" or through the value's own remove chip.
        """
        self._scroll_top()
        self._park_pointer()
        before = self._tiles()
        seen, listener = self._watch_api()
        try:
            log.info("Applying the %s filter %r", label, value)
            trigger.click()
            pick()

            self.page.wait_for_url(re.compile(rf"[?&]{param}="), timeout=15000)
            self._wait_for_endpoints(seen, self.FILTERED_ENDPOINTS,
                                     expect_param=f"{param}=")
            self._close_popover()
            expect(trigger).to_contain_text(value)
            assert self._poll(self._tiles_loaded), (
                f"the tiles did not repopulate under the {label} filter: "
                f"{self._tiles()}"
            )
            after = self._tiles()
            log.info("%s filter applied -> Live Sites %s (was %s)",
                     label, after["Live Sites"], before["Live Sites"])
            if check_applied:
                check_applied()

            # As with the window filter, clearing returns the page to a state it
            # has already pulled, which is served from cache -- so this asserts
            # the rendered result rather than demanding a refetch.
            if clear_with_chip:
                log.info("Removing the %r chip", value)
                self.page.get_by_role(
                    "button", name=f"Remove {value}", exact=True
                ).click()
                # The chip sits inside the trigger, so the click can open it.
                self._close_popover()
            else:
                log.info("Clearing all filters")
                self.clear_filters.click()
            assert self._poll(lambda: param not in self.page.url), (
                f"{param} is still in the URL after clearing: {self.page.url}"
            )
            expect(trigger).not_to_contain_text(value)
            assert self._poll(lambda: self._tiles() == before, timeout_ms=20000), (
                "the tiles did not return to their unfiltered values: "
                f"{self._tiles()} != {before}"
            )
            log.info("Filter cleared, tiles back to their unfiltered values")
        finally:
            self.page.remove_listener("response", listener)
            self._park_pointer()

    # ----------------------------------------------------------------- #
    # Geographic distribution map
    # ----------------------------------------------------------------- #
    def browse_map(self):
        """The map's legend, entries, zoom and fullscreen view."""
        panel = self._panel("Color thresholds", 200)
        for state in self.MAP_STATES:
            assert state in panel, f"the map legend is missing {state!r}"
        log.info("Map legend lists all %s maintenance states", len(self.MAP_STATES))

        # One card per organization, each naming its ID and site count.
        assert self._poll(lambda: self.geo_cards.count() > 0, timeout_ms=30000), (
            "the Geographic distribution panel lists nothing"
        )
        cards = self.geo_cards.count()
        log.info("Geographic distribution lists %s organization(s)", cards)
        self._assert_geo_card(self.geo_cards.first)

        log.info("Zooming the map in and back out")
        self.zoom_in.scroll_into_view_if_needed()
        self._park_pointer()
        self.zoom_in.click()
        self.page.wait_for_timeout(1200)
        self.zoom_out.click()
        self.page.wait_for_timeout(1200)
        assert self._poll(lambda: self.geo_cards.count() == cards), (
            "zooming changed the Geographic distribution list"
        )

        log.info("Opening the map fullscreen and closing it again")
        self._park_pointer()
        self.fullscreen.click()
        dialog = self.page.get_by_role("dialog")
        assert self._poll(lambda: dialog.count() > 0, timeout_ms=10000), (
            "'Expand to fullscreen' opened nothing"
        )
        self.page.keyboard.press("Escape")
        assert self._poll(lambda: dialog.count() == 0, timeout_ms=10000), (
            "the fullscreen map did not close on Escape"
        )
        self._park_pointer()

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

    def _geo_names(self):
        try:
            return [t.split("\n")[0] for t in self.geo_cards.all_inner_texts()]
        except Exception:
            return []

    def drill_geographic_distribution(self):
        """Descend the Geographic distribution hierarchy and climb back out.

        The panel is three levels deep -- organization, sub-organization, then
        CPO -- and each card drills one level down. The only way back up is the
        parent header card the panel shows once below the top, so this descends
        two levels and then climbs back with it, checking the list actually
        changes at every step.
        """
        assert self.geo_crumb.count() == 0, (
            "the distribution panel is not at its top level to begin with"
        )
        top = self._geo_names()
        log.info("Distribution level 0: %s organization(s) -> %s", len(top), top)

        # --- descend to the sub-organizations -------------------------- #
        parent = top[0]
        log.info("Drilling into %r", parent)
        self._park_pointer()
        self.geo_cards.first.click()
        assert self._poll(lambda: self.geo_crumb.count() == 1, timeout_ms=15000), (
            f"drilling into {parent!r} showed no parent header to go back with"
        )
        assert self._poll(
            lambda: self._geo_names() and self._geo_names() != top,
            timeout_ms=15000,
        ), f"drilling into {parent!r} did not change the list"
        expect(self.geo_crumb).to_contain_text(parent)
        level1 = self._geo_names()
        self._assert_geo_card(self.geo_cards.first)
        log.info("Level 1 under %r: %s sub-org(s) -> %s", parent, len(level1), level1)

        # --- descend again, to the CPOs -------------------------------- #
        child = level1[0]
        log.info("Drilling into %r", child)
        self.geo_cards.first.click()
        assert self._poll(
            lambda: self._geo_names() and self._geo_names() != level1,
            timeout_ms=15000,
        ), f"drilling into {child!r} did not change the list"
        expect(self.geo_crumb).to_contain_text(child)
        self._assert_geo_card(self.geo_cards.first)
        log.info("Level 2 under %r: %s CPO(s)", child, self.geo_cards.count())

        # --- climb back out, one level per click ----------------------- #
        log.info("Climbing back up with the parent header")
        self.geo_crumb.first.click()
        assert self._poll(
            lambda: self._geo_names() == level1, timeout_ms=15000,
        ), "going back did not restore the sub-organization level"

        self.geo_crumb.first.click()
        assert self._poll(
            lambda: self.geo_crumb.count() == 0 and self._geo_names() == top,
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
        self.check_fault_age()
        self.check_charts()
        self.filter_by_window()
        self.filter_by_cpo()
        self.filter_by_ownership()
        self.browse_map()
        self.drill_geographic_distribution()
        self.check_needs_action()
        self.check_onboarding()
        log.info("Command Center workflow completed")

    # ----------------------------------------------------------------- #
    # Known product bugs -- each its own test, see the method docstring
    # ----------------------------------------------------------------- #
    def check_live_sites_caption_under_ownership(self):
        """Live Sites' "Across N CPOs" caption must follow the Ownership filter.

        Known product bug: with Ownership = Henbury Lodge the tile counts 1
        live site yet is still captioned "Across 61 CPOs" -- the unfiltered
        spread -- while the CPO filter does narrow it ("Across 1 CPO"). The
        `kpis` call returns the filtered site count, so the CPO count in the
        caption is evidently derived without the ownership scope. However it
        is computed, N sites cannot span more than N CPOs.
        """
        self._scroll_top()
        self._park_pointer()
        log.info("Applying the Ownership filter %r", OWNER)
        self.ownership_filter.click()
        self.page.get_by_role("option", name=OWNER, exact=True).click()
        self.page.wait_for_url(re.compile(r"[?&]organization_id="),
                               timeout=15000)
        self._close_popover()
        expect(self.ownership_filter).to_contain_text(OWNER)

        def read():
            match = re.fullmatch(r"(\d+) Across (\d+) CPOs?",
                                 self._tile_value("Live Sites"))
            return match and (int(match.group(1)), int(match.group(2)))

        # Wait for the tile to settle on its filtered figures before judging.
        assert self._poll(lambda: read(), timeout_ms=20000), (
            f"the Live Sites tile is unreadable: "
            f"{self._tile_value('Live Sites')!r}"
        )
        previous, settled = None, None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = read()
            if current and current == previous:
                settled = current
                break
            previous = current
            self.page.wait_for_timeout(1000)
        sites, cpos = settled or read()
        log.info("Ownership %r -> Live Sites %s across %s CPO(s)",
                 OWNER, sites, cpos)
        assert cpos <= sites, (
            f"under Ownership = {OWNER!r} the Live Sites tile counts {sites} "
            f"site(s) but is captioned 'Across {cpos} CPOs' -- the caption "
            f"ignores the Ownership filter"
        )

    def live_sites_caption_page(self):
        self.open_page()
        self.check_live_sites_caption_under_ownership()
