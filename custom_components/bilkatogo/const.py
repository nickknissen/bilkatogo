"""Constants for the BilkaToGo integration."""

from datetime import timedelta
from logging import getLogger

from homeassistant.const import Platform

LOGGER = getLogger(__package__)

DOMAIN = "bilkatogo"
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
]
CURRENCY = "DKK"
WEBSITE_URL = "https://www.bilkatogo.dk"

UPDATE_INTERVAL = timedelta(minutes=60)
FAST_UPDATE_INTERVAL = timedelta(minutes=5)
FAST_UPDATE_LEAD_TIME = timedelta(hours=3)
