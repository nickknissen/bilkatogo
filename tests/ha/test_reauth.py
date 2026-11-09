"""Tests for reauthenticating BilkaToGo."""

from datetime import timedelta

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import EMAIL, FakeBilka
from tests.ha.conftest import SetupIntegration

NEW_PASSWORD = "nyt-kodeord"


async def next_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move time on to the next scheduled update and wait for it to finish."""
    freezer.tick(timedelta(minutes=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def reauth_flows(hass: HomeAssistant, entry: MockConfigEntry) -> list:
    """Return the reauth flows in progress for the config entry."""
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("source") == SOURCE_REAUTH
        and flow["context"].get("entry_id") == entry.entry_id
    ]


def order_status(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Return the state of the Order Status sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}_order_status"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def reauth_with(
    hass: HomeAssistant, entry: MockConfigEntry, password: str
) -> ConfigFlowResult:
    """Answer the entry's reauth flow with the password and return the result."""
    [flow] = reauth_flows(hass, entry)
    return await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: password}
    )


async def change_password_at_bilka(bilka: FakeBilka) -> None:
    """Change the password at Bilka and log the current session out."""
    bilka.password = NEW_PASSWORD
    bilka.script("order_history", httpx.Response(401, json={"code": 401, "msg": "Unauthorized"}))


async def test_an_expired_session_renews_from_the_login_token_without_logging_in(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test an expired session is renewed with the login token, not the password."""
    entry = await setup_integration()

    freezer.tick(timedelta(hours=25))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert order_status(hass, entry) == "received"
    assert len(bilka.calls("gigya_login")) == 1
    assert len(bilka.calls("gigya_jwt")) == 2


async def test_a_refused_login_token_falls_back_to_the_stored_password_without_reauth(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a refused login token logs in again with the stored password, without reauth."""
    entry = await setup_integration()
    bilka.script(
        "gigya_jwt",
        httpx.Response(403, json={"errorCode": 403005, "errorMessage": "Unauthorized user"}),
    )

    freezer.tick(timedelta(hours=25))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert order_status(hass, entry) == "received"
    assert len(bilka.calls("gigya_login")) == 2
    assert reauth_flows(hass, entry) == []


async def test_changed_password_starts_reauth(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a changed password starts a reauth flow."""
    entry = await setup_integration()
    await change_password_at_bilka(bilka)

    await next_update(hass, freezer)

    assert len(reauth_flows(hass, entry)) == 1


async def test_reauth_with_the_new_password_resumes_tracking(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test reauth with the new password stores it and brings the entities back."""
    entry = await setup_integration()
    await change_password_at_bilka(bilka)
    await next_update(hass, freezer)
    assert order_status(hass, entry) == STATE_UNAVAILABLE

    result = await reauth_with(hass, entry, NEW_PASSWORD)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_EMAIL: EMAIL, CONF_PASSWORD: NEW_PASSWORD}
    assert entry.state is ConfigEntryState.LOADED
    assert order_status(hass, entry) == "received"
    assert reauth_flows(hass, entry) == []


async def test_reauth_recovers_from_a_wrong_password(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a wrong password in reauth shows an error and the right one then succeeds."""
    entry = await setup_integration()
    await change_password_at_bilka(bilka)
    await next_update(hass, freezer)

    result = await reauth_with(hass, entry, "stadig-forkert")

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] != NEW_PASSWORD
    assert order_status(hass, entry) == STATE_UNAVAILABLE

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: NEW_PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == NEW_PASSWORD


@pytest.mark.parametrize("language", ["en", "da"])
async def test_reauth_texts_are_translated(hass: HomeAssistant, language: str) -> None:
    """Test the reauth form, abort reason and auth error have texts in each language."""
    config = await async_get_translations(hass, language, "config", [DOMAIN])
    exceptions = await async_get_translations(hass, language, "exceptions", [DOMAIN])
    prefix = f"component.{DOMAIN}"

    assert "{email}" in config[f"{prefix}.config.step.reauth_confirm.description"]
    assert config[f"{prefix}.config.step.reauth_confirm.data.password"]
    assert config[f"{prefix}.config.abort.reauth_successful"]
    assert "{error}" in exceptions[f"{prefix}.exceptions.auth_failed.message"]
