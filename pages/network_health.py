import logging
import re
import time

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.network_health")

# The sub-organisation the filter check is pinned to. Larkfleet owns exactly
# one of the handful of sites that have monitoring coverage on staging, so
# applying it collapses the Sites table to a single row -- a change large
# enough to be unambiguous and small enough to verify row by row.
SUB_ORG = "Larkfleet"

# The search check reuses it: the term matches that site by name, which proves
# the box really filters rather than merely accepting typing.
SEARCH_TERM = SUB_ORG

# A term no site, charger or ID can match, used to reach the empty state.
NO_MATCH = "zzzz-no-such-site"

# The range the page opens on, and the one the range check switches to.
DEFAULT_RANGE = "Last 30 days"
OTHER_RANGE = "Last 7 days"


class network_health:
    """Network Health (/operations/network-health).

    The diagnostic view of the estate, one level below Network Status: eight
    KPI tiles over a chosen date range, a trend section, a reliability section
    that accounts for every observed EVSE minute, a ranked at-risk charger
    watchlist, a site coverage table that expands into its chargers, and an
    alert history with its own filters. Sites and chargers each have their own
    page underneath this one.

    The workflow is entirely read-only -- the page creates, edits and deletes
    nothing -- so it always leaves staging exactly as it found it. It exercises
    every control: all eight tiles and their tooltips, both trend charts, all
    four reliability panels, the watchlist with its per-charger score
    explainer and its expand toggle, the sites table with search, expansion
    and pagination, the sub-organisation filter (applied and cleared), the
    date-range presets, the three alert filters, and the site and charger
    pages reached from the table.
    """

    # The eight KPI tiles, in render order. Each maps to the shape its value
    # must take and a phrase its tooltip must contain. The labels are rendered
    # uppercase by CSS but sit in the DOM in title case, so they are read from
    # the rendered text rather than matched as DOM strings.
    PERCENT = r"\d[\d,]*\.\d{2}%"
    TILES = {
        "FLEET UPTIME": (PERCENT, "averaged across every charger in scope"),
        "COVERAGE": (PERCENT, "Share of the window we actually observed"),
        "OCCUPIED": (PERCENT, "plugged-in car drawing no power"),
        "CHARGING": (PERCENT, "actually delivering energy"),
        "FAULT HOURS": (r"\d", "fault state over the range"),
        "0-KWH RATE": (PERCENT, "delivered no energy"),
        "REBOOTS": (r"[\d,]+", "BootNotification"),
        "AUTH-DENIAL RATE": (PERCENT, "authorisation attempts refused"),
    }

    # The section eyebrows, in render order. They are the page's spine: if one
    # is missing the whole section behind it failed to render.
    SECTIONS = ["TREND", "RELIABILITY", "WATCHLIST", "SITES", "ALERTS"]

    # The trend chart's series legend, and the uptime-distribution buckets.
    # The band labels use en dashes, which is why they are spelled out here
    # rather than built from a range.
    TREND_SERIES = ["Uptime %", "Serviceable %", "Charging", "Idle (EV)",
                    "Transitional"]
    UPTIME_BANDS = ["<80", "80–85", "85–90", "90–95",
                    "95–97", "97–98", "98–99", "99+"]

    # "Where the time goes" accounts for every observed EVSE minute, so these
    # six shares are expected to add up to 100%.
    TIME_STATES = ["Charging", "Idle (EV)", "Transitional", "Available",
                   "Faulted", "Unaccounted"]

    # The session-outcome funnel, top to bottom.
    FUNNEL_STAGES = ["Auth attempts", "Authorised",
                     "Sessions with measured energy", "Energy delivered"]

    # The bands a watchlist score can carry, worst first.
    RISK_BANDS = ["Critical", "Elevated", "Watch", "Stable"]

    # The Sites table's column set. The first column holds the row expander and
    # the last the row's link, so both are matched loosely; the Site heading
    # carries a live count.
    SITE_COLUMNS = ["", "Site", "CPO", "Chargers", "Uptime", "Fault hours",
                    "0-kWh rate", "Reboots", "Auth-denial", ""]

    # The same table on a site's own page, one level down.
    CHARGER_COLUMNS = ["Charger", "Uptime", "Fault hours", "0-kWh rate",
                       "Reboots", "Auth-denial", ""]

    # And on a charger's page.
    CONNECTOR_COLUMNS = ["", "Connector", "Uptime", "Offline Hours",
                         "Sessions", "Energy", "Fault Hours", "0-kWh Rate"]

    PAGE_SIZES = ["10", "25", "50", "100"]

    # The date picker's presets, mapped to the `date_range` value each sends.
    # "Custom" opens a calendar instead of applying a preset, so it carries no
    # parameter of its own.
    DATE_PRESETS = {
        "Last 7 days": "last_7_days",
        "Last 30 days": "last_30_days",
        "Last 90 days": "last_90_days",
        "Month to date": "mtd",
        "All time": "all",
        "Custom": None,
    }

    # The three alert filters, each with the options it must offer.
    ALERT_FILTERS = {
        "Detector": [
            "Offline episode", "Chronic degradation", "Fault burst",
            "Session stall", "Zero kWh rate", "Card tag failure",
            "Card reader failure", "Site denial rate", "Reboot frequency",
            "Site outage", "Payout energy/revenue mismatch",
            "Payout revenue drop",
        ],
        "Severity": ["Info", "Warning", "Critical"],
        "Status": ["Open", "Resolved", "Suppressed", "Expired"],
    }

    def __init__(self, page):
        self.page = page

        # Sidebar navigation. The breadcrumb carries the same link name, so
        # this is anchored on the first match -- the sidebar renders before
        # the page content.
        self.nh_link = page.get_by_role("link", name="Network Health").first
        self.heading = page.locator("//h1[normalize-space()='Network Health']")
        self.refresh = page.get_by_role("button", name="Refresh data")

        # Page-level filters
        self.sub_org_filter = page.get_by_role(
            "button", name=re.compile(r"^Sub-Organisation\b")
        ).first
        self.date_filter = page.get_by_role(
            "button",
            name=re.compile(r"^(Last \d+ days|Month to date|All time|Custom)$"),
        ).first

        # Tiles / tooltips
        self.tooltip_buttons = page.get_by_role("button", name="More information")

        # Watchlist
        self.score_buttons = page.get_by_role(
            "button", name=re.compile(r"^Why is .+ ranked \d+$")
        )
        self.watchlist_toggle = page.get_by_role(
            "button", name=re.compile(r"^(View all \d+|Show top \d+)$")
        )
        self.open_network_status = page.get_by_role(
            "link", name="Open Network Status"
        )

        # Sites section
        self.search = page.get_by_placeholder(
            re.compile(r"^Search sites, chargers")
        )
        # Expanding a row injects a nested table inside the outer table's body,
        # so rows are taken as direct children only -- a plain "table tbody tr"
        # would also count the nested charger rows.
        self.table = page.locator("table").first
        self.rows = self.table.locator("> tbody > tr")
        self.all_rows = page.locator("table tbody tr")
        self.expand_row = page.get_by_role("button", name="Expand row")
        self.collapse_row = page.get_by_role("button", name="Collapse row")
        self.expand_all = page.get_by_role("button", name="Expand all rows")
        self.collapse_all = page.get_by_role("button", name="Collapse all rows")

        # Empty state
        self.empty_state = page.get_by_text("No sites found", exact=True)
        self.empty_clear = page.get_by_role("button", name="Clear filters")

        # Pagination
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|25|50|100)$")
        )
        self.next_page = page.get_by_role("button", name="Go to next page")
        self.prev_page = page.get_by_role("button", name="Go to previous page")

        # Detail pages
        self.back_link = page.get_by_role("link", name=re.compile(r"^Back to "))
        # Scoped to the breadcrumb: the sidebar carries a link of the same
        # name, so an unscoped lookup resolves to two.
        self.breadcrumb = page.get_by_label("Breadcrumb")
        self.breadcrumb_nh = self.breadcrumb.get_by_role(
            "link", name="Network Health", exact=True
        )

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #
    def _poll(self, predicate, timeout_ms=15000, interval_ms=250):
        """Poll `predicate` until truthy (or timeout), returning its last value.

        Every section refetches on a filter, range or page change, so state is
        polled until it settles rather than raced with a fixed sleep. The
        deadline is wall-clock so an expensive predicate cannot overrun it.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.page.wait_for_timeout(interval_ms)
        return predicate()

    def _park_mouse(self):
        """Move the pointer off the sidebar and let it collapse.

        The sidebar is fixed to the left edge and widens while hovered,
        covering the leading columns of the content. A click there is then
        intercepted by the nav and retries until it times out.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] - 40, size["height"] // 2)
        self.page.wait_for_timeout(700)

    def _blur(self):
        """Drop keyboard focus, so the sidebar stops holding itself open.

        The sidebar widens from 125px to 294px for `:focus-visible` as well as
        for hover, and the link that opened the page keeps focus. The first
        key press of the run -- the Escape that closes a tooltip -- promotes
        that focus to `:focus-visible`, the nav stays expanded for the rest of
        the test, and everything within 294px of the left edge becomes
        unclickable. That covers the leading tile of the second row, which is
        how this presents: hovers time out on "nav intercepts pointer events"
        from the fifth tile onwards while the first four are fine.
        """
        self.page.evaluate(
            "() => document.activeElement && document.activeElement.blur()"
        )

    def _dismiss_tooltip(self):
        """Close an open tooltip and let it leave the DOM.

        Parked mid-header rather than in the corner: the corner sits over the
        sidebar, which then expands and blocks the next hover.
        """
        size = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(size["width"] // 2, 30)
        self.page.keyboard.press("Escape")
        self._blur()
        self.page.wait_for_timeout(600)

    def _hover_tile(self, index):
        """Bring tile `index` into the open and hover its info icon.

        The tiles sit in a strip that scrolls sideways at this viewport, and
        Playwright's own "scroll into view" brings a tile to the *left* edge --
        which is exactly where the sidebar is pinned. The icon then sits under
        the nav and every hover is intercepted until it times out. Scrolling
        the icon to the middle of the viewport first puts it in the clear;
        parking the pointer afterwards gives the sidebar the half second it
        takes to retract before the hover lands.
        """
        icon = self.tooltip_buttons.nth(index)
        icon.evaluate(
            "e => e.scrollIntoView({block: 'center', inline: 'center'})"
        )
        self.page.wait_for_timeout(400)
        self._park_mouse()
        icon.hover()

    def _open_row(self, row):
        """Follow a table row to its own page.

        Only the chevron in the trailing cell is a link -- clicking the row
        itself does nothing at all, so a test that clicked the row and waited
        for a URL would sit there until it timed out. That cell sits past the
        right edge at this viewport, so the table is scrolled to it first.
        """
        link = row.get_by_role("link")
        assert link.count() == 1, (
            f"expected one link on the row, found {link.count()}"
        )
        link.first.scroll_into_view_if_needed()
        self.page.wait_for_timeout(300)
        link.first.click()

    def _body(self):
        return self.page.locator("body").inner_text() or ""

    def _section(self, name, length=2500):
        """The rendered text of one section, found by its eyebrow label."""
        body = self._body()
        start = body.find(f"\n{name}\n")
        assert start != -1, f"the {name} section never rendered"
        return body[start:start + length]

    def _tile_text(self, index, label):
        """The label and value of tile `index`, as rendered, or "" if not ready.

        The tiles carry no stable class of their own, so each is reached from
        its info icon and the DOM is walked upwards to the tile body. The climb
        stops at the first container holding *both* this tile's label and a
        digit outside it: stopping at "any digit" instead lets a tile whose
        value has not rendered yet climb clean out of the tile and return the
        whole page -- and would stop dead on the label node of "0-KWH RATE",
        whose own name carries a digit.

        The label is compared against the rendered text because CSS uppercases
        it -- the DOM string is title case and would not match.
        """
        return self.tooltip_buttons.nth(index).evaluate("""(e, label) => {
            let n = e.closest('div');
            for (let i = 0; i < 6 && n; i++) {
                const t = (n.innerText || '').replace(/\\n/g, ' ');
                if (t.includes(label) && /\\d/.test(t.replace(label, ''))) {
                    return t;
                }
                n = n.parentElement;
            }
            return '';
        }""", label)

    def _tiles_loaded(self):
        """True once every KPI tile has painted a value of the right shape."""
        if self.tooltip_buttons.count() < len(self.TILES):
            return False
        for index, (label, (pattern, _)) in enumerate(self.TILES.items()):
            text = self._tile_text(index, label)
            if not text or not re.search(pattern, text.replace(label, "", 1)):
                return False
        return True

    def _site_count(self):
        """The count the table header advertises, from "Site (4)"."""
        header = self.table.locator("thead th").nth(1).inner_text() or ""
        match = re.search(r"\((\d+)\)", header)
        return int(match.group(1)) if match else None

    def _names(self):
        """The site name in each row, top to bottom."""
        try:
            return [
                (r.locator("td").nth(1).inner_text() or "").strip().split("\n")[0]
                for r in self.rows.all()
            ]
        except Exception:
            # The table re-renders as its refetch lands; treat a read that
            # catches it mid-repaint as "not settled yet".
            return []

    def _loaded(self):
        """True once the Sites table holds real rows rather than skeletons."""
        return self.rows.count() > 0 and all(self._names())

    def _settled_names(self, timeout_ms=15000):
        """The row order, once it has stopped changing.

        The table repaints a moment after its refetch resolves, so a baseline
        captured immediately after a previous action can still be the old
        order. This waits for two consecutive identical reads.
        """
        previous = None
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            current = self._names()
            if current and current == previous:
                return current
            previous = current
            self.page.wait_for_timeout(400)
        return self._names()

    def _headers(self, table=None):
        table = table if table is not None else self.table
        return [
            (h.inner_text() or "").strip()
            for h in table.locator("thead th").all()
        ]

    def _assert_columns(self, expected, table=None):
        headers = self._headers(table)
        assert len(headers) == len(expected), (
            f"expected {len(expected)} columns, got {len(headers)}: {headers}"
        )
        for want, got in zip(expected, headers):
            assert got.startswith(want), (
                f"expected a {want!r} column, found {got!r}"
            )
        log.info("Table columns: %s", headers)

    # ----------------------------------------------------------------- #
    # Open
    # ----------------------------------------------------------------- #
    def open_page(self):
        log.info("Opening Network Health")
        self.nh_link.click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-health"), timeout=20000
        )
        self.heading.wait_for(state="visible", timeout=20000)
        assert self._poll(self._loaded, timeout_ms=60000), (
            "the site coverage table never loaded"
        )
        # The tiles paint from their own summary call, which can land after the
        # table; wait for every one to carry a value before reading any.
        assert self._poll(self._tiles_loaded, timeout_ms=60000), (
            "the KPI tiles never finished loading"
        )
        # Clicking the sidebar link leaves the pointer on the nav, which stays
        # expanded and covers the leading edge of the content -- and leaves
        # the link focused, which does the same again on the first key press.
        self._park_mouse()
        self._blur()
        log.info("Network Health loaded with %s site row(s)", self.rows.count())

    def check_header(self):
        """The page names itself, its data cut-off and every section."""
        expect(self.heading).to_be_visible()

        # The refresh control doubles as the data-freshness stamp: whatever the
        # numbers below say, they are only as current as this date.
        stamp = (self.refresh.inner_text() or "").strip()
        assert re.match(r"Data through \d{1,2} \w{3} \d{4}$", stamp), (
            f"the refresh control does not stamp the data cut-off: {stamp!r}"
        )
        log.info("Header: %s", stamp)

        body = self._body()
        for section in self.SECTIONS:
            assert f"\n{section}\n" in body, (
                f"the {section} section never rendered"
            )
        log.info("All %s sections rendered: %s",
                 len(self.SECTIONS), ", ".join(self.SECTIONS))

    # ----------------------------------------------------------------- #
    # KPI tiles
    # ----------------------------------------------------------------- #
    def check_kpi_tiles(self):
        """All eight KPI tiles render a value of the right shape."""
        count = self.tooltip_buttons.count()
        assert count == len(self.TILES), (
            f"expected {len(self.TILES)} KPI tiles, found {count}"
        )
        for index, (label, (pattern, _)) in enumerate(self.TILES.items()):
            text = self._tile_text(index, label)
            assert label in text, (
                f"tile {index} should be {label!r} but reads {text[:120]!r}"
            )
            value = text.replace(label, "", 1).strip()
            assert re.search(pattern, value), (
                f"the {label} tile shows {value!r}, which does not match the "
                f"expected shape {pattern!r}"
            )
            log.info("Tile %-18s -> %s", label, value[:60])

    def check_tile_tooltips(self):
        """Each tile's info icon explains what that tile measures."""
        tooltip = self.page.get_by_role("tooltip")
        for index, (label, (_, phrase)) in enumerate(self.TILES.items()):
            # Wait for the previous tooltip to leave the DOM first: it lingers
            # while it fades, and reading through that window returns the
            # *previous* tile's text, silently shifting every result by one.
            assert self._poll(lambda: tooltip.count() == 0, timeout_ms=8000), (
                f"the previous tooltip never closed before hovering {label!r}"
            )
            self._hover_tile(index)
            assert self._poll(lambda: tooltip.count() > 0, timeout_ms=8000), (
                f"the {label} info icon raised no tooltip"
            )
            text = (tooltip.first.inner_text() or "").strip()
            assert phrase.lower() in text.lower(), (
                f"the {label} tooltip should mention {phrase!r} but reads {text!r}"
            )
            log.info("Tooltip %-18s -> %s", label, text[:60])
            self._dismiss_tooltip()
        log.info("All %s tile tooltips explain the right tile", len(self.TILES))

    # ----------------------------------------------------------------- #
    # Trend
    # ----------------------------------------------------------------- #
    def check_trend_section(self):
        """The trend chart draws its series and states the range it covers."""
        trend = self._section("TREND")
        assert "Network trend" in trend, "the trend chart has no title"
        for series in self.TREND_SERIES:
            assert series in trend, (
                f"the trend legend is missing {series!r}: {trend[:200]!r}"
            )
        # The subtitle is the range the chart actually plotted, which is how a
        # chart left on a stale range is caught.
        span = re.search(r"\d{1,2} \w{3} – \d{1,2} \w{3}", trend)
        assert span, f"the trend chart states no date span: {trend[:200]!r}"
        log.info("Network trend covers %s with %s series",
                 span.group(0), len(self.TREND_SERIES))

    def check_uptime_distribution(self):
        """The distribution histogram buckets every scored charger.

        The footnote is the point of this panel: it says how many chargers
        could be scored at all, and the bars are only meaningful next to it.
        """
        trend = self._section("TREND", length=3000)
        assert "Uptime distribution" in trend, "the distribution chart is missing"
        for band in self.UPTIME_BANDS:
            assert band in trend, (
                f"the distribution is missing the {band!r} band"
            )
        for series in ("Reachable", "Serviceable"):
            assert series in trend, (
                f"the distribution is missing its {series!r} series"
            )

        sla = re.search(
            r"(\d+) of (\d+) scored chargers sit below the ([\d.]+)% SLA floor",
            trend,
        )
        assert sla, f"the distribution states no SLA breach count: {trend[:400]!r}"
        below, scored, floor = int(sla.group(1)), int(sla.group(2)), sla.group(3)
        assert below <= scored, (
            f"{below} chargers are below the floor out of {scored} scored"
        )

        scope = re.search(
            r"Bands cover the (\d+) of (\d+) chargers in scope", trend
        )
        assert scope, f"the distribution does not state its scope: {trend[:600]!r}"
        assert int(scope.group(1)) == scored, (
            f"the SLA line scores {scored} chargers but the scope line covers "
            f"{scope.group(1)}"
        )
        log.info("Uptime distribution: %s of %s scored chargers below the %s%% "
                 "floor, out of %s in the estate",
                 below, scored, floor, scope.group(2))

    # ----------------------------------------------------------------- #
    # Reliability
    # ----------------------------------------------------------------- #
    def check_time_breakdown(self):
        """"Where the time goes" accounts for every observed EVSE minute.

        The six shares are asserted to add up to 100%, which is the claim the
        panel makes in its own footnote -- a breakdown that silently drops
        faulted time would still look plausible read state by state.
        """
        section = self._section("RELIABILITY", length=2000)
        assert "Where the time goes" in section, (
            "the time breakdown panel is missing"
        )
        shares = {}
        for state in self.TIME_STATES:
            match = re.search(
                rf"{re.escape(state)}\n[^\n]*\n([\d.]+)%", section
            )
            assert match, (
                f"the time breakdown states no share for {state!r}: "
                f"{section[:400]!r}"
            )
            shares[state] = float(match.group(1))
        total = sum(shares.values())
        # Each share is rounded to two decimals, so six of them can drift by a
        # few hundredths without anything being wrong.
        assert abs(total - 100) < 0.5, (
            f"the six shares add up to {total:.2f}%, not 100%: {shares}"
        )
        log.info("Where the time goes -> %s (total %.2f%%)", shares, total)

    def check_session_outcomes(self):
        """The session-outcome funnel narrows from attempts to energy."""
        section = self._section("RELIABILITY", length=3000)
        assert "Session outcomes" in section, "the session funnel is missing"
        for stage in self.FUNNEL_STAGES:
            assert stage in section, (
                f"the session funnel is missing its {stage!r} stage"
            )
        # Each stage carries its count above its label, and each pair must
        # narrow -- a funnel where a later stage counts more than an earlier
        # one is not a funnel.
        for first, second in (("Auth attempts", "Authorised"),
                              ("Sessions with measured energy",
                               "Energy delivered")):
            counts = []
            for stage in (first, second):
                match = re.search(
                    rf"([\d,]+)\n{re.escape(stage)} ", section
                )
                assert match, (
                    f"the {stage!r} stage carries no count: {section[:600]!r}"
                )
                counts.append(int(match.group(1).replace(",", "")))
            assert counts[0] >= counts[1], (
                f"the funnel widens: {first} = {counts[0]} but {second} = "
                f"{counts[1]}"
            )
            log.info("Funnel %s (%s) -> %s (%s)",
                     first, counts[0], second, counts[1])

    def check_model_reliability(self):
        """Reliability by model ranks each model against its fleet share."""
        section = self._section("RELIABILITY", length=4000)
        assert "Reliability by model" in section, (
            "the per-model reliability panel is missing"
        )
        for series in ("Share of fleet", "Share of fault hours"):
            assert series in section, (
                f"the model panel is missing its {series!r} series"
            )
        # Every listed model states how many fault hours it takes relative to
        # its size; that multiple is the whole point of the panel.
        multiples = re.findall(r"([\d.]+)× expected", section)
        assert multiples, (
            f"no model states an expected-fault multiple: {section[:600]!r}"
        )
        log.info("Reliability by model -> %s model(s), worst %sx expected",
                 len(multiples), max(float(m) for m in multiples))

    def check_uptime_by_hour(self):
        """The hour-of-day heatmap covers a full week of hours."""
        section = self._section("RELIABILITY", length=5000)
        assert "Uptime by hour" in section, "the hour heatmap is missing"
        for label in ("Worse", "Better"):
            assert label in section, (
                f"the heatmap has no {label!r} end to its scale"
            )
        days = [d for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
                if d in section]
        assert len(days) == 7, (
            f"the heatmap covers {len(days)} day(s), not a full week: {days}"
        )
        assert re.search(r"Hours are UTC", section), (
            "the heatmap does not say which timezone its hours are in"
        )
        log.info("Uptime by hour covers %s days, hours in UTC", len(days))

    # ----------------------------------------------------------------- #
    # Watchlist
    # ----------------------------------------------------------------- #
    def check_watchlist(self):
        """The watchlist ranks chargers worst-first, each with a scored band."""
        section = self._section("WATCHLIST", length=4000)
        assert "At-risk chargers" in section, "the watchlist has no title"

        # The escape hatch to the live view. Asserted on the href because the
        # link's whole value is that it lands on the faulted filter already
        # applied.
        expect(self.open_network_status).to_be_visible()
        href = self.open_network_status.get_attribute("href") or ""
        assert "/operations/network-status" in href and "faulted" in href, (
            f"'Open Network Status' does not land on the faulted view: {href!r}"
        )

        count = self.score_buttons.count()
        assert count > 0, "the watchlist lists no chargers"

        scores, ranks = [], []
        for index in range(count):
            button = self.score_buttons.nth(index)
            label = button.get_attribute("aria-label") or ""
            rank = int(re.search(r"ranked (\d+)$", label).group(1))
            score = int((button.inner_text() or "0").strip())
            ranks.append(rank)
            scores.append(score)
        assert ranks == list(range(1, count + 1)), (
            f"the watchlist ranks are not 1..{count}: {ranks}"
        )
        # Worst first: the ranking is only usable if the scores agree with it.
        assert scores == sorted(scores, reverse=True), (
            f"the watchlist is ranked worst-first but its scores are not "
            f"descending: {scores}"
        )
        bands = set(re.findall(r"|".join(self.RISK_BANDS), section))
        assert bands, f"no charger carries a risk band: {section[:400]!r}"

        footer = re.search(r"Showing (\d+) of (\d+) scored chargers", section)
        assert footer, f"the watchlist has no footer count: {section[-400:]!r}"
        assert int(footer.group(1)) == count, (
            f"the footer claims {footer.group(1)} chargers but {count} are drawn"
        )
        log.info("Watchlist shows %s of %s scored charger(s), scores %s..%s, "
                 "bands %s", count, footer.group(2), scores[0], scores[-1],
                 sorted(bands))

    def check_score_explainer(self):
        """The worst charger's score opens an explainer that justifies it.

        The panel is the page's only account of *why* a charger is ranked
        where it is, so it has to carry both score components and the
        observation window they were computed over.
        """
        button = self.score_buttons.first
        label = button.get_attribute("aria-label")
        log.info("Opening the score explainer: %s", label)
        self._park_mouse()
        button.click()
        assert self._poll(
            lambda: self.page.get_by_role("dialog").count() > 0
                    or self.page.get_by_role("tooltip").count() > 0,
            timeout_ms=10000,
        ), "the risk score opened no explainer"

        panel = (self.page.get_by_role("dialog").last.inner_text()
                 if self.page.get_by_role("dialog").count()
                 else self.page.get_by_role("tooltip").last.inner_text()) or ""
        for part in ("Persistence", "Responsiveness"):
            assert part in panel, (
                f"the explainer does not break the score into {part!r}: "
                f"{panel[:300]!r}"
            )
        for metric in ("Offline, 7d", "Faulted, 7d", "Offline, 28d",
                       "Faulted, 28d", "Heavy offline days"):
            assert metric in panel, (
                f"the explainer omits {metric!r}: {panel[:400]!r}"
            )
        assert re.search(r"rank \d+ of \d+", panel), (
            f"the explainer does not state the charger's standing: {panel[:200]!r}"
        )
        assert re.search(r"Scored on \d+ observed days", panel), (
            f"the explainer does not state its observation window: "
            f"{panel[:400]!r}"
        )
        log.info("Score explainer: %r", panel.replace("\n", " ")[:110])
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(700)

    def expand_watchlist(self):
        """"View all" grows the watchlist to every scored charger, and back."""
        before = self.score_buttons.count()
        toggle = self.watchlist_toggle.first
        label = (toggle.inner_text() or "").strip()
        total = int(re.search(r"(\d+)$", label).group(1))
        assert total > before, (
            f"the watchlist already shows all {before} chargers, so "
            f"{label!r} has nothing to expand"
        )

        log.info("Expanding the watchlist (%s)", label)
        self._park_mouse()
        toggle.click()
        assert self._poll(
            lambda: self.score_buttons.count() == total, timeout_ms=20000
        ), (
            f"expected {total} chargers after {label!r}, got "
            f"{self.score_buttons.count()}"
        )
        assert f"Showing {total} of {total} scored chargers" in self._body(), (
            "the watchlist footer was not updated when it expanded"
        )

        collapse = self.watchlist_toggle.first
        collapse_label = (collapse.inner_text() or "").strip()
        assert collapse_label.startswith("Show top"), (
            f"expected the toggle to offer a way back, it reads "
            f"{collapse_label!r}"
        )
        log.info("Collapsing the watchlist again (%s)", collapse_label)
        self._park_mouse()
        collapse.click()
        assert self._poll(
            lambda: self.score_buttons.count() == before, timeout_ms=20000
        ), (
            f"expected {before} chargers after {collapse_label!r}, got "
            f"{self.score_buttons.count()}"
        )

    # ----------------------------------------------------------------- #
    # Sites table
    # ----------------------------------------------------------------- #
    def check_sites_table(self):
        """The coverage table renders its columns, its count and its metrics."""
        self._assert_columns(self.SITE_COLUMNS)

        total = self._site_count()
        assert total is not None, (
            f"the Site header carries no count: "
            f"{self.table.locator('thead th').nth(1).inner_text()!r}"
        )
        assert self.rows.count() <= total, (
            f"the header claims {total} sites but {self.rows.count()} rows "
            f"are drawn"
        )

        # Every row names a site, states its CPO and carries a full metric set;
        # a row that lost its numbers would still read plausibly without this.
        for row in self.rows.all():
            cells = [c.inner_text().strip() for c in row.locator("td").all()]
            name, cpo = cells[1], cells[2]
            assert re.search(r"ID:\s*\S+", name), (
                f"a site row states no ID: {name!r}"
            )
            assert cpo, f"the site {name.splitlines()[0]!r} names no CPO"
            assert re.fullmatch(r"\d+", cells[3]), (
                f"{name.splitlines()[0]!r} shows a non-numeric charger count: "
                f"{cells[3]!r}"
            )
            assert re.fullmatch(self.PERCENT, cells[4]), (
                f"{name.splitlines()[0]!r} shows an unreadable uptime: "
                f"{cells[4]!r}"
            )
            assert re.fullmatch(r"(\d+d\s*)?(\d+h\s*)?(\d+m)?", cells[5]) \
                   and cells[5], (
                f"{name.splitlines()[0]!r} shows an unreadable fault-hours "
                f"figure: {cells[5]!r}"
            )
            for label, value in (("0-kWh rate", cells[6]),
                                 ("Auth-denial", cells[8])):
                assert re.fullmatch(self.PERCENT, value), (
                    f"{name.splitlines()[0]!r} shows an unreadable {label}: "
                    f"{value!r}"
                )
            assert re.fullmatch(r"[\d,]+", cells[7]), (
                f"{name.splitlines()[0]!r} shows a non-numeric reboot count: "
                f"{cells[7]!r}"
            )
        log.info("Sites table shows %s of %s site(s)", self.rows.count(), total)

    def search_sites(self):
        """Search narrows the table, and a no-match query shows the empty state."""
        before = self._settled_names()
        assert len(before) > 1, (
            "the coverage table has only one site, so a search cannot be shown "
            "to narrow it"
        )

        log.info("Searching for %r", SEARCH_TERM)
        self._park_mouse()
        self.search.fill(SEARCH_TERM)
        self.page.wait_for_url(re.compile(r"[?&]search="), timeout=20000)
        assert self._poll(
            lambda: 0 < len(self._names()) < len(before), timeout_ms=25000
        ), (
            f"the search did not narrow the table from {len(before)} site(s) "
            f"(now {self._names()})"
        )
        matched = self._names()
        assert all(SEARCH_TERM.lower() in n.lower() for n in matched), (
            f"the search returned sites that do not match {SEARCH_TERM!r}: "
            f"{matched}"
        )
        # The header count follows the query rather than the whole estate.
        assert self._poll(lambda: self._site_count() == len(matched)), (
            f"the header counts {self._site_count()} site(s) but "
            f"{len(matched)} row(s) match"
        )
        log.info("Search %r -> %s", SEARCH_TERM, matched)

        log.info("Searching for a term that matches nothing")
        self.search.fill(NO_MATCH)
        assert self._poll(lambda: self._site_count() == 0, timeout_ms=25000), (
            f"a search for {NO_MATCH!r} still counts {self._site_count()} site(s)"
        )
        expect(self.empty_state).to_be_visible()
        # An empty table that does not say what to do next is a dead end, so
        # the guidance and its escape hatch are part of the state.
        assert "Try widening or clearing them" in self._body(), (
            "the empty state gives no way out of a query that matched nothing"
        )
        expect(self.empty_clear).to_be_visible()
        assert "Showing 0 results" in self._body(), (
            "the footer does not report an empty result"
        )
        log.info("Empty state reached for %r", NO_MATCH)

        log.info("Clearing the search")
        self.search.fill("")
        assert self._poll(
            lambda: self._names() == before, timeout_ms=25000
        ), (
            f"clearing the search did not restore the {len(before)} site(s): "
            f"{self._names()}"
        )
        assert self._poll(lambda: "search=" not in self.page.url), (
            f"the search is still in the URL after clearing it: {self.page.url}"
        )

    def filter_by_sub_org(self):
        """Apply the sub-organisation filter through its popover, then clear it.

        This is a multi-select: ticking an option changes nothing until Apply
        is pressed, and dismissing the popover discards the tick. That is the
        whole point of this check -- a test that clicked the option and
        expected the table to move would pass on a broken Apply.
        """
        before = self._settled_names()

        log.info("Opening the Sub-Organisation filter and ticking %r", SUB_ORG)
        self._park_mouse()
        self.sub_org_filter.click()
        self.page.wait_for_timeout(1200)
        popover = self.page.get_by_role("dialog").last
        expect(popover.get_by_role("button", name="Apply")).to_be_visible()
        expect(popover.get_by_role("button", name="Clear")).to_be_visible()

        search = popover.locator("input[type=text]")
        assert search.count(), "the sub-organisation filter offers no search"
        search.first.fill(SUB_ORG)
        self.page.wait_for_timeout(1200)

        option = self.page.get_by_role("option", name=SUB_ORG, exact=True)
        assert self._poll(lambda: option.count() == 1, timeout_ms=10000), (
            f"the sub-organisation filter does not offer {SUB_ORG!r}"
        )
        option.click()
        expect(option).to_have_attribute("aria-selected", "true")

        # Nothing has changed yet -- the filter only takes effect on Apply.
        assert self._names() == before, (
            "ticking an option changed the table before Apply was pressed"
        )
        popover.get_by_role("button", name="Apply").click()
        assert self._poll(
            lambda: self._names() and self._names() != before, timeout_ms=30000
        ), (
            f"applying the {SUB_ORG!r} filter did not change the "
            f"{len(before)} site(s) shown"
        )
        after = self._settled_names()
        assert len(after) < len(before), (
            f"the {SUB_ORG!r} filter did not narrow the table from "
            f"{len(before)} site(s): {after}"
        )
        log.info("Sub-Organisation %r -> %s", SUB_ORG, after)

        log.info("Clearing the Sub-Organisation filter")
        self._park_mouse()
        self.sub_org_filter.click()
        self.page.wait_for_timeout(1200)
        self.page.get_by_role("dialog").last.get_by_role(
            "button", name="Clear"
        ).click()
        assert self._poll(
            lambda: self._names() == before, timeout_ms=30000
        ), (
            f"clearing the filter did not restore the {len(before)} site(s): "
            f"{self._names()}"
        )
        if self.page.get_by_role("dialog").count():
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(600)

    def check_date_range(self):
        """The range picker offers its presets and re-cuts the whole page.

        Asserted on the tile captions rather than only on the trigger: the
        control is meant to change what every figure is measured over, and a
        picker that updates its own label and nothing else is exactly the
        failure worth catching.
        """
        expect(self.date_filter).to_have_text(DEFAULT_RANGE)

        log.info("Opening the date-range picker")
        self._park_mouse()
        self.date_filter.click()
        self.page.wait_for_timeout(1200)
        popover = self.page.get_by_role("dialog").last
        listed = (popover.inner_text() or "")
        for preset in self.DATE_PRESETS:
            assert preset in listed, (
                f"the range picker does not offer {preset!r}: {listed!r}"
            )
        log.info("Range picker offers all %s presets", len(self.DATE_PRESETS))

        log.info("Switching the range to %r", OTHER_RANGE)
        popover.get_by_text(OTHER_RANGE, exact=True).first.click()
        self.page.wait_for_url(
            re.compile(rf"[?&]date_range={self.DATE_PRESETS[OTHER_RANGE]}"),
            timeout=20000,
        )
        assert self._poll(
            lambda: (self.date_filter.inner_text() or "").strip() == OTHER_RANGE,
            timeout_ms=20000,
        ), (
            f"the range trigger still reads "
            f"{self.date_filter.inner_text()!r}"
        )
        caption = OTHER_RANGE.lower()
        assert self._poll(lambda: caption in self._body(), timeout_ms=30000), (
            f"the tiles are still captioned for the old range, not {caption!r}"
        )
        assert self._poll(self._loaded, timeout_ms=40000), (
            f"the coverage table never reloaded under {OTHER_RANGE!r}"
        )
        log.info("%r applied -> %s site(s)", OTHER_RANGE, self.rows.count())

        log.info("Restoring the range to %r", DEFAULT_RANGE)
        self._park_mouse()
        self.date_filter.click()
        self.page.wait_for_timeout(1200)
        self.page.get_by_role("dialog").last.get_by_text(
            DEFAULT_RANGE, exact=True
        ).first.click()
        assert self._poll(
            lambda: (self.date_filter.inner_text() or "").strip() == DEFAULT_RANGE,
            timeout_ms=20000,
        ), "the range was not restored"
        assert self._poll(self._loaded, timeout_ms=40000)

    def expand_site_row(self):
        """Expand a site into its chargers, then collapse it again.

        Expanding injects a nested table, so the check is that the total row
        count grows and the nested rows really are that site's chargers.
        """
        base = self.all_rows.count()
        chargers = int(
            self.rows.first.locator("td").nth(3).inner_text().strip()
        )

        log.info("Expanding the first site row (%s charger(s) expected)",
                 chargers)
        self._park_mouse()
        self.expand_row.first.click()
        assert self._poll(
            lambda: self.page.locator("table").count() == 2, timeout_ms=20000
        ), "expanding a site did not inject its charger table"
        expect(self.collapse_row.first).to_be_visible()

        nested = self.page.locator("table").nth(1)
        # The nested table paints skeleton rows first and fills them once its
        # own fetch lands, so wait for real content rather than reading the
        # placeholder (which is just tabs).
        assert self._poll(
            lambda: re.search(
                r"ID:\s*\S+",
                nested.locator("> tbody > tr").first.inner_text() or "",
            ),
            timeout_ms=25000,
        ), (
            "the charger rows never loaded: "
            f"{(nested.locator('> tbody > tr').first.inner_text() or '')[:80]!r}"
        )
        assert self.all_rows.count() > base, (
            "expanding a site added no charger rows"
        )
        assert nested.locator("> tbody > tr").count() == chargers, (
            f"the row claims {chargers} charger(s) but expands to "
            f"{nested.locator('> tbody > tr').count()}"
        )
        log.info("Site expanded to %s charger(s): %r", chargers,
                 nested.locator("> tbody > tr").first.inner_text()
                 .replace("\n", " ")[:70])

        log.info("Collapsing the site row again")
        self._park_mouse()
        self.collapse_row.first.click()
        assert self._poll(
            lambda: self.all_rows.count() == base, timeout_ms=15000
        ), (
            f"expected {base} row(s) after collapsing, got {self.all_rows.count()}"
        )
        assert self.page.locator("table").count() == 1, (
            "collapsing the site left a nested table behind"
        )

    def expand_all_site_rows(self):
        """Expand every site at once, then collapse them all."""
        base = self.all_rows.count()
        sites = self.rows.count()

        log.info("Expanding all %s site rows", sites)
        self._park_mouse()
        self.expand_all.click()
        assert self._poll(
            lambda: self.page.locator("table").count() == sites + 1,
            timeout_ms=30000,
        ), (
            f"expected a nested table per site ({sites}), got "
            f"{self.page.locator('table').count() - 1}"
        )
        assert self.all_rows.count() > base
        log.info("All rows expanded -> %s row(s) across %s table(s)",
                 self.all_rows.count(), self.page.locator("table").count())

        log.info("Collapsing all rows")
        self._park_mouse()
        self.collapse_all.click()
        assert self._poll(
            lambda: self.all_rows.count() == base, timeout_ms=25000
        ), (
            f"expected {base} row(s) after collapsing all, got "
            f"{self.all_rows.count()}"
        )
        assert self.page.locator("table").count() == 1

    def paginate_sites(self):
        """The pager offers every page size and states an honest footer.

        Only a handful of sites have coverage on staging, so this cannot rely
        on there being a second page. What it can always check is that the
        selector offers its four sizes and that the footer's total agrees with
        the count in the header.
        """
        current = (self.page_size.first.inner_text() or "").strip()
        log.info("Opening the page-size selector (currently %s)", current)
        self._park_mouse()
        self.page_size.first.click()
        self.page.wait_for_timeout(900)
        listed = [
            o.inner_text().strip() for o in self.page.get_by_role("option").all()
        ]
        assert listed == self.PAGE_SIZES, (
            f"the page-size selector offers {listed}, expected {self.PAGE_SIZES}"
        )

        target = "50" if current != "50" else "100"
        log.info("Switching the page size from %s to %s", current, target)
        self.page.get_by_role("option", name=target, exact=True).click()
        assert self._poll(
            lambda: (self.page_size.first.inner_text() or "").strip() == target,
            timeout_ms=20000,
        ), "the page size was not applied"
        assert self._poll(self._loaded, timeout_ms=30000), (
            f"the table never reloaded at page size {target}"
        )

        total = self._site_count()
        footer = re.search(
            r"Showing (\d+)[–-](\d+) of (\d+)", self._body()
        )
        assert footer, f"the pager states no footer count"
        assert int(footer.group(3)) == total, (
            f"the header counts {total} site(s) but the footer counts "
            f"{footer.group(3)}"
        )
        assert self.rows.count() == int(footer.group(2)) - int(footer.group(1)) + 1, (
            f"the footer says {footer.group(0)!r} but {self.rows.count()} "
            f"row(s) are drawn"
        )
        log.info("Page size %s -> %r across %s row(s)",
                 target, footer.group(0), self.rows.count())

        if self.next_page.is_enabled():
            first_page = self._settled_names()
            log.info("Paging forward and back")
            self._park_mouse()
            self.next_page.click()
            assert self._poll(
                lambda: self._names() and self._names() != first_page,
                timeout_ms=25000,
            ), "page 2 shows the same sites as page 1"
            self._park_mouse()
            self.prev_page.click()
            assert self._poll(
                lambda: self._names() == first_page, timeout_ms=25000
            ), "going back did not restore page 1"
        else:
            log.info("Every site fits on one page -- nothing to page through")

        log.info("Restoring the page size to %s", current)
        self._park_mouse()
        self.page_size.first.click()
        self.page.wait_for_timeout(900)
        self.page.get_by_role("option", name=current, exact=True).click()
        assert self._poll(
            lambda: (self.page_size.first.inner_text() or "").strip() == current,
            timeout_ms=20000,
        ), "the page size was not restored"

    # ----------------------------------------------------------------- #
    # Alerts
    # ----------------------------------------------------------------- #
    def check_alerts_section(self):
        """The alert history offers its three filters and states its scope.

        Staging carries no alert episodes, so the list itself is empty. That is
        checked as a real empty state rather than skipped: the panel is
        required to say *why* it is empty, and specifically to warn that an
        empty list is not evidence that monitoring is running.
        """
        section = self._section("ALERTS", length=1500)
        assert "Alert history" in section, "the alert panel has no title"

        for label, options in self.ALERT_FILTERS.items():
            trigger = self.page.get_by_role(
                "button", name=re.compile(rf"^{re.escape(label)}\b")
            ).last
            log.info("Opening the %r alert filter", label)
            self._park_mouse()
            trigger.click()
            assert self._poll(
                lambda: self.page.get_by_role("option").count() > 0,
                timeout_ms=10000,
            ), f"the {label!r} filter opened with no options"
            listed = [
                o.inner_text().strip()
                for o in self.page.get_by_role("option").all()
            ]
            assert listed == options, (
                f"the {label!r} filter offers {listed}, expected {options}"
            )
            log.info("Filter %-8s -> %s option(s)", label, len(listed))
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(700)

        assert "No alerts in range" in section, (
            f"staging has no alert episodes, so the panel should say so: "
            f"{section!r}"
        )
        assert "does not confirm ongoing monitoring is active" in section, (
            f"the empty state does not warn that it is not proof of "
            f"monitoring: {section!r}"
        )
        log.info("Alert history is empty for this range, and says why")

    # ----------------------------------------------------------------- #
    # Site page
    # ----------------------------------------------------------------- #
    def open_site_page(self):
        """Open the first site's own page from the coverage table."""
        name = self._names()[0]
        log.info("Opening the site page for %r", name)
        self._park_mouse()
        self._open_row(self.rows.first)
        self.page.wait_for_url(
            re.compile(r"/operations/network-health/[0-9a-f-]{36}$"),
            timeout=30000,
        )
        assert self._poll(self._loaded, timeout_ms=40000), (
            f"the charger table on {name!r} never loaded"
        )
        assert self._poll(self._tiles_loaded, timeout_ms=40000), (
            f"the KPI tiles on {name!r} never loaded"
        )
        self._park_mouse()

        # The site names itself in the breadcrumb, and the way back is spelled
        # out rather than left to the browser's Back button.
        body = self._body()
        assert name in body, (
            f"the site page does not name {name!r}: {body[:200]!r}"
        )
        expect(self.page.get_by_role(
            "link", name="Back to Network Health"
        )).to_be_visible()
        expect(self.breadcrumb_nh).to_be_visible()

        assert "Uptime trend" in body, (
            "the site page draws no uptime trend"
        )
        self._assert_columns(self.CHARGER_COLUMNS)

        header = self.table.locator("thead th").first.inner_text() or ""
        count = re.search(r"\((\d+)\)", header)
        assert count, f"the Charger header carries no count: {header!r}"
        assert self.rows.count() == int(count.group(1)), (
            f"the header claims {count.group(1)} charger(s) but "
            f"{self.rows.count()} rows are drawn"
        )
        for row in self.rows.all():
            cells = [c.inner_text().strip() for c in row.locator("td").all()]
            assert re.search(r"ID:\s*\S+", cells[0]), (
                f"a charger row states no ID: {cells[0]!r}"
            )
            assert re.fullmatch(self.PERCENT, cells[1]), (
                f"{cells[0].splitlines()[0]!r} shows an unreadable uptime: "
                f"{cells[1]!r}"
            )
        log.info("Site page for %r lists %s charger(s)", name, self.rows.count())
        return name

    def open_charger_page(self, site):
        """Open the first charger's page from the site page."""
        charger = (self.rows.first.locator("td").first.inner_text() or "")
        model = charger.splitlines()[0].strip()
        log.info("Opening the charger page for %r", model)
        self._park_mouse()
        self._open_row(self.rows.first)
        self.page.wait_for_url(
            re.compile(
                r"/operations/network-health/[0-9a-f-]{36}/[0-9a-f-]{36}$"
            ),
            timeout=30000,
        )
        assert self._poll(self._tiles_loaded, timeout_ms=40000), (
            f"the KPI tiles on {model!r} never loaded"
        )
        # The trend card and the connector table arrive on their own fetches,
        # after the tiles. Reading the page before both have landed sees a
        # chart with no granularity toggle and no connector section at all.
        assert self._poll(
            lambda: "CONNECTORS" in self._body() and "Daily" in self._body(),
            timeout_ms=40000,
        ), (
            f"the charger page for {model!r} never finished rendering its "
            f"trend and connectors"
        )
        self._park_mouse()

        body = self._body()
        assert model in body, (
            f"the charger page does not name {model!r}: {body[:300]!r}"
        )
        expect(self.page.get_by_role(
            "link", name=f"Back to {site}"
        )).to_be_visible()

        # The identity card ties the charger to its site and CPO -- this page
        # is reached from a ranked list, so "which charger is this" matters.
        for field in ("Site", "CPO", "Sessions in window", "Site uptime",
                      "Rank at site"):
            assert field in body, (
                f"the charger page omits its {field!r} field"
            )
        assert re.search(r"Rank at site\n\d+ of \d+", body), (
            "the charger page does not state its standing at the site"
        )

        # The same risk score the watchlist ranked it by, shown in full.
        assert "Risk score" in body, "the charger page shows no risk score"
        for part in ("Persistence", "Responsiveness"):
            assert part in body, (
                f"the risk score is not broken into {part!r}"
            )
        assert "changing the length of the date range does not change this "\
               "ranking" in body.replace("\n", " "), (
            "the charger page does not explain that its ranking uses a fixed "
            "scoring window"
        )

        # The trend can be read per day or per hour.
        for grain in ("Daily", "Hourly"):
            assert grain in body, (
                f"the uptime trend offers no {grain!r} view"
            )

        assert "CONNECTORS" in body, "the charger page lists no connectors"
        self._assert_columns(self.CONNECTOR_COLUMNS)
        assert self.rows.count() > 0, "the connector table is empty"
        log.info("Charger page for %r lists %s connector row(s)",
                 model, self.rows.count())

        log.info("Expanding the first connector row")
        self._park_mouse()
        self.expand_row.first.click()
        assert self._poll(
            lambda: self.collapse_row.count() > 0, timeout_ms=15000
        ), "the connector row did not expand"
        self._park_mouse()
        self.collapse_row.first.click()
        assert self._poll(
            lambda: self.collapse_row.count() == 0, timeout_ms=15000
        ), "the connector row did not collapse again"

        log.info("Returning to %r", site)
        self._park_mouse()
        self.page.get_by_role("link", name=f"Back to {site}").click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-health/[0-9a-f-]{36}$"),
            timeout=30000,
        )
        assert self._poll(self._loaded, timeout_ms=40000)

    def back_to_network_health(self):
        """The site page's back link returns to the fleet view."""
        log.info("Returning to Network Health")
        self._park_mouse()
        self.page.get_by_role("link", name="Back to Network Health").click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-health(\?|$)"), timeout=30000
        )
        self.heading.wait_for(state="visible", timeout=20000)
        assert self._poll(self._loaded, timeout_ms=40000), (
            "the coverage table never reloaded on the way back"
        )
        self._assert_columns(self.SITE_COLUMNS)

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def network_health_page(self):
        self.open_page()
        self.check_header()
        self.check_kpi_tiles()
        self.check_tile_tooltips()
        self.check_trend_section()
        self.check_uptime_distribution()
        self.check_time_breakdown()
        self.check_session_outcomes()
        self.check_model_reliability()
        self.check_uptime_by_hour()
        self.check_watchlist()
        self.check_score_explainer()
        self.expand_watchlist()
        self.check_sites_table()
        self.search_sites()
        self.filter_by_sub_org()
        self.check_date_range()
        self.expand_site_row()
        self.expand_all_site_rows()
        self.paginate_sites()
        self.check_alerts_section()
        site = self.open_site_page()
        self.open_charger_page(site)
        self.back_to_network_health()
        log.info("Network Health workflow completed")
