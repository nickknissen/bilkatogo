import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bilkatogo.cli import app
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, load_fixture


@pytest.fixture
def invoke(bilka: FakeBilka, tmp_path: Path):
    runner = CliRunner()

    def run(*args: str):
        env: dict[str, str | None] = {
            "BILKATOGO_CONFIG_DIR": str(tmp_path / "config"),
            "BILKATOGO_EMAIL": None,
            "BILKATOGO_PASSWORD": None,
        }
        return runner.invoke(
            app, [*args, "--email", EMAIL, "--password", PASSWORD], env=env, obj=bilka.client
        )

    return run


def test_cart_raw_prints_bilkas_json_as_received(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke("cart", "--raw")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == load_fixture("cart_logged_in.json")


def test_order_raw_prints_bilkas_json_as_received(invoke) -> None:
    result = invoke("order", "900000000004", "--raw")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == load_fixture("order_details_captured.json")


def test_order_raw_without_a_number_prints_the_current_order(invoke) -> None:
    result = invoke("order", "--raw")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == load_fixture("order_details_plain.json")


def test_order_raw_without_a_number_and_no_orders(invoke, bilka: FakeBilka) -> None:
    bilka.orders = []

    result = invoke("order", "--raw")

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "null"


@pytest.mark.parametrize("command", [["cart"], ["order"], ["order", "900000000004"]])
def test_redact_without_raw_is_an_error(invoke, bilka: FakeBilka, command: list[str]) -> None:
    result = invoke(*command, "--redact")

    assert result.exit_code != 0
    assert "--redact only works with --raw" in result.output
    assert bilka.requests == []


ORDER_NUMBER = "512345678901"
HOME_ADDRESS = {
    "recipient_name": "Karen Kundesen",
    "email": "karen.kundesen@gmail.example",
    "phone": "+4561234567",
    "address": "Rosenhaven 17, 1. tv",
    "street": "Rosenhaven 17, 1. tv",
    "zip": "5230",
    "zipCode": "5230",
    "city": "Odense M",
    "lat": "55.3699",
    "lng": "10.4289",
    "location": "Karen Kundesen, Rosenhaven 17",
}
PERSONAL_VALUES = [
    ORDER_NUMBER,
    "73914862",
    "4817263",
    "c0ffee00-1234-4abc-9def-0123456789ab",
    "61 23 45 67",
    "+45 87654321",
    *(value for key, value in HOME_ADDRESS.items() if key not in {"zip", "zipCode"}),
]


def home_delivered_order() -> dict:
    details = load_fixture("order_details_captured.json")
    order = details["order"]
    order["orderId"] = ORDER_NUMBER
    order["deliveryAddress"].update(HOME_ADDRESS, home_delivery=True, order_pickup_code="73914862")
    order["customer"] = {"uid": 4817263, "tid": "c0ffee00-1234-4abc-9def-0123456789ab"}
    order["note"] = f"Ordre {ORDER_NUMBER} til karen.kundesen@gmail.example"
    # Bilka writes the phone number into the receipt text without a phone field to match.
    order["receipt_content"] = {
        "pickup_body": "får du en sms på <b>61 23 45 67</b> med en påmindelse",
        "help": "Ring til +45 87654321",
    }
    return details


def test_redacted_order_capture_contains_no_personal_values(invoke, bilka: FakeBilka) -> None:
    original = home_delivered_order()
    bilka.details[ORDER_NUMBER] = original

    result = invoke("order", ORDER_NUMBER, "--raw", "--redact")

    assert result.exit_code == 0, result.output
    for value in PERSONAL_VALUES:
        assert value not in result.output
    order = json.loads(result.output)["order"]
    assert order["orderId"] == "900000000001"
    assert order["deliveryAddress"]["order_pickup_code"] == "12345678"
    assert order["deliveryAddress"]["address"] == "Testvej 1"
    assert order["deliveryAddress"]["zipCode"] == "9999"
    assert order["deliveryAddress"]["city"] == "Eksempelby"
    assert order["deliveryAddress"]["email"] == "kunde@example.com"
    assert order["customer"] == {"uid": 900001, "tid": "00000000-0000-0000-0000-000000000000"}
    assert order["note"] == "Ordre 900000000001 til kunde@example.com"
    assert order["receipt_content"] == {
        "pickup_body": "får du en sms på <b>12345678</b> med en påmindelse",
        "help": "Ring til 12345678",
    }
    assert order["lines"] == original["order"]["lines"]
    assert order["stat"] == original["order"]["stat"]


def test_redacted_cart_capture_keeps_the_public_pickup_point(
    invoke, logged_in_cart: FakeBilka
) -> None:
    cart = logged_in_cart.cart_contents
    cart.update(uid=4817263, tid="c0ffee00-1234-4abc-9def-0123456789ab")

    result = invoke("cart", "--raw", "--redact")

    assert result.exit_code == 0, result.output
    redacted = json.loads(result.output)
    assert (redacted["uid"], redacted["tid"]) == (900001, "00000000-0000-0000-0000-000000000000")
    assert redacted["deliveryAddress"] == cart["deliveryAddress"]
    assert redacted["lines"] == cart["lines"]


def test_home_delivery_cart_fixture_is_already_redacted(
    invoke, home_delivery_cart: FakeBilka
) -> None:
    result = invoke("cart", "--raw", "--redact")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == load_fixture("cart_home.json")


def test_redacted_home_delivery_cart_contains_no_personal_values(
    invoke, logged_in_cart: FakeBilka
) -> None:
    cart = logged_in_cart.cart_contents
    cart.update(uid=4817263, tid="c0ffee00-1234-4abc-9def-0123456789ab")
    cart["deliveryAddress"].update(HOME_ADDRESS)
    cart["deliveryDate"]["order_id"] = ORDER_NUMBER

    result = invoke("cart", "--raw", "--redact")

    assert result.exit_code == 0, result.output
    for value in PERSONAL_VALUES:
        assert value not in result.output
    address = json.loads(result.output)["deliveryAddress"]
    assert address["recipient_name"] == "Kunde Eksempel"
    assert (address["zip"], address["city"]) == ("9999", "Eksempelby")
