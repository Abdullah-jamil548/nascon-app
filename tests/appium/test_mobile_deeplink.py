"""Appium deep-link checks. Skips unless Appium is listening on port 4723."""

import importlib.util
import os
import urllib.request

import pytest

try:
    _appium = importlib.util.find_spec("appium.webdriver")
except (ImportError, ValueError):
    _appium = None
if _appium is None:
    pytest.skip("appium is not installed", allow_module_level=True)

from appium import webdriver
from appium.options.android import UiAutomator2Options


def appium_ready():
    try:
        with urllib.request.urlopen("http://localhost:4723/status", timeout=2) as response:
            return b"ready" in response.read()
    except Exception:
        return False


@pytest.fixture
def phone():
    if not appium_ready():
        pytest.skip("appium is not running on port 4723")
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.app_package = "com.nascon.nascon_mobile"
    options.app_activity = "com.nascon.nascon_mobile.MainActivity"
    options.no_reset = True
    driver = webdriver.Remote(os.environ.get("APPIUM_URL", "http://localhost:4723"), options=options)
    yield driver
    driver.quit()


def test_match_and_costream_links_leave_login(phone):
    for link in ("nascon://entity/match-m1", "nascon://stream/cs-session-101"):
        phone.execute_script("mobile: deepLink", {"url": link, "package": "com.nascon.nascon_mobile"})
        page = phone.page_source
        assert "Sign in" not in page or "Live match" in page or "Co-Stream" in page
