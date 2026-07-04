"""Human-like interaction helpers for Playwright (self-contained, no config file).

Randomized typing, clicking, and delay helpers to reduce bot-like timing
signatures. Uses module constants instead of an external config file.
"""

from __future__ import annotations

import random
import time

from playwright.sync_api import Page

# Timing constants (seconds / milliseconds). Keep these non-trivial — tight,
# uniform timings are an easy bot signal.
MIN_ACTION_DELAY = 0.8
MAX_ACTION_DELAY = 2.2
TYPING_DELAY_MIN = 30   # ms per keystroke
TYPING_DELAY_MAX = 110


def random_sleep(min_s: float | None = None, max_s: float | None = None) -> float:
    """Sleep a random duration, biased slightly toward the shorter end."""
    min_s = MIN_ACTION_DELAY if min_s is None else min_s
    max_s = MAX_ACTION_DELAY if max_s is None else max_s
    bias = random.random() ** 1.4  # skew toward shorter waits
    wait = min_s + bias * (max_s - min_s)
    time.sleep(wait)
    return wait


def human_type(page: Page, selector: str, text: str) -> None:
    """Type text with per-keystroke delay; fill directly for long text."""
    if not text:
        return
    if len(text) > 200:
        random_sleep(0.2, 0.8)
        page.fill(selector, text)
        random_sleep()
        return
    delay = random.randint(TYPING_DELAY_MIN, TYPING_DELAY_MAX)
    page.click(selector)
    random_sleep(0.05, 0.2)
    page.type(selector, text, delay=delay)
    random_sleep()


def human_click(page: Page, selector: str, timeout: int = 30000) -> bool:
    """Wait + click with human-like pauses. Returns False if it failed."""
    random_sleep()
    try:
        locator = page.locator(selector)
        locator.wait_for(state="visible", timeout=timeout)
        locator.click()
    except Exception:
        try:
            page.locator(selector).click(force=True)
        except Exception:
            return False
    random_sleep()
    return True


def wait_for_page_full_load(page: Page, selector: str | None = None, timeout: int = 45000) -> None:
    """Wait for DOM + load events, plus a randomized buffer for async UI."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
        page.wait_for_load_state("load", timeout=timeout)
    except Exception:
        pass
    if selector:
        try:
            page.wait_for_selector(selector, timeout=timeout)
        except Exception:
            pass
    time.sleep(random.uniform(2.0, 4.0))
