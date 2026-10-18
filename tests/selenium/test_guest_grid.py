"""Selenium Grid checks for the guest player. Skips when the hub on port 4444 is down."""

import importlib.util
import os
import urllib.request

import pytest

try:
    _selenium = importlib.util.find_spec("selenium.webdriver")
except (ImportError, ValueError):
    _selenium = None
if _selenium is None:
    pytest.skip("selenium is not installed", allow_module_level=True)

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE = os.environ.get("GUEST_BASE", "http://host.docker.internal:8080")
HUB = os.environ.get("SELENIUM_HUB", "http://localhost:4444/wd/hub")


def hub_ready():
    try:
        urllib.request.urlopen("http://localhost:4444/status", timeout=2)
        return True
    except Exception:
        return False


@pytest.fixture
def browser():
    if not hub_ready():
        pytest.skip("selenium hub is not running on port 4444")
    options = webdriver.ChromeOptions()
    driver = webdriver.Remote(command_executor=HUB, options=options)
    driver.set_page_load_timeout(10)
    yield driver
    driver.quit()


def test_qr_opens_live_without_a_login_wall(browser):
    browser.get(BASE + "/q/m1-live")
    WebDriverWait(browser, 3).until(lambda d: "/web/matches/match-m1/live" in d.current_url)
    banner = browser.find_element(By.CSS_SELECTOR, '[data-testid="live-score-banner"]')
    player = browser.find_element(By.CSS_SELECTOR, '[data-testid="guest-video-player"]')
    assert player.is_displayed()
    assert banner.text.strip()
    modal = browser.find_element(By.CSS_SELECTOR, '[data-testid="progressive-auth-modal"]')
    assert not modal.is_displayed()


def test_guest_chat_opens_the_auth_modal(browser):
    browser.get(BASE + "/web/matches/match-m1/live")
    browser.find_element(By.CSS_SELECTOR, '[data-testid="chat-input-field"]').send_keys("hello from the stands")
    browser.find_element(By.CSS_SELECTOR, '[data-testid="chat-send-btn"]').click()
    modal = browser.find_element(By.CSS_SELECTOR, '[data-testid="progressive-auth-modal"]')
    WebDriverWait(browser, 3).until(lambda d: modal.is_displayed())
    assert browser.find_element(By.CSS_SELECTOR, '[data-testid="chat-message-list"]').text == ""


def test_guest_cart_and_score_banner(browser):
    import json

    browser.get(BASE + "/web/matches/match-m1/live")
    browser.find_element(By.CSS_SELECTOR, '[data-testid="add-to-guest-cart-btn"]').click()
    WebDriverWait(browser, 3).until(lambda d: d.find_element(By.CSS_SELECTOR, '[data-testid="guest-cart-count"]').text == "1")
    assert "var-jersey-001-L" in browser.find_element(By.CSS_SELECTOR, '[data-testid="pinned-merch-card"]').text
    payload = json.dumps(
        {"event_id": "m1-goal-grid", "match_id": "match-m1", "type": "score", "payload": {"team_id": "club-alpha-1", "event_type": "goal"}}
    ).encode()
    request = urllib.request.Request(
        os.environ.get("API_BASE", "http://localhost:8080") + "/audit/v1/events",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=5)
    WebDriverWait(browser, 3).until(lambda d: "2 - 0" in d.find_element(By.CSS_SELECTOR, '[data-testid="live-score-banner"]').text)
