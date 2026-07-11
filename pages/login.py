class login:
    def __init__(self,page):
        self.page=page
        self.email = page.get_by_placeholder("Enter your email address")
        self.password = page.get_by_placeholder("Enter your password")
        self.login_btn = page.locator("(//button[normalize-space()='Log In'])[1]")

    def login_page(self):
        self.email.fill("himanshu@equidria.com")
        self.password.fill("Himanshu@14")
        self.login_btn.click()
        self.page.wait_for_timeout(2000)