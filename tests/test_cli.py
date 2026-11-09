import asyncio
import io
import json
import sys
from importlib.metadata import entry_points
from pathlib import Path

import httpx
import pytest
from freezegun.api import FrozenDateTimeFactory
from typer.testing import CliRunner

from bilkatogo import FileTokenStorage
from bilkatogo.cli import app
from tests.fake_bilka import EMAIL, PASSWORD, FakeBilka, algolia_query, form, load_fixture


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "config"


@pytest.fixture
def invoke(bilka: FakeBilka, config_dir: Path):
    runner = CliRunner()

    def run(*args: str, env: dict[str, str] | None = None, input: str | None = None):
        full_env: dict[str, str | None] = {
            "BILKATOGO_CONFIG_DIR": str(config_dir),
            "BILKATOGO_EMAIL": None,
            "BILKATOGO_PASSWORD": None,
            **(env or {}),
        }
        return runner.invoke(app, list(args), env=full_env, input=input, obj=bilka.client)

    return run


def logged_in_as(bilka: FakeBilka) -> str:
    [login] = bilka.calls("gigya_login")
    return form(login)["loginID"]


def test_help_lists_the_login_command(invoke) -> None:
    result = invoke("--help")

    assert result.exit_code == 0
    assert "login" in result.output


def test_login_with_flags_stores_the_session(invoke, config_dir: Path) -> None:
    result = invoke("login", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert EMAIL in result.output
    session = asyncio.run(FileTokenStorage(config_dir / "session.json").load())
    assert session is not None
    assert session["jwt"] == "eyFake.jwt.token"


def test_login_reads_credentials_from_environment(invoke, bilka: FakeBilka) -> None:
    result = invoke("login", env={"BILKATOGO_EMAIL": EMAIL, "BILKATOGO_PASSWORD": PASSWORD})

    assert result.exit_code == 0, result.output
    assert logged_in_as(bilka) == EMAIL


def test_flags_win_over_environment(invoke, bilka: FakeBilka) -> None:
    result = invoke(
        "login",
        "--email",
        EMAIL,
        "--password",
        PASSWORD,
        env={"BILKATOGO_EMAIL": "other@example.com", "BILKATOGO_PASSWORD": "other"},
    )

    assert result.exit_code == 0, result.output
    assert logged_in_as(bilka) == EMAIL


def test_environment_wins_over_config_file(invoke, bilka: FakeBilka, config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"email": "other@example.com", "password": "other"}), encoding="utf-8"
    )

    result = invoke("login", env={"BILKATOGO_EMAIL": EMAIL, "BILKATOGO_PASSWORD": PASSWORD})

    assert result.exit_code == 0, result.output
    assert logged_in_as(bilka) == EMAIL


def test_login_reads_credentials_from_config_file(
    invoke, bilka: FakeBilka, config_dir: Path
) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"email": EMAIL, "password": PASSWORD}), encoding="utf-8"
    )

    result = invoke("login")

    assert result.exit_code == 0, result.output
    assert logged_in_as(bilka) == EMAIL


