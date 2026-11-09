"""Tests for the BilkaToGo binary sensor platform."""

from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import snapshot_platform
from syrupy.assertion import SnapshotAssertion

from tests.fake_bilka import FakeBilka
from tests.ha.conftest import SNAPSHOT_TIME, SetupIntegration, pack_substituted_order


async def test_all_entities(
    hass: HomeAssistant,
    setup_integration: SetupIntegration,
    bilka: FakeBilka,
    freezer: FrozenDateTimeFactory,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the binary sensor of a Packed Order."""
    freezer.move_to(SNAPSHOT_TIME)
    pack_substituted_order(bilka)
    with patch("custom_components.bilkatogo.PLATFORMS", [Platform.BINARY_SENSOR]):
        entry = await setup_integration()

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)
