"""Tests for the BilkaToGo config flow."""

from unittest.mock import AsyncMock

import httpx
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from bilkatogo import REQUEST_TIMEOUT_SECONDS
from custom_components.bilkatogo.const import DOMAIN
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka


async def start_flow(
    hass: HomeAssistant, email: str = EMAIL, password: str = PASSWORD
) -> ConfigFlowResult:
    """Open the user step and submit the email and password."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: email, CONF_PASSWORD: password}
    )


async def test_user_adds_account_with_email_and_password(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test the account is added under its email, which is also its unique ID."""
    result = await start_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    assert result["result"].unique_id == EMAIL
    assert len(mock_setup_entry.mock_calls) == 1


async def test_the_login_waits_for_a_slow_bilka(
    hass: HomeAssistant, bilka: FakeBilka, mock_setup_entry: AsyncMock
) -> None:
    """Test the flow's login uses the library's request timeout."""
    await start_flow(hass)

    [login] = bilka.calls("gigya_login")
    assert login.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("password", "route", "answer", "error"),
    [
        ("forkert", None, None, "invalid_auth"),
        (PASSWORD, "gigya_login", httpx.ConnectError("down"), "cannot_connect"),
        (
            PASSWORD,
            "gigya_login",
            httpx.Response(403, json={"errorCode": 403048, "errorMessage": "Too many requests"}),
            "cannot_connect",
        ),
        (
            PASSWORD,
            "gigya_login",
            httpx.Response(500, json={"errorCode": 500001, "errorMessage": "General Server Error"}),
            "cannot_connect",
        ),
        (PASSWORD, "gigya_jwt", httpx.Response(500, text="oops"), "unknown"),
    ],
    ids=["wrong_password", "unreachable", "rate_limit", "server_error", "unexpected_answer"],
)
async def test_user_recovers_from_a_failed_login(
    hass: HomeAssistant,
    bilka: FakeBilka,
    mock_setup_entry: AsyncMock,
    password: str,
    route: str | None,
    answer: httpx.Response | Exception | None,
    error: str,
) -> None:
    """Test a failed login shows its error, and the account is added on the next try."""
    if route is not None and answer is not None:
        bilka.script(route, answer)

    result = await start_flow(hass, password=password)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    assert len(mock_setup_entry.mock_calls) == 1


async def test_same_account_cannot_be_added_twice(
    hass: HomeAssistant, config_entry: MockConfigEntry, bilka: FakeBilka
) -> None:
    """Test the flow aborts for an account already added, matching its email loosely."""
    result = await start_flow(hass, email=f"  {EMAIL.upper()} ")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert bilka.calls("gigya_login") == []


async def test_second_account_can_be_added(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test another account can be added next to an existing one."""
    other = "anden@example.com"
    MockConfigEntry(domain=DOMAIN, title=other, unique_id=other).add_to_hass(hass)

    result = await start_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert {entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)} == {
        other,
        EMAIL,
    }
