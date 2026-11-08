import httpx
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from bilkatogo import REQUEST_TIMEOUT_SECONDS
from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka


async def start_flow(
    hass: HomeAssistant, email: str = EMAIL, password: str = PASSWORD
) -> ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: email, CONF_PASSWORD: password}
    )


async def test_user_adds_account_with_email_and_password(hass: HomeAssistant) -> None:
    result = await start_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    assert result["result"].unique_id == EMAIL


async def test_the_login_waits_for_a_slow_bilka(hass: HomeAssistant, bilka: FakeBilka) -> None:
    await start_flow(hass)

    # The first login is the flow's; setting up the new entry logs in again.
    login = bilka.calls("gigya_login")[0]
    assert login.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS


async def test_wrong_password_shows_invalid_auth(hass: HomeAssistant) -> None:
    result = await start_flow(hass, password="forkert")

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_bilka_unreachable_shows_cannot_connect(
    hass: HomeAssistant, bilka: FakeBilka
) -> None:
    bilka.script("gigya_login", httpx.ConnectError("down"))

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(403, json={"errorCode": 403048, "errorMessage": "Too many requests"}),
        httpx.Response(500, json={"errorCode": 500001, "errorMessage": "General Server Error"}),
    ],
    ids=["rate_limit", "server_error"],
)
async def test_busy_login_service_shows_cannot_connect(
    hass: HomeAssistant, bilka: FakeBilka, answer: httpx.Response
) -> None:
    bilka.script("gigya_login", answer)

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unexpected_answer_shows_unknown(hass: HomeAssistant, bilka: FakeBilka) -> None:
    bilka.script("gigya_jwt", httpx.Response(500, text="oops"))

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_can_retry_after_an_error(hass: HomeAssistant) -> None:
    result = await start_flow(hass, password="forkert")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_same_account_cannot_be_added_twice(
    hass: HomeAssistant, config_entry: MockConfigEntry, bilka: FakeBilka
) -> None:
    result = await start_flow(hass, email=f"  {EMAIL.upper()} ")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert bilka.calls("gigya_login") == []


async def test_second_account_can_be_added(hass: HomeAssistant) -> None:
    other = "anden@example.com"
    MockConfigEntry(domain=DOMAIN, title=other, unique_id=other).add_to_hass(hass)

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert {entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)} == {
        other,
        EMAIL,
    }
