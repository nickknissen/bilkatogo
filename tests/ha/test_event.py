"""Tests for the BilkaToGo event platform."""

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
    """Test the Substitution and Missing item events once a Packed Order is announced."""
    freezer.move_to(SNAPSHOT_TIME)
    pack_substituted_order(bilka)
    with patch("custom_components.bilkatogo.PLATFORMS", [Platform.EVENT]):
        entry = await setup_integration()
        await hass.async_block_till_done()

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)
