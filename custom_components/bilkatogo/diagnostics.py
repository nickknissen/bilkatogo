import dataclasses
from datetime import date
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import BilkaToGoConfigEntry

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "title",
    "unique_id",
    "pickup_code",
    "order_pickup_code",
    "deliveryAddress",
    "address",
    "zipCode",
    "zip_code",
    "zipcity",
    "city",
    "phone",
    "uid",
    "UID",
    "tid",
    "customerId",
    "customer_id",
    "token",
    "id_token",
    "access_token",
    "refresh_token",
    "sessionToken",
    "sessionSecret",
    "login_token",
    "jwt",
    "cookies",
    "PHPSESSID",
    "jwt_validation",
}

EACH = "*"

CUSTOMER_NAME_PATHS = (
    ("data", "order", "_raw", "name"),
    ("data", "orders", EACH, "_raw", "name"),
)


def _redact_path(node: Any, path: tuple[str, ...]) -> None:
    head, *rest = path
    if head == EACH and isinstance(node, list):
        for item in node:
            _redact_path(item, tuple(rest))
    elif not isinstance(node, dict) or head not in node:
        return
    elif rest:
        _redact_path(node[head], tuple(rest))
    else:
        node[head] = REDACTED


def _redact_paths(data: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> dict[str, Any]:
    for path in paths:
        _redact_path(data, path)
    return data


# Order Numbers identify the customer's account, so each one is replaced by a label that
# stays the same across the file, which keeps an announced substitution tied to its order.
ORDER_NUMBER_KEYS = {"order_number", "orderId"}


def _label_order_numbers(node: Any, labels: dict[str, str]) -> None:
    if isinstance(node, list):
        for item in node:
            _label_order_numbers(item, labels)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key in ORDER_NUMBER_KEYS and isinstance(value, str | int):
                node[key] = labels.setdefault(str(value), f"order_{len(labels) + 1}")
            else:
                _label_order_numbers(value, labels)


def _text_keys(node: Any) -> Any:
    """Diagnostics are written as JSON, whose keys must be text; `days_without_slots` is keyed
    by date."""
    if isinstance(node, list):
        return [_text_keys(item) for item in node]
    if isinstance(node, dict):
        return {
            key.isoformat() if isinstance(key, date) else key: _text_keys(value)
            for key, value in node.items()
        }
    return node


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BilkaToGoConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data.data
    diagnostics = async_redact_data(
        {
            "entry": entry.as_dict(),
            "data": _text_keys(dataclasses.asdict(data)) if data is not None else None,
        },
        TO_REDACT,
    )
    _label_order_numbers(diagnostics, {})
    return _redact_paths(diagnostics, CUSTOMER_NAME_PATHS)
