from importlib.util import find_spec
from typing import Any

import pytest

from bilkatogo import TokenStorage
from tests.fake_bilka import FakeBilka, load_fixture

collect_ignore = [] if find_spec("homeassistant") else ["ha"]


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if collect_ignore:
        terminalreporter.write_line(
            "tests/ha not run: homeassistant is not installed. Run scripts/ha-test.sh.",
            yellow=True,
        )


class MemoryTokenStorage(TokenStorage):
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def load(self) -> dict[str, Any] | None:
        return self.data

    async def save(self, data: dict[str, Any]) -> None:
        self.data = data


@pytest.fixture
def bilka() -> FakeBilka:
    return FakeBilka()


@pytest.fixture
def logged_in_cart(bilka: FakeBilka) -> FakeBilka:
    bilka.cart_contents = load_fixture("cart_logged_in.json")
    return bilka


@pytest.fixture
def home_delivery_cart(bilka: FakeBilka) -> FakeBilka:
    bilka.cart_contents = load_fixture("cart_home.json")
    return bilka


@pytest.fixture
def storage() -> MemoryTokenStorage:
    return MemoryTokenStorage()
