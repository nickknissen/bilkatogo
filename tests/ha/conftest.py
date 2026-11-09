"""Fixtures for the BilkaToGo Home Assistant tests."""

from collections.abc import Awaitable, Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka

type SetupIntegration = Callable[[], Awaitable[MockConfigEntry]]

# When the Pickup Slot fixtures were captured: their slots are open after it.
SNAPSHOT_TIME = "2026-11-08T20:00:00+01:00"


def pack_substituted_order(bilka: FakeBilka) -> None:
    """Make the Order with two Substitutions the only one, and Packed."""
    order_number = "900000000001"
    order = next(o for o in bilka.orders if o["orderId"] == order_number)
    bilka.orders = [{**order, "status": "Pakket"}]
    details = bilka.details[order_number]
    bilka.details[order_number] = {**details, "order": {**details["order"], "status": "Pakket"}}


def event_entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    """Return the entity ID of the event entity with the given key."""
    entity_id = er.async_get(hass).async_get_entity_id("event", DOMAIN, f"{entry.unique_id}_{key}")
    assert entity_id is not None
    return entity_id


def fired(state_changes: list[Event[Any]], entity_id: str) -> list[dict[str, Any]]:
    """The events an event entity fired, in order: their event type and data."""
    return [
        {
            key: value
            for key, value in change.data["new_state"].attributes.items()
            if key not in ("event_types", "friendly_name")
        }
        for change in state_changes
        if change.data["entity_id"] == entity_id
        and change.data["new_state"].state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    ]


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return the snapshot assertion with Home Assistant's snapshot extension."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations; `pytest-homeassistant` does so itself.

    Only ha-test-oldest's `pytest-homeassistant-custom-component` needs this (see
    tests/ha/__init__.py).
    """
    return None


@pytest.fixture(autouse=True)
def bilka_backend(bilka: FakeBilka) -> Generator[FakeBilka]:
    """Route every HTTP request Home Assistant's httpx clients send to the fake Bilka backend.

    The integration builds its own httpx client as in production; only the transport
    boundary is replaced, so the real bilkatogo library code runs in these tests.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        return bilka.transport.handle_request(request)

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=handle)
        yield bilka


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip setting up the entry a config flow creates."""
    with patch(
        "custom_components.bilkatogo.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a BilkaToGo config entry for the fake account, added to Home Assistant."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        minor_version=2,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> SetupIntegration:
    """Return a function that sets up the config entry and returns it."""

    async def setup() -> MockConfigEntry:
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        return config_entry

    return setup
