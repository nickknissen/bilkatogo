from datetime import timedelta

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from bilkatogo import REQUEST_TIMEOUT_SECONDS
from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import LOGGED_OUT, FakeBilka, Scripted
from tests.ha.conftest import SetupIntegration

FAILURES: dict[str, Scripted] = {
    "unreachable": httpx.ConnectError("down"),
    "timeout": httpx.ReadTimeout("slow"),
    "server_error": httpx.Response(500, text="oops"),
    "not_json": httpx.Response(200, text="<html>maintenance</html>"),
}


async def next_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def order_status(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_order_status"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def test_requests_wait_for_a_slow_bilka(
    setup_integration: SetupIntegration, bilka: FakeBilka
) -> None:
    await setup_integration()

    assert bilka.requests
    for request in bilka.requests:
        assert request.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS


@pytest.mark.parametrize("failure", FAILURES.values(), ids=FAILURES.keys())
async def test_failed_update_makes_entities_unavailable_without_reauth(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    failure: Scripted,
) -> None:
    entry = await setup_integration()
    bilka.script("order_history", failure)

    await next_update(hass, freezer)

    assert order_status(hass, entry) == STATE_UNAVAILABLE
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_busy_login_service_makes_entities_unavailable_without_reauth(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await setup_integration()
    bilka.script("order_history", LOGGED_OUT)
    bilka.script(
        "gigya_login",
        httpx.Response(403, json={"errorCode": 403048, "errorMessage": "Too many requests"}),
    )

    await next_update(hass, freezer)

    assert len(bilka.calls("gigya_login")) == 2
    assert order_status(hass, entry) == STATE_UNAVAILABLE
    assert entry.state is ConfigEntryState.LOADED
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_entities_recover_on_the_next_successful_update(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await setup_integration()
    bilka.script("order_history", httpx.ConnectError("down"))
    await next_update(hass, freezer)
    assert order_status(hass, entry) == STATE_UNAVAILABLE

    await next_update(hass, freezer)

    assert order_status(hass, entry) == "received"
