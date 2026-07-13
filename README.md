# Equidria OS – Playwright + Pytest Automation

End-to-end UI automation for Equidria OS (staging), built with
[Playwright](https://playwright.dev/python/) and `pytest`, following the
Page Object Model.

```
Eq_automation/
├── config.py          # BASE_URL and shared config
├── conftest.py        # shared logged-in `page` fixture + reporting hooks
├── pytest.ini         # markers + default report options
├── pages/             # Page Objects (login, reconciliation, self_billing)
└── tests/             # Test cases (one per feature)
```

## Setup

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Running tests

> Tests share **one** logged-in browser session, so they run **sequentially**.
> Do **not** pass `-n` (pytest-xdist / parallel) — each worker gets its own
> browser and only one would be logged in.

```bash
pytest                                   # run everything
pytest -m smoke                          # only smoke tests
pytest tests/test_reconciliation.py      # a single feature
```

Login happens automatically in the shared `page` fixture, so any single test
file can be run on its own and still starts authenticated.

## Reports (for review / sign-off)

Every run generates two reports automatically.

### 1. HTML report (single file – easiest to share)

Open or email this file after a run:

```
reports/report.html
```

It is self-contained (styling + failure screenshots embedded), so it opens in
any browser with no server needed.

### 2. Allure report (rich dashboard)

Raw results are written to `allure-results/` on every run. To view the
dashboard you need the Allure CLI once:

```bash
# macOS
brew install allure

# then, after a test run:
allure serve allure-results          # opens an interactive dashboard
# or generate a static site:
allure generate allure-results -o allure-report --clean
allure open allure-report
```

The Allure report groups tests by **Feature / Story**, shows **severity**, step
timelines, and screenshots captured on failure.

## Screenshots on failure

If a test fails, a full-page screenshot is captured automatically and embedded
in **both** the HTML report and the Allure report, so failures are easy to
triage without re-running.
