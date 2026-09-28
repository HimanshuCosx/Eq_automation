import logging
import re
import time
from datetime import datetime

from playwright.sync_api import expect

log = logging.getLogger("eq_automation.network_intelligence")

# The sub-organisation the filter check is pinned to. Larkfleet owns exactly
# one of the sites that have monitoring coverage on staging, so applying it
# collapses the Sites table to a single row -- a change large enough to be
# unambiguous and small enough to verify row by row.
SUB_ORG = "Larkfleet"

# The search check reuses it: the term matches that site by name, which proves
# the box really filters rather than merely accepting typing.
SEARCH_TERM = SUB_ORG

# A term no site, charger or ID can match, used to reach the empty state.
NO_MATCH = "zzzz-no-such-site"

# The range the page opens on, and the one the range check switches to.
DEFAULT_RANGE = "Last 30 days"
OTHER_RANGE = "Last 7 days"

# The alert filter checks: a severity to narrow the history to, a status that
# no Critical episode carries on staging (to reach the empty state), and a
# detector reached through the Detector filter's own search box.
ALERT_SEVERITY = "Critical"
ALERT_EMPTY_STATUS = "Suppressed"
ALERT_DETECTOR = "Reboot frequency"
ALERT_DETECTOR_QUERY = "reboot"


