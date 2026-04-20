import os

import pytest


DEFAULT_E2E_PORT = os.getenv("PLAYWRIGHT_E2E_PORT", "8011")
DEFAULT_BASE_URL = f"http://127.0.0.1:{DEFAULT_E2E_PORT}"


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("PLAYWRIGHT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    return {
        **browser_context_args,
        "ignore_https_errors": True,
        "viewport": {"width": 1440, "height": 900},
    }


@pytest.fixture(autouse=True)
def configure_page(page):
    page.set_default_timeout(10_000)
    page.set_default_navigation_timeout(15_000)
