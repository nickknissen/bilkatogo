import re
from typing import Any

NAME = "Kunde Eksempel"
EMAIL = "kunde@example.com"
PHONE = "12345678"
PICKUP_CODE = "12345678"
UID = 900001
TID = "00000000-0000-0000-0000-000000000000"
ORDER_NUMBER_BASE = 900000000000

PLACEHOLDERS: dict[str, Any] = {
    **dict.fromkeys(
        [
            "recipient_name",
            "firstname",
            "firstName",
            "first_name",
            "lastname",
            "lastName",
            "last_name",
            "fullname",
            "fullName",
            "full_name",
            "customer_name",
            "customerName",
        ],
        NAME,
    ),
    **dict.fromkeys(["email", "mail", "emailAddress"], EMAIL),
    **dict.fromkeys(
        ["phone", "mobile", "telephone", "phoneNumber", "phone_number", "mobilePhone"], PHONE
    ),
    **dict.fromkeys(["order_pickup_code", "pickup_code", "pickupCode"], PICKUP_CODE),
    "uid": UID,
    "tid": TID,
}
ORDER_NUMBER_KEYS = frozenset({"orderId", "order_id", "orderNumber", "order_number"})
HOME_ADDRESS_PLACEHOLDERS: dict[str, Any] = {
    # A Pickup Point's name is public; a home address's can be anyone in the household.
    "name": NAME,
    "temporary_zipcode": "9999",
    "address": "Testvej 1",
    "street": "Testvej 1",
    "location": "Testvej 1",
    "zip": "9999",
    "zipCode": "9999",
    "city": "Eksempelby",
    "lat": "0",
    "lng": "0",
}
UNSET = (None, "", -1, 0, False)
MIN_TEXT_MATCH = 6
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# A Danish phone number in text, either "+45" and eight digits or written in pairs
# ("60 12 34 56"). Eight digits in a row without "+45" are left alone: article numbers
# look the same.
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?<!\d )(?:\+45 ?)?\d{2} \d{2} \d{2} \d{2}(?! ?\d)|\+45 ?\d{8}(?!\d)"
)


def redact(data: Any) -> Any:
    """Bilka's JSON with personal data replaced by the placeholders the fixtures use.

    Pickup Point addresses are public and kept; an address marked as home delivery is not.
    Every replaced value of six or more characters, and any email address or Danish phone
    number, is also replaced inside other text.
    """
    replaced: dict[str, str] = {}
    redacted = _redact(data, replaced, {})
    return _replace_in_text(redacted, replaced)


def _redact(data: Any, replaced: dict[str, str], order_numbers: dict[str, str]) -> Any:
    if isinstance(data, list):
        return [_redact(item, replaced, order_numbers) for item in data]
    if not isinstance(data, dict):
        return data
    home = _is_home_address(data)
    result = {}
    for key, value in data.items():
        placeholder = _placeholder(key, value, home, order_numbers)
        if placeholder is None:
            result[key] = _redact(value, replaced, order_numbers)
        else:
            replaced[str(value)] = str(placeholder)
            result[key] = placeholder
    return result


def _placeholder(key: str, value: Any, home: bool, order_numbers: dict[str, str]) -> Any:
    if value in UNSET or isinstance(value, dict | list):
        return None
    if key in ORDER_NUMBER_KEYS:
        number = str(value)
        if number not in order_numbers:
            order_numbers[number] = str(ORDER_NUMBER_BASE + len(order_numbers) + 1)
        return order_numbers[number]
    if key in PLACEHOLDERS:
        return PLACEHOLDERS[key]
    if home and key in HOME_ADDRESS_PLACEHOLDERS:
        return HOME_ADDRESS_PLACEHOLDERS[key]
    return None


def _is_home_address(data: dict[str, Any]) -> bool:
    if "home_delivery" in data:
        return bool(data["home_delivery"])
    return bool(data.get("recipient_name"))


def _replace_in_text(data: Any, replaced: dict[str, str]) -> Any:
    if isinstance(data, list):
        return [_replace_in_text(item, replaced) for item in data]
    if isinstance(data, dict):
        return {key: _replace_in_text(value, replaced) for key, value in data.items()}
    if not isinstance(data, str):
        return data
    for original in sorted(replaced, key=len, reverse=True):
        if len(original) >= MIN_TEXT_MATCH:
            data = data.replace(original, replaced[original])
    return PHONE_PATTERN.sub(PHONE, EMAIL_PATTERN.sub(EMAIL, data))