def test_login_prompts_for_missing_credentials(invoke, bilka: FakeBilka) -> None:
    result = invoke("login", input=f"{EMAIL}\n{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert logged_in_as(bilka) == EMAIL
    assert PASSWORD not in result.output


def test_wrong_password_prints_a_clear_message_and_exits_non_zero(invoke, config_dir: Path) -> None:
    result = invoke("login", "--email", EMAIL, "--password", "forkert")

    assert result.exit_code == 1
    assert "Wrong email or password" in result.output
    assert not (config_dir / "session.json").exists()


def test_login_json_output(invoke) -> None:
    result = invoke("login", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"email": EMAIL, "logged_in": True}


def test_orders_uses_the_stored_session_and_prints_a_table(invoke, bilka: FakeBilka) -> None:
    invoke("login", "--email", EMAIL, "--password", PASSWORD)

    result = invoke("orders")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["Order", "Ordered", "Pickup", "Slot", "Pickup", "Point", "Status"]
    assert lines[1].split() == [
        "900000000002",
        "2026-11-01",
        "20:15",
        "Tue",
        "03/11",
        "16:00-18:00",
        "Bilka",
        "Eksempelby",
        "Received",
    ]
    assert lines[2].split()[0] == "900000000001"
    assert lines[2].split()[-2:] == ["Picked", "Up"]
    assert len(bilka.calls("gigya_login")) == 1


def test_orders_limit(invoke, bilka: FakeBilka) -> None:
    invoke("login", "--email", EMAIL, "--password", PASSWORD)

    result = invoke("orders", "--limit", "1")

    assert result.exit_code == 0, result.output
    assert "900000000002" in result.output
    assert "900000000001" not in result.output
    [request] = bilka.calls("order_history")
    assert request.url.params["limit"] == "1"


def test_orders_json_output(invoke) -> None:
    invoke("login", "--email", EMAIL, "--password", PASSWORD)

    result = invoke("orders", "--json")

    assert result.exit_code == 0, result.output
    upcoming, collected = json.loads(result.output)
    assert upcoming == {
        "order_number": "900000000002",
        "status": "received",
        "status_code": "Bestilt",
        "ordered_at": "2026-11-01T20:15:00+01:00",
        "pickup_slot": {"date": "2026-11-03", "start": "16:00:00", "end": "18:00:00"},
        "pickup_point": {
            "name": "Bilka Eksempelby",
            "address": "Testvej 1",
            "zip_code": "9999",
            "city": "Eksempelby",
            "store_id": None,
        },
        "home_delivery": False,
        "delivered": False,
        "ordered_total": "456.00",
        "order_total": "449.00",
        "content": "Æbler, Havregryn, Letmælk",
    }
    assert collected["status"] == "picked_up"


def test_orders_json_shows_bilkas_estimate_before_packing(invoke, bilka: FakeBilka) -> None:
    bilka.orders = load_fixture("order_history_received.json")["orders"]
    invoke("login", "--email", EMAIL, "--password", PASSWORD)

    result = invoke("orders", "--json")

    assert result.exit_code == 0, result.output
    [received] = json.loads(result.output)
    assert received["status"] == "received"
    assert received["ordered_total"] == "876.53"
    assert received["order_total"] == "859.98"


def test_orders_without_a_session_prompts_for_the_password(
    invoke, bilka: FakeBilka, config_dir: Path
) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(json.dumps({"email": EMAIL}), encoding="utf-8")

    result = invoke("orders", input=f"{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert logged_in_as(bilka) == EMAIL
    assert "900000000002" in result.output
    assert (config_dir / "session.json").exists()


def expire_session(config_dir: Path, *, keep_login_token: bool) -> None:
    path = config_dir / "session.json"
    session = json.loads(path.read_text(encoding="utf-8"))
    session["jwt_expires_at"] = "2020-01-01T00:00:00+00:00"
    if not keep_login_token:
        del session["login_token"]
    path.write_text(json.dumps(session), encoding="utf-8")


def test_an_expired_session_is_renewed_without_asking_for_the_password(
    invoke, bilka: FakeBilka, config_dir: Path
) -> None:
    invoke("login", "--email", EMAIL, "--password", PASSWORD)
    expire_session(config_dir, keep_login_token=True)

    result = invoke("orders")

    assert result.exit_code == 0, result.output
    assert "Password" not in result.output
    assert len(bilka.calls("gigya_login")) == 1
    assert len(bilka.calls("gigya_jwt")) == 2


def test_a_dead_session_names_the_login_command(invoke, bilka: FakeBilka, config_dir: Path) -> None:
    invoke("login", "--email", EMAIL, "--password", PASSWORD)
    expire_session(config_dir, keep_login_token=False)

    result = invoke("orders", input=f"{PASSWORD}\n")

    assert result.exit_code == 1
    assert "Password" not in result.output
    assert "Session expired. Run `bilkatogo login`." in result.output
    assert len(bilka.calls("gigya_login")) == 1


def test_orders_with_a_wrong_password_fails_clearly(invoke) -> None:
    result = invoke("orders", "--email", EMAIL, "--password", "forkert")

    assert result.exit_code == 1
    assert "Wrong email or password" in result.output


def test_orders_when_there_are_none(invoke, bilka: FakeBilka) -> None:
    bilka.orders = []

    result = invoke("orders", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert "No orders" in result.output


@pytest.mark.parametrize("command", ["login", "orders"])
@pytest.mark.parametrize(
    ("route", "failure", "message"),
    [
        ("gigya_login", httpx.ConnectError("fake: refused"), "Could not reach Bilka"),
        ("gigya_jwt", httpx.ReadTimeout("fake: timed out"), "Could not reach Bilka"),
        ("gigya_jwt", httpx.Response(503, text="<html>Down</html>"), "Bilka answered unexpectedly"),
        (
            "gigya_login",
            httpx.Response(403, json={"errorCode": 403048, "errorMessage": "Too many requests"}),
            "temporarily unavailable",
        ),
    ],
)
def test_failures_print_a_clear_message_and_exit_non_zero(
    invoke, bilka: FakeBilka, command: str, route: str, failure, message: str
) -> None:
    bilka.script(route, failure)

    result = invoke(command, "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (httpx.ConnectError("fake: refused"), "Could not reach Bilka"),
        (httpx.Response(200, text="<html>Vedligehold</html>"), "Bilka answered unexpectedly"),
    ],
)
def test_orders_failures_on_the_order_history_call(
    invoke, bilka: FakeBilka, failure, message: str
) -> None:
    bilka.script("order_history", failure)

    result = invoke("orders", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 1
    assert message in result.output


def test_order_prints_details_and_substitutions(invoke, bilka: FakeBilka) -> None:
    invoke("login", "--email", EMAIL, "--password", PASSWORD)

    result = invoke("order", "900000000001")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Order         900000000001",
        "Status        Picked Up",
        "Pickup Slot   Tue 27/10 12:00-14:00",
        "Pickup Point  Bilka Eksempelby",
        "Pickup Code   12345678",
        "Ordered Total 512.00 kr",
        "Order Total   509.00 kr",
        "Deposit       0.00 kr",
        "Service Fee   0.00 kr",
        "Packaging     0.00 kr",
        "Delivery Fee  0.00 kr",
        "Discount      0.00 kr",
        "Substitutions",
        "  4 x Æbler 1 kg -> 4 x Pærer 750 g",
        "  2 x Letmælk 1 l -> 3 x Minimælk 0,5 l",
        "Missing Items none",
    ]
    assert len(bilka.calls("gigya_login")) == 1


def test_order_shows_sizes_when_the_replacement_has_the_same_name(invoke) -> None:
    result = invoke("order", "900000000005", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert (
        lines[lines.index("Substitutions") + 1]
        == "  1 x Klementiner 750 g -> 1 x Klementiner 900 g"
    )


def test_order_shows_the_order_total_breakdown(invoke) -> None:
    result = invoke("order", "900000000004", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[lines.index("Pickup Code   11223344") + 1 :] == [
        "Ordered Total ",
        "Order Total   793.80 kr",
        "Deposit       1.50 kr",
        "Service Fee   39.00 kr",
        "Packaging     0.00 kr",
        "Delivery Fee  0.00 kr",
        "Discount      0.00 kr",
        "Substitutions none",
        "Missing Items none",
    ]


ORDER_TOTAL_KEYS = (
    "order_total",
    "ordered_total",
    "deposit",
    "service_fee",
    "packaging",
    "delivery_fee",
    "discount",
)


def test_order_json_includes_the_order_total_breakdown(invoke) -> None:
    result = invoke("order", "900000000004", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    details = json.loads(result.output)
    assert {key: details[key] for key in ORDER_TOTAL_KEYS} == {
        "order_total": "793.80",
        "ordered_total": None,
        "deposit": "1.50",
        "service_fee": "39.00",
        "packaging": "0.00",
        "delivery_fee": "0.00",
        "discount": "0.00",
    }


def test_order_without_substitutions_says_so(invoke) -> None:
    result = invoke("order", "900000000002", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert "Pickup Code   87654321" in result.output
    assert "Substitutions none" in result.output.splitlines()


def test_order_lists_missing_items_after_substitutions(invoke) -> None:
    result = invoke("order", "900000000003", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-5:] == [
        "Substitutions",
        "  2 x Letmælk -> 3 x Minimælk",
        "Missing Items",
        "  Skyr: 2 of 4 missing",
        "  Havregryn: 1 of 1 missing",
    ]


def test_order_json_includes_missing_items(invoke) -> None:
    result = invoke("order", "900000000003", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["missing_items"] == [
        {
            "product": "Skyr",
            "article_number": "932058",
            "ordered_quantity": 4,
            "missing_quantity": 2,
        },
        {
            "product": "Havregryn",
            "article_number": "1006",
            "ordered_quantity": 1,
            "missing_quantity": 1,
        },
    ]


def test_order_json_output(invoke) -> None:
    result = invoke("order", "900000000001", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    details = json.loads(result.output)
    assert details["order_number"] == "900000000001"
    assert details["status"] == "picked_up"
    assert details["pickup_slot"] == {"date": "2026-10-27", "start": "12:00:00", "end": "14:00:00"}
    assert details["pickup_point"]["name"] == "Bilka Eksempelby"
    assert details["pickup_code"] == {"code": "12345678"}
    assert details["substitutions"][0] == {
        "original_product": "Æbler",
        "original_article_number": "1001",
        "original_size": "1 kg",
        "ordered_quantity": 4,
        "replacement_product": "Pærer",
        "replacement_article_number": "1002",
        "replacement_size": "750 g",
        "quantity": 4,
        "replacement_price": "4.00",
        "replacement_line_total": "12.00",
    }
    assert details["lines"][1] == {
        "category": "Frugt & grønt",
        "article_number": "1003",
        "name": "Rugbrød",
        "quantity": 1,
        "amount": "25.00",
        "substituted": False,
        "unavailable_reason": "",
    }


def test_order_that_does_not_exist_fails_clearly(invoke) -> None:
    result = invoke("order", "900000000099", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 1
    assert "Could not fetch order 900000000099" in result.output


def test_order_without_a_number_prints_the_current_order(invoke, bilka: FakeBilka) -> None:
    result = invoke("order", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:3] == [
        "Order         900000000002",
        "Status        Received",
        "Pickup Slot   Tue 03/11 16:00-18:00",
    ]


def test_order_without_a_number_and_no_orders(invoke, bilka: FakeBilka) -> None:
    bilka.orders = []

    result = invoke("order", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "No orders."


def test_order_without_a_number_and_no_orders_json(invoke, bilka: FakeBilka) -> None:
    bilka.orders = []

    result = invoke("order", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) is None


def test_pickup_points_table_marks_the_carts_pickup_point(invoke) -> None:
    result = invoke("pickup-points", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["Pickup", "Point", "Address", "Store"]
    assert len(lines) == 23
    [chosen] = [line for line in lines if line.startswith("*")]
    assert chosen.split()[1:4] == ["Bilka", "Waves,", "Hundige"]
    assert chosen.split()[-1] == "1653"
    [fredericia] = [line for line in lines if "Pickup Fredericia" in line]
    assert "Strandvejen 220, 7000 Fredericia" in fredericia
    assert fredericia.split()[-1] == "1662"


def test_pickup_points_json(invoke) -> None:
    result = invoke("pickup-points", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    points = json.loads(result.output)
    assert len(points) == 22
    assert points[1] == {
        "name": "Pickup Fredericia",
        "address": "Strandvejen 220",
        "zip_code": "7000",
        "city": "Fredericia",
        "store_id": "1662",
        "chosen": False,
    }
    assert [point["name"] for point in points if point["chosen"]] == ["Bilka Waves, Hundige"]


def search_rows(output: str) -> dict[str, str]:
    return {line.split()[0]: line for line in output.splitlines()[1:]}


def test_search_lists_products_with_price_stock_and_offer(invoke) -> None:
    result = invoke("search", "e", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    header, *_ = result.output.splitlines()
    assert header.split() == [
        "Article",
        "Product",
        "Size",
        "Brand",
        "Price",
        "Unit",
        "Price",
        "Stock",
        "Offer",
    ]
    rows = search_rows(result.output)
    assert list(rows) == ["19753", "128298", "18381", "140787", "169794"]
    assert "u. tilsat sukker  1 kg " in rows["19753"]
    assert "Cheasy" in rows["19753"]
    assert "30.95 kr  30.95 kr/kg  In stock" in rows["19753"]
    assert rows["128298"].rstrip().endswith("Skarp pris: 25.00 kr")
    assert rows["18381"].rstrip().endswith("Mix 10 for 25,00")
    assert "Out of stock" in rows["140787"]
    assert rows["169794"].rstrip().endswith("Spar 4,95: 60.00 kr until 2026-10-29")
    assert "64.95 kr  99.92 kr/kg  Out of stock" in rows["169794"]


def test_search_pickup_point_and_limit(invoke, bilka: FakeBilka) -> None:
    result = invoke(
        "search",
        "skyr",
        "--pickup-point",
        "Bilka Vejle",
        "--limit",
        "1",
        "--email",
        EMAIL,
        "--password",
        PASSWORD,
    )

    assert result.exit_code == 0, result.output
    assert list(search_rows(result.output)) == ["19753"]
    query = algolia_query(bilka.calls("algolia")[0])
    assert query["filters"] == "isInAssortmentIn:1661 AND nonsearchable:false"
    assert query["hitsPerPage"] == "1"


def test_search_shows_the_limit_per_customer(invoke, bilka: FakeBilka) -> None:
    bilka.products[1]["storeData"]["1653"]["offerMax"] = 3

    result = invoke("search", "skyr", "--email", EMAIL, "--password", PASSWORD)

    assert (
        search_rows(result.output)["128298"]
        .rstrip()
        .endswith("Skarp pris: 25.00 kr, max 3 per customer")
    )


def test_search_without_matches(invoke) -> None:
    result = invoke("search", "lakrids", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "No products."


def test_search_at_an_unknown_pickup_point_fails_clearly(invoke) -> None:
    result = invoke(
        "search",
        "skyr",
        "--pickup-point",
        "Bilka Atlantis",
        "--email",
        EMAIL,
        "--password",
        PASSWORD,
    )

    assert result.exit_code == 1
    assert "Unknown Pickup Point 'Bilka Atlantis'" in result.output


def test_search_filters_and_sort(invoke, bilka: FakeBilka) -> None:
    result = invoke(
        "search",
        "e",
        "--in-stock",
        "--on-offer",
        "--sort",
        "price",
        "--email",
        EMAIL,
        "--password",
        PASSWORD,
    )

    assert result.exit_code == 0, result.output
    assert list(search_rows(result.output)) == ["18381", "128298"]
    query = algolia_query(bilka.calls("algolia")[0])
    assert query["filters"] == (
        "isInAssortmentIn:1653 AND nonsearchable:false AND inStockStore:1653 AND isInOffer:1653"
    )


def test_search_in_leaflet(invoke, bilka: FakeBilka) -> None:
    result = invoke("search", "e", "--in-leaflet", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert "No products." in result.output
    query = algolia_query(bilka.calls("algolia")[0])
    assert query["filters"].endswith(" AND isInCurrentLeaflet:true")


def test_search_json(invoke) -> None:
    result = invoke("search", "skyr", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    plain, offered = json.loads(result.output)
    assert plain["offer"] is None
    assert offered == {
        "article_number": "128298",
        "name": "Skyr m. vanilje 0,2 % fedt øko",
        "brand": "Salling ØKO",
        "store_id": "1653",
        "price": "39.31",
        "unit_price": "49.14",
        "unit": "kg",
        "in_stock": True,
        "offer": {
            "label": "Skarp pris",
            "price": "25.00",
            "unit_price": "31.25",
            "ends": None,
            "limit_per_customer": None,
        },
        "multibuy_offer": None,
        "ean": "5712876340580",
        "size": "800 g",
        "category": "Mejeri & køl > Mejeri > Yoghurt & syrnede mælkeprodukter > Skyr "
        "> Skyr m. smag",
    }


def test_products_prints_the_products_asked_for_in_order(invoke, bilka: FakeBilka) -> None:
    result = invoke("products", "18381", "99999", "19753", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].split()[:3] == ["Article", "Product", "Size"]
    rows = search_rows(result.output)
    assert list(rows) == ["18381", "19753"]
    assert rows["18381"].split()[:3] == ["18381", "Bananer", "1"]


def test_products_at_a_pickup_point_as_json(invoke, bilka: FakeBilka) -> None:
    result = invoke(
        "products",
        "140787",
        "--pickup-point",
        "Pickup Fredericia",
        "--json",
        "--email",
        EMAIL,
        "--password",
        PASSWORD,
    )

    assert result.exit_code == 0, result.output
    [product] = json.loads(result.output)
    assert (product["article_number"], product["store_id"]) == ("140787", "1662")
    assert product["size"] == "200 g"


def test_products_none_found(invoke) -> None:
    result = invoke("products", "99999", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "No products."


def test_cart_prints_cart_lines_total_and_pickup_point(
    invoke, logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-30T12:00:00+01:00")

    result = invoke("cart", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["Article", "Product", "Quantity", "Price", "Line", "Total"]
    bananas = lines[1].split()
    assert bananas[:2] == ["18381", "Bananer"]
    assert bananas[2:] == ["10", "2.75", "kr", "25.00", "kr"]
    assert lines[6:] == [
        "Pickup Point   Bilka Vejle",
        "Pickup Slot    Sun 01/11 14:00-16:00",
        "Next Available 2026-10-27",
        "Cart Total     127.00 kr",
        "Deposit        0.00 kr",
        "Service Fee    39.00 kr",
        "Packaging      0.00 kr",
        "Delivery Fee   0.00 kr",
        "Discount       9.50 kr",
    ]


def test_home_delivery_cart_prints_the_minimum_order_and_not_the_address(
    invoke, home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-02T12:00:00+01:00")

    result = invoke("cart", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1 + 2 :] == [
        "Pickup Point   Home Delivery",
        "Pickup Slot    Wed 11/11 15:00-22:00",
        "Next Available 2026-11-09",
        "Cart Total     101.40 kr",
        "Deposit        0.00 kr",
        "Service Fee    39.00 kr",
        "Packaging      0.00 kr",
        "Delivery Fee   39.00 kr",
        "Discount       0.00 kr",
        "Minimum Order  500.00 kr (398.60 kr left)",
        "Warning from Bilka: 398,60 kr. fra minimumskøb på 500,00 kr. alt inkl. "
        "for at få hjemmelevering",
    ]
    for personal in ("Kunde Eksempel", "Testvej", "Eksempelby"):
        assert personal not in result.output


def test_minimum_order_reached_prints_just_the_amount(
    invoke, home_delivery_cart: FakeBilka
) -> None:
    home_delivery_cart.cart_contents["stat"]["minimum_left"] = 0

    result = invoke("cart", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "Minimum Order  500.00 kr"


def test_home_delivery_cart_json(invoke, home_delivery_cart: FakeBilka) -> None:
    result = invoke("cart", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    cart = json.loads(result.output)
    assert {
        key: cart[key]
        for key in (
            "home_delivery",
            "pickup_point",
            "store_id",
            "delivery_fee",
            "minimum_order",
            "minimum_left",
            "next_available_date",
            "warnings",
        )
    } == {
        "home_delivery": True,
        "pickup_point": None,
        "store_id": "1661",
        "delivery_fee": "39.00",
        "minimum_order": "500.00",
        "minimum_left": "398.60",
        "next_available_date": "2026-11-09",
        "warnings": ["398,60 kr. fra minimumskøb på 500,00 kr. alt inkl. for at få hjemmelevering"],
    }


def test_empty_cart_says_so(invoke) -> None:
    result = invoke("cart", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "No Cart Lines."
    assert lines[1] == "Pickup Point   Bilka Waves, Hundige"


def test_cart_json(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke("cart", "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    cart = json.loads(result.output)
    assert cart["lines"][0] == {
        "product": "Bananer",
        "article_number": "18381",
        "quantity": 10,
        "price": "2.75",
        "line_total": "25.00",
    }
    assert len(cart["lines"]) == 5
    assert {key: cart[key] for key in ("total", "deposit", "service_fee", "packaging")} == {
        "total": "127.00",
        "deposit": "0.00",
        "service_fee": "39.00",
        "packaging": "0.00",
    }
    assert cart["discount"] == "9.50"
    assert cart["pickup_point"]["name"] == "Bilka Vejle"
    assert cart["pickup_point"]["store_id"] == "1661"
    assert cart["home_delivery"] is False
    assert cart["minimum_order"] is None
    assert "pickup_slot" in cart


def test_cart_set_prints_the_updated_cart(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke("cart", "set", "18364", "3", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "3"
    agurk = next(row for row in result.output.splitlines() if "Agurk" in row)
    assert agurk.split()[2:] == ["3", "7.00", "kr", "21.00", "kr"]
    assert "Cart Total" in result.output


def test_cart_add_adds_the_quantity_to_the_cart_line(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke(
        "cart", "add", "18381", "--quantity", "2", "--email", EMAIL, "--password", PASSWORD
    )

    assert result.exit_code == 0, result.output
    bananas = next(row for row in result.output.splitlines() if "Bananer" in row)
    assert bananas.split()[2] == "12"


def test_cart_add_defaults_to_one(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke("cart", "add", "19753", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "1"
    assert "Skyr m. vanilje" in result.output


@pytest.fixture
def no_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("bilkatogo.client.sleep", sleep)


def change_rows(output: str) -> dict[str, str]:
    table, _, _ = output.partition("\n\n")
    return search_rows(table)


@pytest.mark.usefixtures("no_pauses")
def test_cart_add_items_adds_each_and_prints_the_results_and_cart(
    invoke, logged_in_cart: FakeBilka
) -> None:
    result = invoke(
        "cart", "add-items", "18381:2", "19753", "--email", EMAIL, "--password", PASSWORD
    )

    assert result.exit_code == 0, result.output
    counts = [request.url.params["count"] for request in logged_in_cart.calls("change_line_count")]
    assert counts == ["12", "1"]
    rows = change_rows(result.output)
    assert rows["18381"].split()[1:3] == ["12", "applied"]
    assert rows["19753"].split()[1:3] == ["1", "applied"]
    assert "Cart Total" in result.output


@pytest.mark.usefixtures("no_pauses")
def test_cart_add_items_reports_a_rejection_and_fails_after_adding_the_rest(
    invoke, logged_in_cart: FakeBilka
) -> None:
    logged_in_cart.script("change_line_count", load_fixture("change_line_count_unavailable.json"))

    result = invoke(
        "cart", "add-items", "88672", "19753:2", "--email", EMAIL, "--password", PASSWORD
    )

    assert result.exit_code == 1
    rows = change_rows(result.output)
    assert "rejected" in rows["88672"]
    assert "ikke aktuelt tilgængeligt" in rows["88672"]
    assert rows["19753"].split()[1:3] == ["2", "applied"]
    assert "Cart Total" in result.output


@pytest.mark.usefixtures("no_pauses")
def test_cart_add_items_json(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke(
        "cart",
        "add-items",
        "19753",
        "--quantity",
        "3",
        "--email",
        EMAIL,
        "--password",
        PASSWORD,
        "--json",
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["changes"] == [
        {
            "article_number": "19753",
            "requested": 3,
            "applied": 3,
            "status": "applied",
            "message": None,
        }
    ]
    assert data["cart"]["pickup_point"]["name"] == "Bilka Vejle"


@pytest.mark.parametrize("item", ["18381:x", "18381:0", ":2"])
def test_cart_add_items_refuses_a_malformed_item(
    invoke, logged_in_cart: FakeBilka, item: str
) -> None:
    result = invoke("cart", "add-items", item, "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 1
    assert "ARTICLE:QUANTITY" in result.output
    assert logged_in_cart.calls("change_line_count") == []


def test_cart_remove_drops_the_cart_line(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke("cart", "remove", "18364", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    [request] = logged_in_cart.calls("change_line_count")
    assert request.url.params["count"] == "0"
    assert "Agurk" not in result.output
    assert "Bananer" in result.output


def test_cart_remove_with_a_quantity_takes_it_off_the_cart_line(
    invoke, logged_in_cart: FakeBilka
) -> None:
    result = invoke(
        "cart", "remove", "18381", "--quantity", "3", "--email", EMAIL, "--password", PASSWORD
    )

    assert result.exit_code == 0, result.output
    bananas = next(row for row in result.output.splitlines() if "Bananer" in row)
    assert bananas.split()[2] == "7"


def test_cart_change_prints_bilkas_warning_and_succeeds(invoke, logged_in_cart: FakeBilka) -> None:
    logged_in_cart.change_answer = load_fixture("change_line_count_warning.json")

    result = invoke("cart", "set", "18364", "99", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == (
        "Warning from Bilka: Ring venligst til kundeservice på 89 30 30 00 inden bestilling, "
        "for at sikre det ønskede antal"
    )


def test_cart_change_rejected_prints_bilkas_message_and_fails(
    invoke, logged_in_cart: FakeBilka
) -> None:
    logged_in_cart.max_quantity = 2
    logged_in_cart.change_answer = {
        **load_fixture("change_line_count.json"),
        "offerLimitMessage": "Maks. 2 pr. kunde",
    }

    result = invoke(
        "cart", "add", "18364", "--quantity", "4", "--email", EMAIL, "--password", PASSWORD
    )

    assert result.exit_code == 1
    assert "Could not change the cart: Maks. 2 pr. kunde" in result.output
    assert "Cart Total" not in result.output


@pytest.mark.parametrize(
    "args",
    [("add", "18364"), ("set", "18364", "2"), ("remove", "18381")],
)
def test_cart_changes_print_the_updated_cart_as_json_with_warnings(
    invoke, logged_in_cart: FakeBilka, args: tuple[str, ...]
) -> None:
    result = invoke("cart", *args, "--email", EMAIL, "--password", PASSWORD, "--json")

    assert result.exit_code == 0, result.output
    cart = json.loads(result.output)
    quantities = {line["article_number"]: line["quantity"] for line in cart["lines"]}
    assert quantities.get(args[1], 0) == {"add": 2, "set": 2, "remove": 0}[args[0]]
    assert cart["warnings"] == []
    assert cart["pickup_point"]["name"] == "Bilka Vejle"


def test_there_is_no_cart_clear_command(invoke, logged_in_cart: FakeBilka) -> None:
    result = invoke("cart", "clear", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code != 0
    assert logged_in_cart.calls("empty_cart") == []


@pytest.mark.parametrize(
    ("group_options", "message"),
    [
        (["--raw"], "--raw only works without a subcommand, not with cart add."),
        (["--redact"], "--redact only works without a subcommand, not with cart add."),
        (["--raw", "--redact"], "--raw only works without a subcommand, not with cart add."),
        (["--json"], "Put --json after the subcommand: cart add --json."),
    ],
)
def test_cart_output_options_before_a_subcommand_are_an_error(
    invoke, logged_in_cart: FakeBilka, group_options: list[str], message: str
) -> None:
    result = invoke(
        "cart", *group_options, "add", "18364", "--email", EMAIL, "--password", PASSWORD
    )

    assert result.exit_code == 1
    assert message in result.output
    assert logged_in_cart.requests == []


def test_entry_point_without_the_cli_extra_explains_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    [script] = entry_points(group="console_scripts", name="bilkatogo")
    main = script.load()
    monkeypatch.setitem(sys.modules, "typer", None)
    monkeypatch.delitem(sys.modules, "bilkatogo.cli")

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    message = capsys.readouterr().err.strip()
    assert "bilkatogo[cli]" in message
    assert "\n" not in message


@pytest.fixture
def run_piped(bilka: FakeBilka, config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: async_client(transport=bilka.transport, **kwargs)
    )
    monkeypatch.setenv("BILKATOGO_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("BILKATOGO_EMAIL", raising=False)
    monkeypatch.delenv("BILKATOGO_PASSWORD", raising=False)
    [script] = entry_points(group="console_scripts", name="bilkatogo")
    main = script.load()

    def run(*args: str) -> tuple[bytes, bytes]:
        stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        monkeypatch.setattr(sys, "argv", ["bilkatogo", *args])
        monkeypatch.setattr(sys, "stdout", stdout)
        monkeypatch.setattr(sys, "stderr", stderr)
        with pytest.raises(SystemExit):
            main()
        stdout.flush()
        stderr.flush()
        return stdout.buffer.getvalue(), stderr.buffer.getvalue()

    return run


def test_piped_search_results_are_utf8(run_piped) -> None:
    stdout, _ = run_piped("search", "skyr", "--email", EMAIL, "--password", PASSWORD)

    assert "Skyr m. vanilje 0,2 % fedt øko".encode() in stdout


def test_piped_json_parses_back_as_utf8(run_piped) -> None:
    stdout, _ = run_piped("search", "skyr", "--json", "--email", EMAIL, "--password", PASSWORD)

    names = [product["name"] for product in json.loads(stdout.decode("utf-8"))]
    assert "Skyr m. vanilje 0,2 % fedt øko" in names


def test_piped_errors_are_utf8(run_piped) -> None:
    _, stderr = run_piped("search", "skyr", "--limit", "smør")

    assert "smør".encode() in stderr


PICKUP_CAPTURED = "2026-11-08T20:00:00+01:00"
HOME_CAPTURED = "2026-11-02T08:00:00+01:00"


def test_pickup_slots_prints_a_table_and_the_next_open_slot(
    invoke, logged_in_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PICKUP_CAPTURED)
    # What the Cart held when the slots were captured.
    logged_in_cart.cart_contents["deliveryDate"].update(deliveryDate="2026-11-11")

    result = invoke("pickup-slots", "--days", "2", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[:2] == [
        "Pickup Point  Bilka Vejle",
        "Chosen        Wed 11/11 14:00-16:00",
    ]
    assert lines[2].split() == ["Date", "Start", "End", "Availability", "Order", "before", "Note"]
    assert lines[3].split() == ["Sun", "08/11", "No", "slots"]
    assert lines[4].split()[:9] == [
        "Mon",
        "09/11",
        "08:00",
        "10:00",
        "Sold",
        "out",
        "i",
        "morgen",
        "kl.",
    ]
    assert lines[4].rstrip().endswith("bemærk at tidspunktet kan blive fuldt booket forinden.")
    assert lines[5].split()[:5] == ["Mon", "09/11", "10:00", "12:00", "Open"]
    assert len(lines) == 3 + 1 + 6 + 1
    assert lines[-1] == "Next open: Mon 09/11 10:00-12:00"


def test_pickup_slots_for_home_delivery_without_open_slots(
    invoke, home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    result = invoke("pickup-slots", "--days", "2", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Pickup Point  Home Delivery"
    assert [line.split()[:5] for line in lines[3:5]] == [
        ["Mon", "02/11", "00:00", "06:00", "Closed"],
        ["Tue", "03/11", "No", "slots", "udsolgt"],
    ]
    assert lines[-1] == "No open Pickup Slots in the next 2 days."
    for personal in ("Kunde Eksempel", "Eksempelby"):
        assert personal not in result.output


def test_pickup_slots_json(
    invoke, home_delivery_cart: FakeBilka, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(HOME_CAPTURED)

    result = invoke("pickup-slots", "--json", "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    slots = json.loads(result.output)
    assert slots["slots"][1] == {
        "slot": {"date": "2026-11-04", "start": "00:00:00", "end": "06:00:00"},
        "availability": "open",
        "order_before": "i morgen kl. 15:00",
        "note": (
            "Levering vil ske natten til onsdag. Vi kommer med dine varer imellem 20:00 og 06:00. "
            "For at benytte det valgte tidsrum, skal du bestille senest i morgen kl. 15:00, "
            "bemærk at tidspunktet kan blive fuldt booket forinden."
        ),
    }
    assert slots["days_without_slots"] == {"2026-11-03": "udsolgt"}
    assert slots["next_open"] == slots["slots"][1]
    assert slots["chosen"] == {"date": "2026-11-11", "start": "15:00:00", "end": "22:00:00"}
    assert (slots["pickup_point"], slots["home_delivery"]) == (None, True)


def test_recipes_shows_this_weeks_madplan_without_logging_in(invoke, bilka: FakeBilka) -> None:
    result = invoke("recipes")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Madplan til uge 46"
    assert lines[1].split() == ["Day", "Recipe", "Path"]
    assert lines[2].split() == [
        "Mon",
        "Cowboy",
        "toast",
        "aftensmad/sandwiches-pita-og-wraps/sandwiches/cowboy-toast",
    ]
    assert lines[8].split()[:2] == ["Sun", "Minestronesuppe"]
    assert bilka.calls("gigya_login") == []


def test_recipes_list_shows_a_category(invoke, bilka: FakeBilka) -> None:
    result = invoke("recipes", "list", "aftensmad/danske-retter", "--limit", "3")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["Recipe", "Group", "Path"]
    assert lines[3].split() == [
        "Karbonader",
        "Dansk",
        "inspirerede",
        "retter",
        "aftensmad/danske-retter/karbonader",
    ]
    assert len(lines) == 4


def test_recipes_list_of_an_unknown_category_names_the_real_ones(invoke) -> None:
    result = invoke("recipes", "list", "pandekager")

    assert result.exit_code == 1
    assert "No recipe category 'pandekager'" in result.output
    assert "aftensmad, frokost" in result.output


def test_recipes_json(invoke) -> None:
    result = invoke("recipes", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[1] == {
        "id": "aftensmad/pastaretter/carbonara",
        "name": "Carbonara",
        "url": "https://www.bilkatogo.dk/inspiration/opskrifter/aftensmad/pastaretter/carbonara/",
        "group": "Madplan til uge 46",
    }


CARBONARA = "aftensmad/pastaretter/carbonara"


@pytest.fixture
def carbonara_sold(logged_in_cart: FakeBilka) -> FakeBilka:
    """Two of the carbonara's Products sold at the Cart's Store: Bananer and Skyr renamed."""
    for hit, number in zip(logged_in_cart.products[1:3], ["81422", "128254"], strict=True):
        hit["objectID"] = number
    return logged_in_cart


def test_recipes_show_prints_the_recipe_and_its_products_at_the_store(
    invoke, carbonara_sold: FakeBilka
) -> None:
    result = invoke("recipes", "show", CARBONARA, "--email", EMAIL, "--password", PASSWORD)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[:4] == [
        "Recipe   Carbonara",
        f"Link     https://www.bilkatogo.dk/inspiration/opskrifter/{CARBONARA}/",
        "Persons  4",
        "Time     20 min",
    ]
    assert "Ingredients" in lines
    assert "Fremgangsmåde" in lines
    products = lines[lines.index("") + 1 :]
    assert products[0].split()[:2] == ["Article", "Product"]
    assert [line.split()[0] for line in products[1:3]] == ["128254", "81422"]
    assert products[-1] == "Not sold at your Store: 166050, 125823, 170956, 16868, 93861"


def test_recipes_add_asks_before_adding_the_products_the_store_sells(
    invoke, carbonara_sold: FakeBilka
) -> None:
    result = invoke(
        "recipes", "add", CARBONARA, "--email", EMAIL, "--password", PASSWORD, input="y\n"
    )

    assert result.exit_code == 0, result.output
    assert "Add 2 Products to the Cart? [y/N]" in result.output
    added = [
        request.url.params["productId"] for request in carbonara_sold.calls("change_line_count")
    ]
    assert added == ["128254", "81422"]
    assert "applied" in result.output


def test_recipes_add_answered_no_changes_nothing(invoke, carbonara_sold: FakeBilka) -> None:
    result = invoke(
        "recipes", "add", CARBONARA, "--email", EMAIL, "--password", PASSWORD, input="n\n"
    )

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "Nothing added."
    assert carbonara_sold.calls("change_line_count") == []


def test_recipes_add_with_yes_does_not_ask(invoke, carbonara_sold: FakeBilka) -> None:
    result = invoke(
        "recipes", "add", CARBONARA, "--yes", "--json", "--email", EMAIL, "--password", PASSWORD
    )

    assert result.exit_code == 0, result.output
    changes = json.loads(result.output)["changes"]
    assert [(change["article_number"], change["status"]) for change in changes] == [
        ("128254", "applied"),
        ("81422", "applied"),
    ]


def test_recipes_add_json_needs_yes(invoke, bilka: FakeBilka) -> None:
    result = invoke("recipes", "add", CARBONARA, "--json")

    assert result.exit_code == 1
    assert "--json needs --yes" in result.output
    assert bilka.requests == []
