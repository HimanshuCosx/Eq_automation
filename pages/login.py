import logging

log = logging.getLogger("eq_automation.login")


class login:
    def __init__(self, page):
        self.page = page
        self.email = page.get_by_placeholder("Enter your email address")
        self.password = page.get_by_placeholder("Enter your password")
        self.login_btn = page.locator("(//button[normalize-space()='Log In'])[1]")

    def login_page(self):
        # Wait for the form and give React a moment to hydrate; filling a
        # controlled input before its onChange is attached silently drops the
        # value and the login fails (especially in headless/parallel runs).
        log.info("Waiting for the login form to be visible")
        self.email.wait_for(state="visible")
        self.page.wait_for_timeout(1000)

        log.info("Entering credentials and submitting the login form")
        self.email.fill("himanshu@equidria.com")
        self.password.fill("Himanshu@14")
        self.login_btn.click()

        # Confirm we actually left the login screen before continuing.
        try:
            self.page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
            log.info("Left the login screen, now at: %s", self.page.url)
        except Exception:
            log.warning("Did not leave /login within 20s; current URL: %s", self.page.url)
        self.page.wait_for_timeout(1000)