class network_intelligence:
    """Network Intelligence (/operations/network-intelligence).

    The diagnostic view of the estate, listed under AI & Analytics in the
    sidebar. It replaced Network Health, which now 404s: the same backend
    (`/api/v1/network-health/*`), with period-over-period deltas on the tiles,
    a site-scope selector on the coverage table, a watchlist that links to each
    charger, and an alert history that is now populated and expands into the
    detector's evidence.

    The page has eight KPI tiles over a chosen date range, a trend section, a
    reliability section that accounts for every observed EVSE minute, a ranked
    at-risk charger watchlist, a site coverage table that expands into its
    chargers, and an alert history with its own filters and pager. Sites and
    chargers each have their own page underneath this one.

    The workflow is entirely read-only -- the page creates, edits and deletes
    nothing -- so it always leaves staging exactly as it found it. It exercises
    every control: the refresh button, all eight tiles with their deltas and
    tooltips, both trend charts, all four reliability panels, the watchlist
    with its per-charger score explainer, its expand toggle and its charger
    links, the sites table with its scope selector, search, expansion and
    pagination, the sub-organisation filter (applied and cleared), the
    date-range presets and the custom calendar, the three alert filters with
    their empty state, alert expansion and paging, and the site and charger
    pages reached from the table.
    """

    # The eight KPI tiles, in render order. Each maps to the shape its value
    # must take and a phrase its tooltip must contain. The labels are rendered
    # uppercase by CSS but sit in the DOM in title case, so they are read from
    # the rendered text rather than matched as DOM strings.
    PERCENT = r"\d[\d,]*\.\d{2}%"
    # An uptime cell. Where monitoring missed part of the window, the share
    # it did observe is stated underneath -- the uptime is only as good as it.
    UPTIME_CELL = rf"{PERCENT}(\n{PERCENT} coverage)?"
    TILES = {
        "FLEET UPTIME": (PERCENT, "averaged across every charger in scope"),
        "SERVICEABLE": (PERCENT, "a driver could actually have used"),
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

    # The bands a watchlist score can carry, worst first. The watchlist only
    # shows the at-risk end; a charger page can carry any of them.
    RISK_BANDS = ["Critical", "Elevated", "Watch", "Normal", "Stable"]

    # Each tile's comparison against the previous period of the same length.
    # Rates move in percentage points; counts and durations in percent.
    DELTA_POINTS = r"\d[\d,]*\.\d pts"
    DELTA_PERCENT = r"\d[\d,]*\.\d%"
    TILE_DELTAS = {
        "FLEET UPTIME": DELTA_POINTS,
        "SERVICEABLE": DELTA_POINTS,
        "OCCUPIED": DELTA_POINTS,
        "CHARGING": DELTA_POINTS,
        "FAULT HOURS": DELTA_PERCENT,
        "0-KWH RATE": DELTA_POINTS,
        "REBOOTS": DELTA_PERCENT,
        "AUTH-DENIAL RATE": DELTA_POINTS,
    }

    # What a page says instead of deltas when there is nothing to compare to.
    NO_COMPARISON = "No OCPP data was observed in the comparison window"

    # The coverage table's two scopes. The second carries a live count.
    SCOPE_COVERED = "Sites with coverage"
    SCOPE_ALL = "All sites"

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

    # What each alert filter's trigger reads while nothing is chosen.
    ALERT_DEFAULTS = {
        "Detector": "All detectors",
        "Severity": "All severities",
        "Status": "All statuses",
    }

    def __init__(self, page):
        self.page = page

        # Sidebar navigation. The breadcrumb carries the same link name, so
        # this is anchored on the first match -- the sidebar renders before
        # the page content.
        self.ni_link = page.get_by_role(
            "link", name="Network Intelligence"
        ).first
        self.heading = page.locator(
            "//h1[normalize-space()='Network Intelligence']"
        )
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
        # Each card links to its charger's page twice over: the model name and
        # a trailing "Open <model>" chevron.
        self.watchlist_open = page.get_by_role(
            "link", name=re.compile(r"^Open (?!Network Status$).+")
        )

        # Sites section
        self.scope_filter = page.get_by_role(
            "button", name=re.compile(r"^Coverage\b")
        )
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

        # Pagination. The sites table and the alert history each carry a
        # pager, sites first, so the two are told apart by position.
        self.page_size = page.get_by_role(
            "button", name=re.compile(r"^(10|25|50|100)$")
        )
        self.next_page = page.get_by_role(
            "button", name="Go to next page"
        ).first
        self.prev_page = page.get_by_role(
            "button", name="Go to previous page"
        ).first
        self.alert_next = page.get_by_role(
            "button", name="Go to next page"
        ).last
        self.alert_prev = page.get_by_role(
            "button", name="Go to previous page"
        ).last

        # Alert history. Each episode is a disclosure button; the filter
        # triggers are disclosures too, so rows are told apart by the
        # "Opened <date>" line only an episode carries.
        self.alert_rows = page.locator("button[aria-expanded]").filter(
            has_text=re.compile(r"Opened \d")
        )
        self.alert_empty = page.get_by_text(
            "No alerts match these filters", exact=True
        )

        # Detail pages
        self.back_link = page.get_by_role("link", name=re.compile(r"^Back to "))
        # Scoped to the breadcrumb: the sidebar carries a link of the same
        # name, so an unscoped lookup resolves to two.
        self.breadcrumb = page.get_by_label("Breadcrumb")
        self.breadcrumb_ni = self.breadcrumb.get_by_role(
            "link", name="Network Intelligence", exact=True
        )

        # The fleet size the watchlist ranks against, read from its cards and
        # cross-checked on the charger page.
        self.scored_fleet = None

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

    def _wait_for_charger_page(self, model):
        """Wait for a charger page to finish rendering, however it was reached.

        The trend card and the connector table arrive on their own fetches,
        after the tiles. Reading the page before both have landed sees a chart
        with no granularity toggle and no connector section at all.
        """
        assert self._poll(self._tiles_loaded, timeout_ms=40000), (
            f"the KPI tiles on {model!r} never loaded"
        )
        assert self._poll(
            lambda: "CONNECTORS" in self._body() and "Daily" in self._body()
                    and "Risk score" in self._body(),
            timeout_ms=40000,
        ), (
            f"the charger page for {model!r} never finished rendering its "
            f"trend, risk score and connectors"
        )
        self._park_mouse()

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
        # Filters, scope and alert choices live in component state, so a run
        # that is already here (say, after a failed test) routes away first to
        # start from the page's defaults.
        if "/operations/network-intelligence" in self.page.url:
            self.page.get_by_role("link", name="Command Center").click()
            self.page.wait_for_url(re.compile(r"/operations/overview"),
                                   timeout=20000)
        log.info("Opening Network Intelligence")
        self.ni_link.click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-intelligence"), timeout=20000
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
        log.info("Network Intelligence loaded with %s site row(s)",
                 self.rows.count())

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

    def check_refresh(self):
        """The data-freshness stamp is also a button that refetches the page.

        Asserted on the request rather than on the numbers: a refresh against
        unchanged data repaints identical values, so the only honest evidence
        that it did anything is the KPI call it sends.
        """
        stamp = (self.refresh.inner_text() or "").strip()
        log.info("Refreshing the page data (%s)", stamp)
        self._park_mouse()
        with self.page.expect_response(
            lambda r: "/network-health/kpis" in r.url, timeout=30000
        ) as response:
            self.refresh.click()
        assert response.value.ok, (
            f"the refresh's KPI call failed with HTTP {response.value.status}"
        )
        assert self._poll(self._tiles_loaded, timeout_ms=40000), (
            "the KPI tiles never repainted after the refresh"
        )
        assert self._poll(self._loaded, timeout_ms=40000), (
            "the coverage table never repainted after the refresh"
        )
        self._blur()
        log.info("Refresh refetched the KPIs (HTTP %s)", response.value.status)

    def check_kpi_deltas(self):
        """Each tile compares itself with the previous period of equal length.

        Rates move in percentage points and counts in percent, and mixing the
        two up is exactly the kind of slip that still reads plausibly, so each
        tile is held to its own unit. Where there is no earlier data to compare
        with, the page has to say so rather than show a delta against zero.
        """
        if self.NO_COMPARISON in self._body():
            log.info("No comparison window on this page -- deltas are "
                     "withheld, and the page says so")
            return
        for index, (label, pattern) in enumerate(self.TILE_DELTAS.items()):
            text = self._tile_text(index, label)
            match = re.search(pattern, text)
            assert match, (
                f"the {label} tile shows no delta of the shape {pattern!r}: "
                f"{text!r}"
            )
            log.info("Delta %-18s -> %s", label, match.group(0))

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
        # The ingestion-gap band only appears when the range overlaps a known
        # OCPP outage; when it does, the funnel has to own up to it too.
        if "Ingestion gap" in trend:
            assert "ingestion gap" in self._section(
                "RELIABILITY", length=4000
            ), (
                "the trend marks an ingestion gap but the session funnel does "
                "not warn that its counters under-count across it"
            )
            log.info("The range overlaps an OCPP ingestion gap, and the "
                     "session funnel says so")
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

        # Each card restates its rank against the whole scored fleet, which
        # has to agree with the "#n" it is drawn under.
        cards = re.findall(r"#(\d+)\n.*?\nRank (\d+) of (\d+)\n", section,
                           flags=re.S)
        assert len(cards) == count, (
            f"{count} chargers are ranked but {len(cards)} card(s) state a "
            f"rank line: {cards}"
        )
        for badge, rank, _ in cards:
            assert badge == rank, (
                f"card #{badge} claims to be rank {rank}"
            )
        fleet = {int(of) for _, _, of in cards}
        assert len(fleet) == 1, (
            f"the cards disagree on the size of the scored fleet: {fleet}"
        )
        self.scored_fleet = fleet.pop()

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

    def open_watchlist_charger(self):
        """The worst-ranked charger links to its own page, which agrees.

        The watchlist and the charger page compute the score separately, so
        this cross-checks them: the charger page must restate the same score,
        the same rank and the same fleet size the watchlist card showed.
        """
        button = self.score_buttons.first
        model = re.search(
            r"^Why is (.+) ranked 1$", button.get_attribute("aria-label")
        ).group(1)
        score = int((button.inner_text() or "0").strip())

        link = self.watchlist_open.first
        href = link.get_attribute("href") or ""
        assert re.search(
            r"/operations/network-intelligence/[0-9a-f-]{36}/[0-9a-f-]{36}$",
            href,
        ), f"the watchlist card does not link to a charger page: {href!r}"
        expect(link).to_have_accessible_name(f"Open {model}")

        log.info("Opening the #1 watchlist charger %r (score %s)", model, score)
        self._park_mouse()
        link.scroll_into_view_if_needed()
        link.click()
        self.page.wait_for_url(re.compile(re.escape(href) + "$"),
                               timeout=30000)
        self._wait_for_charger_page(model)

        body = self._body()
        risk = re.search(
            r"Risk score\n.*?\n(\d+)\n(\w+)\nrank (\d+) of (\d+)", body
        )
        assert risk, (
            f"the charger page shows no risk score: "
            f"{body[body.find('Risk score'):][:200]!r}"
        )
        assert int(risk.group(1)) == score, (
            f"the watchlist scores {model!r} at {score} but its page says "
            f"{risk.group(1)}"
        )
        assert risk.group(2) in self.RISK_BANDS, (
            f"the charger page shows an unknown band {risk.group(2)!r}"
        )
        assert int(risk.group(3)) == 1, (
            f"the watchlist's #1 charger is rank {risk.group(3)} on its page"
        )
        assert int(risk.group(4)) == self.scored_fleet, (
            f"the watchlist ranks against {self.scored_fleet} chargers but the "
            f"charger page against {risk.group(4)}"
        )
        log.info("Charger page agrees: %s %s, rank 1 of %s",
                 score, risk.group(2), risk.group(4))

        log.info("Returning to Network Intelligence through the breadcrumb")
        self._park_mouse()
        self.breadcrumb_ni.click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-intelligence(\?|$)"),
            timeout=30000,
        )
        assert self._poll(self._loaded, timeout_ms=60000), (
            "the coverage table never reloaded after the breadcrumb"
        )
        assert self._poll(self._tiles_loaded, timeout_ms=60000)
        self._park_mouse()
        self._blur()

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
            assert re.fullmatch(self.UPTIME_CELL, cells[4]), (
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

    def check_coverage_scope(self):
        """The scope selector widens the table from covered sites to all.

        The page opens on sites with monitoring coverage. "All sites" adds the
        ones without, so it must count at least as many -- and restoring the
        default must bring the original rows back.
        """
        expect(self.scope_filter).to_contain_text(self.SCOPE_COVERED)
        covered = self._site_count()
        before = self._settled_names()

        log.info("Opening the coverage scope selector (%s site(s))", covered)
        self._park_mouse()
        self.scope_filter.click()
        options = self.page.get_by_role("option")
        assert self._poll(lambda: options.count() == 2, timeout_ms=10000), (
            f"the scope selector offers {options.all_inner_texts()}"
        )
        listed = [o.strip() for o in options.all_inner_texts()]
        assert listed[0] == self.SCOPE_COVERED, (
            f"the first scope is {listed[0]!r}, not {self.SCOPE_COVERED!r}"
        )
        expect(options.first).to_have_attribute("aria-selected", "true")
        everything = re.fullmatch(rf"{self.SCOPE_ALL} \((\d+)\)", listed[1])
        assert everything, f"the second scope reads {listed[1]!r}"
        total = int(everything.group(1))
        assert total >= covered, (
            f"'All sites' counts {total}, fewer than the {covered} covered"
        )

        log.info("Switching the scope to %r", listed[1])
        options.nth(1).click()
        assert self._poll(lambda: self._site_count() == total,
                          timeout_ms=30000), (
            f"the Site header counts {self._site_count()} under {listed[1]!r}"
        )
        expect(self.scope_filter).to_contain_text(listed[1])
        assert self._poll(
            lambda: re.search(rf"Showing \d+[–-]\d+ of {total}\b",
                              self._body()),
            timeout_ms=20000,
        ), "the pager footer does not follow the wider scope"
        log.info("All sites -> %s site(s), %s without coverage",
                 total, total - covered)

        log.info("Restoring the scope to %r", self.SCOPE_COVERED)
        self._park_mouse()
        self.scope_filter.click()
        self.page.get_by_role("option", name=self.SCOPE_COVERED).click()
        assert self._poll(lambda: self._site_count() == covered,
                          timeout_ms=30000), (
            f"restoring the scope left {self._site_count()} site(s), not "
            f"{covered}"
        )
        assert self._poll(lambda: self._names() == before, timeout_ms=25000), (
            "restoring the scope did not bring the original rows back"
        )

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
        self.check_custom_range()

    def check_custom_range(self):
        """"Custom" opens a two-date calendar that cannot run into the future.

        Opened and dismissed without choosing dates: the point is that the
        calendar is there and bounded, and that walking away from it leaves
        the applied range alone.
        """
        url = self.page.url
        log.info("Opening the Custom range calendar")
        self._park_mouse()
        self.date_filter.click()
        self.page.wait_for_timeout(1200)
        popover = self.page.get_by_role("dialog").last
        popover.get_by_role("button", name=re.compile(r"^Custom\b")).click()
        calendar = popover.get_by_role("grid")
        assert self._poll(lambda: calendar.count() > 0, timeout_ms=10000), (
            "choosing Custom opened no calendar"
        )
        # The field labels wrap onto two lines, so whitespace is normalised.
        text = " ".join((popover.inner_text() or "").split())
        for field in ("Start Date", "End Date"):
            assert field in text, f"the custom range has no {field!r} field"
        assert "Date Range: Select dates" in text, (
            f"the calendar does not prompt for a range: {text[-80:]!r}"
        )
        for control in ("Clear", "Done"):
            expect(popover.get_by_role("button", name=control,
                                       exact=True)).to_be_visible()
        # Two months side by side, ending on the current one. The data never
        # runs past today, so neither may the calendar.
        assert calendar.count() == 2, (
            f"the custom range shows {calendar.count()} month(s), not two"
        )
        for button in popover.get_by_role("button", name="Next month").all():
            expect(button).to_be_disabled()
        expect(popover.get_by_role(
            "button", name="Previous month"
        ).first).to_be_enabled()
        log.info("Custom calendar open on %s",
                 " and ".join(c.get_attribute("aria-label")
                              for c in calendar.all()))

        self.page.keyboard.press("Escape")
        self._blur()
        assert self._poll(
            lambda: self.page.get_by_role("dialog").count() == 0,
            timeout_ms=8000,
        ), "the range picker did not close"
        assert (self.date_filter.inner_text() or "").strip() == DEFAULT_RANGE, (
            f"dismissing the calendar changed the range to "
            f"{self.date_filter.inner_text()!r}"
        )
        assert self.page.url == url, (
            f"dismissing the calendar changed the URL to {self.page.url}"
        )

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
        # The trigger relabels before the refetch lands, and the old page is
        # already stable -- so wait for the row count the new size implies.
        total = self._site_count()
        expected = min(int(target), total)
        assert self._poll(
            lambda: self._loaded() and self.rows.count() == expected,
            timeout_ms=30000,
        ), (
            f"the table never reloaded at page size {target}: "
            f"{self.rows.count()} row(s), expected {expected}"
        )
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
        assert self._poll(
            lambda: self._loaded()
                    and self.rows.count() == min(int(current), total),
            timeout_ms=30000,
        ), f"the table never reloaded at page size {current}"

    # ----------------------------------------------------------------- #
    # Alerts
    # ----------------------------------------------------------------- #
    def _alerts(self):
        """Every episode on the current alert page, parsed, in one read.

        Read with a single `evaluate_all` so a refetch landing mid-read cannot
        pair one episode's status with another's dates. Each episode renders
        seven lines: detector, subject, severity, status, opened, resolved and
        duration. Returns [] while the list is repainting.
        """
        try:
            texts = self.alert_rows.evaluate_all(
                "els => els.map(e => e.innerText)"
            )
        except Exception:
            return []
        alerts = []
        for text in texts:
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if len(lines) != 7:
                return []
            keys = ("detector", "subject", "severity", "status", "opened",
                    "resolved", "duration")
            alerts.append(dict(zip(keys, lines)))
        return alerts

    @staticmethod
    def _stamp(text, prefix):
        """Parse "Opened 28 Sep 2026, 09:30" (or "Resolved ...") to a datetime."""
        return datetime.strptime(text[len(prefix):].strip(), "%d %b %Y, %H:%M")

    @staticmethod
    def _minutes(duration):
        """Parse a duration such as "1d 4h 19m" into minutes."""
        match = re.fullmatch(r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?",
                             duration)
        assert match and duration, f"unreadable duration {duration!r}"
        d, h, m = (int(g or 0) for g in match.groups())
        return d * 1440 + h * 60 + m

    def _alert_filter(self, label):
        return self.page.get_by_role(
            "button", name=re.compile(rf"^{re.escape(label)}\b")
        ).last

    def _pick_alert_option(self, label, option, query=None):
        """Choose one option in an alert filter. It applies on the click."""
        self._park_mouse()
        self._alert_filter(label).click()
        assert self._poll(
            lambda: self.page.get_by_role("option").count() > 0,
            timeout_ms=10000,
        ), f"the {label!r} filter opened with no options"
        if query is not None:
            self.page.get_by_role("dialog").last.get_by_role(
                "textbox"
            ).fill(query)
            self.page.wait_for_timeout(600)
        choice = self.page.get_by_role("option", name=option, exact=True)
        deselecting = choice.get_attribute("aria-selected") == "true"
        choice.click()
        if deselecting:
            # Choosing the active option again clears the filter, but leaves
            # the popover open.
            expect(choice).not_to_have_attribute("aria-selected", "true")
            self.page.keyboard.press("Escape")
            self._blur()
        # Single-select: a new choice closes the popover and applies at once.
        assert self._poll(
            lambda: self.page.get_by_role("dialog").count() == 0,
            timeout_ms=8000,
        ), f"the {label!r} filter stayed open after choosing {option!r}"

    def _settled_alerts(self, timeout_ms=15000):
        """The alert page, once two consecutive reads agree."""
        previous = None
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            current = self._alerts()
            if current and current == previous:
                return current
            previous = current
            self.page.wait_for_timeout(400)
        return self._alerts()

    def check_alert_filters(self):
        """The alert history offers its three filters, each with its options."""
        section = self._section("ALERTS", length=600)
        assert "Alert history" in section, "the alert panel has no title"
        assert "fleet-wide" in section, (
            "the alert history does not state its scope"
        )

        for label, options in self.ALERT_FILTERS.items():
            trigger = self._alert_filter(label)
            expect(trigger).to_contain_text(self.ALERT_DEFAULTS[label])
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
            self._blur()
            self.page.wait_for_timeout(700)

    def check_alert_rows(self):
        """Each episode is well formed, internally consistent and newest first.

        Status and dates have to agree: an open episode is unresolved and
        ongoing, a resolved one ends after it began and states a duration that
        matches the gap between the two.
        """
        assert self._poll(lambda: self._alerts(), timeout_ms=30000), (
            "the alert history never listed an episode"
        )
        alerts = self._settled_alerts()
        severities = self.ALERT_FILTERS["Severity"]
        statuses = self.ALERT_FILTERS["Status"]
        for alert in alerts:
            where = f"{alert['detector']} on {alert['subject']}"
            assert alert["detector"] in self.ALERT_FILTERS["Detector"], (
                f"unknown detector {alert['detector']!r}"
            )
            assert alert["severity"] in severities, (
                f"{where} carries an unknown severity {alert['severity']!r}"
            )
            assert alert["status"] in statuses, (
                f"{where} carries an unknown status {alert['status']!r}"
            )
            opened = self._stamp(alert["opened"], "Opened")
            if alert["status"] == "Open":
                assert (alert["resolved"], alert["duration"]) == (
                    "Not resolved", "Ongoing"
                ), f"{where} is open but reads {alert}"
            elif alert["status"] == "Resolved":
                resolved = self._stamp(alert["resolved"], "Resolved")
                assert resolved >= opened, (
                    f"{where} was resolved before it opened: {alert}"
                )
                gap = int((resolved - opened).total_seconds() // 60)
                assert self._minutes(alert["duration"]) == gap, (
                    f"{where} states a duration of {alert['duration']!r} but "
                    f"ran {gap} minute(s): {alert}"
                )
        opened = [self._stamp(a["opened"], "Opened") for a in alerts]
        assert opened == sorted(opened, reverse=True), (
            "the alert history is not newest first: "
            f"{[a['opened'] for a in alerts]}"
        )
        log.info("Alert history: %s episode(s) on page 1, newest %r",
                 len(alerts), alerts[0]["opened"])

    def expand_alert(self):
        """An episode expands into the detector's evidence, then collapses."""
        row = self.alert_rows.first
        name = " ".join((row.inner_text() or "").split()[:3])
        expect(row).to_have_attribute("aria-expanded", "false")
        before = self._section("ALERTS", length=6000)
        assert "By the detector" not in before, (
            "detector evidence is showing before any episode was expanded"
        )

        log.info("Expanding the alert %r", name)
        self._park_mouse()
        row.scroll_into_view_if_needed()
        row.click()
        expect(row).to_have_attribute("aria-expanded", "true")
        assert self._poll(
            lambda: "By the detector" in self._section("ALERTS", length=6000),
            timeout_ms=10000,
        ), f"expanding {name!r} showed no detector evidence"
        detail = self._section("ALERTS", length=6000)
        detail = detail[detail.find("By the detector"):][:800]
        assert re.search(r"\n[A-Z][\w ]+\n[^\n]+\n", detail), (
            f"the evidence lists no measurements: {detail!r}"
        )
        log.info("Evidence: %r", detail.replace("\n", " ")[:110])

        log.info("Collapsing it again")
        self._park_mouse()
        row.click()
        expect(row).to_have_attribute("aria-expanded", "false")
        assert self._poll(
            lambda: "By the detector" not in self._section("ALERTS",
                                                           length=6000),
            timeout_ms=10000,
        ), "the evidence stayed on screen after collapsing"

    def filter_alerts(self):
        """Severity narrows the history; a combination nothing matches shows
        the empty state; and its Clear filters restores everything."""
        baseline = self._settled_alerts()

        log.info("Filtering alerts to severity %r", ALERT_SEVERITY)
        self._pick_alert_option("Severity", ALERT_SEVERITY)
        expect(self._alert_filter("Severity")).to_contain_text(ALERT_SEVERITY)
        assert self._poll(
            lambda: self._alerts() and all(
                a["severity"] == ALERT_SEVERITY for a in self._alerts()
            ),
            timeout_ms=30000,
        ), (
            f"the {ALERT_SEVERITY!r} filter still lists "
            f"{sorted({a['severity'] for a in self._alerts()})}"
        )
        log.info("Severity %r -> %s episode(s) on page 1",
                 ALERT_SEVERITY, len(self._alerts()))

        log.info("Adding status %r on top", ALERT_EMPTY_STATUS)
        self._pick_alert_option("Status", ALERT_EMPTY_STATUS)
        expect(self._alert_filter("Status")).to_contain_text(ALERT_EMPTY_STATUS)
        assert self._poll(
            lambda: self.alert_empty.count() > 0 or (
                self._alerts() and all(
                    (a["severity"], a["status"])
                    == (ALERT_SEVERITY, ALERT_EMPTY_STATUS)
                    for a in self._alerts()
                )
            ),
            timeout_ms=30000,
        ), "the two alert filters did not combine"
        if self.alert_empty.count():
            section = self._section("ALERTS", length=800)
            assert "Try widening or clearing them" in section, (
                "the alert empty state gives no way out"
            )
            log.info("%s + %s matches nothing, and the panel says so",
                     ALERT_SEVERITY, ALERT_EMPTY_STATUS)
        else:
            log.info("%s + %s -> %s episode(s)", ALERT_SEVERITY,
                     ALERT_EMPTY_STATUS, len(self._alerts()))

        # The empty state carries the reset; with results, clear each filter
        # by choosing its option again, which toggles it off.
        if self.alert_empty.count():
            log.info("Clearing the alert filters from the empty state")
            self._park_mouse()
            self.page.get_by_role("button", name="Clear filters").last.click()
        else:
            self._pick_alert_option("Status", ALERT_EMPTY_STATUS)
            self._pick_alert_option("Severity", ALERT_SEVERITY)
        for label in ("Severity", "Status"):
            expect(self._alert_filter(label)).to_contain_text(
                self.ALERT_DEFAULTS[label]
            )
        assert self._poll(lambda: self._alerts() == baseline,
                          timeout_ms=30000), (
            "clearing the alert filters did not restore the full history"
        )

    def filter_alerts_by_detector(self):
        """The Detector filter searches its own options, applies one, and
        toggles off when that option is chosen again."""
        baseline = self._settled_alerts()

        log.info("Searching the Detector filter for %r", ALERT_DETECTOR_QUERY)
        self._park_mouse()
        self._alert_filter("Detector").click()
        self.page.get_by_role("dialog").last.get_by_role("textbox").fill(
            ALERT_DETECTOR_QUERY
        )
        assert self._poll(
            lambda: self.page.get_by_role("option").all_inner_texts()
                    == [ALERT_DETECTOR],
            timeout_ms=10000,
        ), (
            f"searching for {ALERT_DETECTOR_QUERY!r} left "
            f"{self.page.get_by_role('option').all_inner_texts()}"
        )
        self.page.get_by_role("option", name=ALERT_DETECTOR).click()
        expect(self._alert_filter("Detector")).to_contain_text(ALERT_DETECTOR)
        assert self._poll(
            lambda: self._alerts() and all(
                a["detector"] == ALERT_DETECTOR for a in self._alerts()
            ),
            timeout_ms=30000,
        ), (
            f"the {ALERT_DETECTOR!r} filter still lists "
            f"{sorted({a['detector'] for a in self._alerts()})}"
        )
        log.info("Detector %r -> %s episode(s) on page 1",
                 ALERT_DETECTOR, len(self._alerts()))

        log.info("Choosing %r again to clear it", ALERT_DETECTOR)
        self._pick_alert_option("Detector", ALERT_DETECTOR)
        expect(self._alert_filter("Detector")).to_contain_text(
            self.ALERT_DEFAULTS["Detector"]
        )
        assert self._poll(lambda: self._alerts() == baseline,
                          timeout_ms=30000), (
            "clearing the detector did not restore the full history"
        )

    def paginate_alerts(self):
        """The alert pager steps to older episodes and back."""
        first = self._settled_alerts()
        if not self.alert_next.is_enabled():
            log.info("Every alert fits on one page -- nothing to page through")
            return
        last_page = self.page.get_by_role(
            "button", name=re.compile(r"^Go to page \d+$")
        ).last.inner_text()
        log.info("Paging the alert history forward (%s page(s))", last_page)
        self._park_mouse()
        self.alert_next.scroll_into_view_if_needed()
        self.alert_next.click()
        assert self._poll(
            lambda: self._alerts() and self._alerts() != first,
            timeout_ms=25000,
        ), "page 2 of the alert history shows the same episodes as page 1"
        second = self._settled_alerts()
        # Newest first across the page boundary, not only within a page.
        assert self._stamp(second[0]["opened"], "Opened") <= self._stamp(
            first[-1]["opened"], "Opened"
        ), (
            f"page 2 opens with {second[0]['opened']!r}, newer than the last "
            f"episode on page 1 ({first[-1]['opened']!r})"
        )
        expect(self.alert_prev).to_be_enabled()
        self._park_mouse()
        self.alert_prev.click()
        assert self._poll(lambda: self._alerts() == first, timeout_ms=25000), (
            "going back did not restore page 1 of the alert history"
        )
        log.info("Alert pager: page 2 continues from %r", second[0]["opened"])

    def check_alert_resolution_times(self):
        """No episode may be marked Resolved at a time that has not happened.

        Known product bug: the Reboot frequency detector resolves each episode
        at the end of its 24-hour window rather than when the condition
        cleared, so an episode opened this morning already reads "Resolved"
        with tomorrow's timestamp (e.g. opened 28 Sep 05:30, "Resolved 29 Sep
        05:30", 1d) while the clock still reads 28 Sep. That is a claim about
        the future presented as history. The dates are compared in the
        browser's own timezone, which is the one the page renders in.
        """
        now = datetime.strptime(
            self.page.evaluate("""() => {
                const d = new Date(), p = n => String(n).padStart(2, '0');
                return `${d.getFullYear()}-${p(d.getMonth() + 1)}-` +
                       `${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
            }"""),
            "%Y-%m-%d %H:%M",
        )
        assert self._poll(lambda: self._alerts(), timeout_ms=30000), (
            "the alert history never listed an episode"
        )
        seen = list(self._settled_alerts())
        # The bug is concentrated in one detector; read its newest page too.
        self._pick_alert_option("Detector", ALERT_DETECTOR,
                                query=ALERT_DETECTOR_QUERY)
        assert self._poll(
            lambda: self._alerts() and all(
                a["detector"] == ALERT_DETECTOR for a in self._alerts()
            ),
            timeout_ms=30000,
        )
        seen += self._settled_alerts()
        self._pick_alert_option("Detector", ALERT_DETECTOR)
        # The newest episodes appear in both reads; count each once.
        seen = list({tuple(a.values()): a for a in seen}.values())

        future = [
            a for a in seen
            if a["resolved"].startswith("Resolved ")
            and self._stamp(a["resolved"], "Resolved") > now
        ]
        log.info("Checked %s episode(s) against %s", len(seen), now)
        assert not future, (
            f"{len(future)} episode(s) are marked Resolved at a time that has "
            f"not happened yet (now {now:%d %b %Y, %H:%M}): "
            + "; ".join(
                f"{a['detector']} on {a['subject']} {a['opened']} -> "
                f"{a['resolved']}" for a in future
            )
        )

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
            re.compile(r"/operations/network-intelligence/[0-9a-f-]{36}$"),
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
            "link", name="Back to Network Intelligence"
        )).to_be_visible()
        expect(self.breadcrumb_ni).to_be_visible()

        assert "Uptime trend" in body, (
            "the site page draws no uptime trend"
        )
        self.check_kpi_deltas()
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
            assert re.fullmatch(self.UPTIME_CELL, cells[1]), (
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
                r"/operations/network-intelligence/[0-9a-f-]{36}/[0-9a-f-]{36}$"
            ),
            timeout=30000,
        )
        self._wait_for_charger_page(model)

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
        band = re.search(r"Risk score\n.*?\n\d+\n(\w+)\nrank \d+ of \d+", body)
        assert band and band.group(1) in self.RISK_BANDS, (
            f"the risk score carries no known band: "
            f"{body[body.find('Risk score'):][:120]!r}"
        )
        for part in ("Persistence", "Responsiveness"):
            assert part in body, (
                f"the risk score is not broken into {part!r}"
            )
        assert "changing the length of the date range does not change this "\
               "ranking" in body.replace("\n", " "), (
            "the charger page does not explain that its ranking uses a fixed "
            "scoring window"
        )

        # The trend can be read per day or per hour; it opens on Daily.
        daily = self.page.get_by_role("tab", name="Daily")
        hourly = self.page.get_by_role("tab", name="Hourly")
        expect(daily).to_have_attribute("aria-selected", "true")
        log.info("Switching the uptime trend to Hourly and back")
        self._park_mouse()
        hourly.click()
        expect(hourly).to_have_attribute("aria-selected", "true")
        expect(daily).to_have_attribute("aria-selected", "false")
        self._park_mouse()
        daily.click()
        expect(daily).to_have_attribute("aria-selected", "true")

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
            re.compile(r"/operations/network-intelligence/[0-9a-f-]{36}$"),
            timeout=30000,
        )
        assert self._poll(self._loaded, timeout_ms=40000)

    def back_to_network_intelligence(self):
        """The site page's back link returns to the fleet view."""
        log.info("Returning to Network Intelligence")
        self._park_mouse()
        self.page.get_by_role("link", name="Back to Network Intelligence").click()
        self.page.wait_for_url(
            re.compile(r"/operations/network-intelligence(\?|$)"), timeout=30000
        )
        self.heading.wait_for(state="visible", timeout=20000)
        assert self._poll(self._loaded, timeout_ms=40000), (
            "the coverage table never reloaded on the way back"
        )
        self._assert_columns(self.SITE_COLUMNS)

    # ----------------------------------------------------------------- #
    # Full workflow
    # ----------------------------------------------------------------- #
    def network_intelligence_page(self):
        self.open_page()
        self.check_header()
        self.check_refresh()
        self.check_kpi_tiles()
        self.check_kpi_deltas()
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
        self.open_watchlist_charger()
        self.check_sites_table()
        self.check_coverage_scope()
        self.search_sites()
        self.filter_by_sub_org()
        self.check_date_range()
        self.expand_site_row()
        self.expand_all_site_rows()
        self.paginate_sites()
        self.check_alert_filters()
        self.check_alert_rows()
        self.expand_alert()
        self.filter_alerts()
        self.filter_alerts_by_detector()
        self.paginate_alerts()
        site = self.open_site_page()
        self.open_charger_page(site)
        self.back_to_network_intelligence()
        log.info("Network Intelligence workflow completed")

    # ----------------------------------------------------------------- #
    # Known product bugs -- each its own test, see the method docstring
    # ----------------------------------------------------------------- #
    def alert_resolution_times_page(self):
        self.open_page()
        self.check_alert_resolution_times()
