from collections.abc import Iterator
from typing import Any, cast

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion
from syrupy.filters import props

from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka
from tests.ha.conftest import SetupIntegration

VOLATILE = props("entry_id", "created_at", "modified_at")

ORDER_NUMBER = "900000000001"

PERSONAL_DATA = {
    EMAIL,
    PASSWORD,
    "Test Kunde",
    "Testvej 1",
    "Eksempelby",
    "9999",
    "12345678",
    "fake-uid-0001",
    "fake-tid-0001",
    "eyFake.jwt.token",
    "st2.fake-login-token",
}


def leaves(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from leaves(item)
    else:
        yield value


def pack_with_substitutions(bilka: FakeBilka) -> None:
    order = next(o for o in bilka.orders if o["orderId"] == ORDER_NUMBER)
    bilka.orders = [{**order, "status": "Pakket", "name": "Test Kunde"}]
    details = bilka.details[ORDER_NUMBER]
    bilka.details[ORDER_NUMBER] = {"order": {**details["order"], "status": "Pakket"}}


# The Pickup Slots kept are those of the coming days, so the day is fixed. The marker, unlike
# `freezer.move_to`, freezes before `hass_client` issues its access token.
@pytest.mark.freeze_time("2026-11-08T20:00:00+01:00")
async def test_diagnostics_keep_products_and_store_but_redact_personal_data(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    snapshot: SnapshotAssertion,
) -> None:
    pack_with_substitutions(bilka)
    entry = await setup_integration()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    values = set(leaves(diagnostics))
    assert values & PERSONAL_DATA == set()
    assert {"Æbler", "Pærer", "Letmælk", "Minimælk", "Bilka Eksempelby"} <= values
    assert not [value for value in values if ORDER_NUMBER in str(value)]
    data = cast("dict[str, Any]", diagnostics["data"])
    assert data["order"]["order_number"] == "order_1"
    assert data["details"]["order_number"] == "order_1"
    assert data["details"]["_raw"]["orderId"] == "order_1"
    assert {s["order_number"] for s in data["announced"]["substitution"]} == {"order_1"}
    assert diagnostics == snapshot(exclude=VOLATILE)


@pytest.mark.freeze_time("2026-11-02T08:00:00+01:00")
async def test_diagnostics_hold_no_home_delivery_address(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_integration: SetupIntegration,
    home_delivery_cart: FakeBilka,
) -> None:
    entry = await setup_integration()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    data = cast("dict[str, Any]", diagnostics["data"])
    assert data["pickup_slots"]["home_delivery"] is True
    assert data["pickup_slots"]["pickup_point"] is None
    assert not [value for value in leaves(diagnostics) if "Kunde Eksempel" in str(value)]
    assert not set(leaves(data["pickup_slots"])) & PERSONAL_DATA
