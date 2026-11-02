import asyncio
import functools
import io
import json
import sys
from collections.abc import Awaitable, Callable, Coroutine
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

import httpx
import typer

from . import config
from .client import BilkaToGoClient
from .const import SEARCH_LIMIT
from .exceptions import (
    BilkaToGoError,
    BilkaUnreachableError,
    InvalidCredentialsError,
    UnexpectedResponseError,
)
from .models import (
    BilkaDataClass,
    Cart,
    CartChangeStatus,
    Offer,
    Order,
    OrderDetails,
    OrderStatus,
    PickupPoint,
    PickupSlot,
    Product,
    SearchSort,
)
from .redact import redact as redact_personal_data
from .token_storage import FileTokenStorage

app = typer.Typer(no_args_is_help=True, help="Track your BilkaToGo orders.")

EmailOption = Annotated[
    str | None, typer.Option("--email", envvar="BILKATOGO_EMAIL", help="BilkaToGo account email.")
]
PasswordOption = Annotated[
    str | None,
    typer.Option("--password", envvar="BILKATOGO_PASSWORD", help="BilkaToGo account password."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Output JSON.")]
RawOption = Annotated[bool, typer.Option("--raw", help="Print Bilka's JSON as received.")]
RedactOption = Annotated[
    bool,
    typer.Option("--redact", help="With --raw: replace personal data with placeholders."),
]


def run_async[**P, R](func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, R]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        return asyncio.run(func(*args, **kwargs), loop_factory=loop_factory)

    return wrapper


def known_credentials(email: str | None, password: str | None) -> tuple[str | None, str | None]:
    saved = config.load_config()
    return email or saved.get("email"), password or saved.get("password")


def resolve_credentials(email: str | None, password: str | None) -> tuple[str, str]:
    email, password = known_credentials(email, password)
    return email or typer.prompt("Email"), password or prompt_password()


def prompt_password() -> str:
    return typer.prompt("Password", hide_input=True)


def to_json(data: Any) -> str:
    def default(obj: Any) -> Any:
        if isinstance(obj, BilkaDataClass):
            return dict(obj)
        if isinstance(obj, datetime | date | time):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError(f"Cannot serialise {type(obj).__name__}")

    return json.dumps(data, default=default, ensure_ascii=False)


def check_redact(raw: bool, redact: bool) -> None:
    if redact and not raw:
        raise fail("--redact only works with --raw.")


def print_raw(data: dict[str, Any] | None, redact: bool) -> None:
    if redact:
        data = redact_personal_data(data)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(cell) for cell in column) for column in zip(headers, *rows, strict=True)]
    for row in [headers, *rows]:
        typer.echo("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))


def http_client(ctx: typer.Context) -> httpx.AsyncClient | None:
    factory = ctx.obj
    return factory() if callable(factory) else None


def fail(message: str) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=1)


def failure(action: str, err: BilkaToGoError) -> typer.Exit:
    if isinstance(err, InvalidCredentialsError):
        return fail("Login failed: Wrong email or password.")
    if isinstance(err, BilkaUnreachableError):
        return fail(f"{action}: Could not reach Bilka. Check your connection and try again.")
    if isinstance(err, UnexpectedResponseError):
        return fail(f"{action}: Bilka answered unexpectedly ({err}). Try again later.")
    return fail(f"{action}: {err}")


@app.callback()
def main_callback() -> None:
    pass


@app.command()
@run_async
async def login(
    ctx: typer.Context,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Log in and store the session for later commands."""
    email, password = resolve_credentials(email, password)
    storage = FileTokenStorage(config.session_file())
    http = http_client(ctx)
    try:
        async with BilkaToGoClient(
            email, password, token_storage=storage, httpx_client=http
        ) as client:
            await client.login()
    except BilkaToGoError as err:
        raise failure("Login failed", err) from None
    finally:
        if http is not None:
            await http.aclose()

    if as_json:
        typer.echo(json.dumps({"email": email, "logged_in": True}))
    else:
        typer.echo(f"Logged in as {email}.")


async def with_session[T](
    ctx: typer.Context,
    email: str | None,
    password: str | None,
    fetch: Callable[[BilkaToGoClient], Awaitable[T]],
    what: str,
    *,
    action: str = "Could not fetch",
) -> T:
    storage = FileTokenStorage(config.session_file())
    email, password = known_credentials(email, password)
    if email is None:
        session = await storage.load()
        email = (session or {}).get("email") or typer.prompt("Email")
    http = http_client(ctx)
    try:
        try:
            async with BilkaToGoClient(
                email, password, token_storage=storage, httpx_client=http
            ) as client:
                return await fetch(client)
        except InvalidCredentialsError:
            if password is not None:
                raise
            async with BilkaToGoClient(
                email, prompt_password(), token_storage=storage, httpx_client=http
            ) as client:
                return await fetch(client)
    except BilkaToGoError as err:
        raise failure(f"{action} {what}", err) from None
    finally:
        if http is not None:
            await http.aclose()


LimitOption = Annotated[int, typer.Option("--limit", min=1, help="Number of orders to show.")]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@app.command()
@run_async
async def orders(
    ctx: typer.Context,
    limit: LimitOption = 5,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show your Orders with their Pickup Slot and Order Status."""
    found = await with_session(
        ctx, email, password, lambda client: client.get_orders(limit=limit), "orders"
    )
    if as_json:
        typer.echo(to_json(found))
    elif not found:
        typer.echo("No orders.")
    else:
        print_table(
            ["Order", "Ordered", "Pickup Slot", "Pickup Point", "Status"],
            [order_row(order) for order in found],
        )


def order_row(order: Order) -> list[str]:
    return [
        order.order_number,
        order.ordered_at.strftime("%Y-%m-%d %H:%M") if order.ordered_at else "",
        format_slot(order.pickup_slot),
        format_point(order.pickup_point, order.home_delivery),
        format_status(order.status),
    ]


def format_slot(slot: PickupSlot | None) -> str:
    if slot is None:
        return ""
    return f"{WEEKDAYS[slot.date.weekday()]} {slot.date:%d/%m} {slot.start:%H:%M}-{slot.end:%H:%M}"


def format_point(point: PickupPoint | None, home_delivery: bool) -> str:
    return "Home Delivery" if home_delivery else (point.name if point else "")


def format_amount(amount: Decimal | None) -> str:
    return "" if amount is None else f"{amount:.2f} kr"


def format_status(status: OrderStatus) -> str:
    return status.name.replace("_", " ").title()


OrderNumberArgument = Annotated[
    str | None,
    typer.Argument(help="Order Number, e.g. 900000000001. Defaults to the Current Order."),
]


@app.command()
@run_async
async def order(
    ctx: typer.Context,
    order_number: OrderNumberArgument = None,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
    raw: RawOption = False,
    redact: RedactOption = False,
) -> None:
    """Show an Order (default: the Current Order), its Order Total and what packing changed."""
    check_redact(raw, redact)
    fetch_raw: Callable[[BilkaToGoClient], Awaitable[dict[str, Any] | None]]
    fetch: Callable[[BilkaToGoClient], Awaitable[OrderDetails | None]]
    if order_number is None:
        what = "the current order"
        fetch_raw = BilkaToGoClient.get_current_order_json
        fetch = BilkaToGoClient.get_current_order
    else:
        what = f"order {order_number}"
        fetch_raw = functools.partial(BilkaToGoClient.get_order_json, order=order_number)
        fetch = functools.partial(BilkaToGoClient.get_order, order=order_number)
    if raw:
        print_raw(await with_session(ctx, email, password, fetch_raw, what), redact)
        return
    details = await with_session(ctx, email, password, fetch, what)
    if as_json:
        typer.echo(to_json(details))
        return
    if details is None:
        typer.echo("No orders.")
        return
    fields = [
        ("Order", details.order_number),
        ("Status", format_status(details.status)),
        ("Pickup Slot", format_slot(details.pickup_slot)),
        ("Pickup Point", format_point(details.pickup_point, details.home_delivery)),
        ("Pickup Code", details.pickup_code.code if details.pickup_code else ""),
        ("Ordered Total", format_amount(details.ordered_total)),
        ("Order Total", format_amount(details.order_total)),
        ("Deposit", format_amount(details.deposit)),
        ("Service Fee", format_amount(details.service_fee)),
        ("Packaging", format_amount(details.packaging)),
        ("Delivery Fee", format_amount(details.delivery_fee)),
        ("Discount", format_amount(details.discount)),
    ]
    for label, value in fields:
        typer.echo(f"{label:<13} {value}")
    print_list(
        "Substitutions",
        [
            f"{substitution.ordered_quantity} x {substitution.original_product} -> "
            f"{substitution.quantity} x {substitution.replacement_product}"
            for substitution in details.substitutions
        ],
    )
    print_list(
        "Missing Items",
        [
            f"{item.product}: {item.missing_quantity} of {item.ordered_quantity} missing"
            for item in details.missing_items
        ],
    )


def print_list(label: str, entries: list[str]) -> None:
    if not entries:
        typer.echo(f"{label} none")
        return
    typer.echo(label)
    for entry in entries:
        typer.echo(f"  {entry}")


@app.command("pickup-points")
@run_async
async def pickup_points(
    ctx: typer.Context,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show every Pickup Point with its Store, marking the one chosen in your Cart."""

    async def fetch(client: BilkaToGoClient) -> tuple[list[PickupPoint], PickupPoint | None]:
        return await client.get_pickup_points(), await client.get_cart_pickup_point()

    points, chosen = await with_session(ctx, email, password, fetch, "pickup points")
    chosen_name = chosen.name if chosen else None
    if as_json:
        typer.echo(
            to_json([{**dict(point), "chosen": point.name == chosen_name} for point in points])
        )
        return
    print_table(
        ["", "Pickup Point", "Address", "Store"],
        [
            [
                "*" if point.name == chosen_name else "",
                point.name,
                f"{point.address}, {point.zip_code} {point.city}",
                point.store_id or "",
            ]
            for point in points
        ],
    )


SearchTextArgument = Annotated[str, typer.Argument(help="What to search for, e.g. skyr.")]
PickupPointOption = Annotated[
    str | None,
    typer.Option(
        "--pickup-point",
        help="Search at this Pickup Point's Store. Defaults to the Pickup Point in your Cart.",
    ),
]
SearchLimitOption = Annotated[
    int, typer.Option("--limit", min=1, help="Number of Products to show.")
]
InStockOption = Annotated[
    bool, typer.Option("--in-stock", help="Only Products in stock at the Store.")
]
OnOfferOption = Annotated[
    bool,
    typer.Option("--on-offer", help="Only Products with an Offer or Multibuy Offer at the Store."),
]
InLeafletOption = Annotated[
    bool, typer.Option("--in-leaflet", help="Only Products in this week's Leaflet.")
]
SortOption = Annotated[
    SearchSort,
    typer.Option(
        "--sort",
        help="Order the --limit best matches by Price or Unit Price, counting Offers.",
    ),
]


@app.command()
@run_async
async def search(
    ctx: typer.Context,
    text: SearchTextArgument,
    pickup_point: PickupPointOption = None,
    limit: SearchLimitOption = SEARCH_LIMIT,
    in_stock: InStockOption = False,
    on_offer: OnOfferOption = False,
    in_leaflet: InLeafletOption = False,
    sort: SortOption = SearchSort.RELEVANCE,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Search Products with their Price, stock and Offer at a Store."""
    found = await with_session(
        ctx,
        email,
        password,
        lambda client: client.search(
            text,
            pickup_point=pickup_point,
            limit=limit,
            in_stock=in_stock,
            on_offer=on_offer,
            in_leaflet=in_leaflet,
            sort=sort,
        ),
        "products",
    )
    if as_json:
        typer.echo(to_json(found))
    elif not found:
        typer.echo("No products.")
    else:
        print_table(
            ["Article", "Product", "Brand", "Price", "Unit Price", "Stock", "Offer"],
            [product_row(product) for product in found],
        )


def product_row(product: Product) -> list[str]:
    unit_price = f"{format_amount(product.unit_price)}/{product.unit}" if product.unit else ""
    return [
        product.article_number,
        product.name,
        product.brand,
        format_amount(product.price),
        unit_price,
        "In stock" if product.in_stock else "Out of stock",
        format_offer(product.offer) if product.offer else product.multibuy_offer or "",
    ]


def format_offer(offer: Offer) -> str:
    text = f"{offer.label}: {format_amount(offer.price)}"
    if offer.ends:
        text += f" until {offer.ends.isoformat()}"
    if offer.limit_per_customer:
        text += f", max {offer.limit_per_customer} per customer"
    return text


cart_app = typer.Typer(help="Show or change your Cart.")
app.add_typer(cart_app, name="cart")


@cart_app.callback(invoke_without_command=True)
@run_async
async def cart(
    ctx: typer.Context,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
    raw: RawOption = False,
    redact: RedactOption = False,
) -> None:
    """Show your Cart: its Cart Lines, the Cart total and your Pickup Point."""
    if ctx.invoked_subcommand is not None:
        check_no_cart_output_options(ctx.invoked_subcommand, as_json, raw, redact)
        return
    check_redact(raw, redact)
    if raw:
        data = await with_session(
            ctx, email, password, lambda client: client.get_cart_json(), "the cart"
        )
        print_raw(data, redact)
        return
    found = await with_session(ctx, email, password, lambda client: client.get_cart(), "the cart")
    if as_json:
        typer.echo(to_json(found))
        return
    print_cart(found)


def check_no_cart_output_options(subcommand: str, as_json: bool, raw: bool, redact: bool) -> None:
    for option, given in (("--raw", raw), ("--redact", redact)):
        if given:
            raise fail(f"{option} only works without a subcommand, not with cart {subcommand}.")
    if as_json:
        raise fail(f"Put --json after the subcommand: cart {subcommand} --json.")


ArticleArgument = Annotated[str, typer.Argument(help="Article Number, e.g. 18381.")]


@cart_app.command("add")
@run_async
async def cart_add(
    ctx: typer.Context,
    article_number: ArticleArgument,
    quantity: Annotated[int, typer.Option("--quantity", min=1, help="How many to add.")] = 1,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Add a Product to your Cart."""
    await change_cart(
        ctx,
        email,
        password,
        as_json,
        lambda client: client.add_to_cart(article_number, quantity),
    )


@cart_app.command("add-items")
@run_async
async def cart_add_items(
    ctx: typer.Context,
    items: Annotated[
        list[str],
        typer.Argument(
            metavar="ARTICLE[:QUANTITY]...",
            help="Article Numbers, each with how many to add, e.g. 18381:10 19753.",
        ),
    ],
    quantity: Annotated[
        int, typer.Option("--quantity", min=1, help="How many to add where none is given.")
    ] = 1,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Add several Products to your Cart, one after another."""
    wanted = parse_items(items, quantity)
    result = await with_session(
        ctx,
        email,
        password,
        lambda client: client.add_items_to_cart(wanted),
        "the cart",
        action="Could not change",
    )
    if as_json:
        typer.echo(to_json(result))
    else:
        print_table(
            ["Article", "Quantity", "Result", "Message"],
            [
                [
                    change.article_number,
                    str(change.requested),
                    change.status.value,
                    change.message or "",
                ]
                for change in result.changes
            ],
        )
        typer.echo()
        print_cart(result.cart)
    if any(change.status is not CartChangeStatus.APPLIED for change in result.changes):
        raise typer.Exit(1)


def parse_items(items: list[str], default_quantity: int) -> dict[str, int]:
    wanted: dict[str, int] = {}
    for item in items:
        article_number, _, count = item.partition(":")
        try:
            quantity = int(count) if count else default_quantity
        except ValueError:
            raise fail(f"{item!r} is not ARTICLE or ARTICLE:QUANTITY.") from None
        if not article_number or quantity < 1:
            raise fail(f"{item!r} is not ARTICLE or ARTICLE:QUANTITY with a quantity of 1 or more.")
        wanted[article_number] = wanted.get(article_number, 0) + quantity
    return wanted


@cart_app.command("set")
@run_async
async def cart_set(
    ctx: typer.Context,
    article_number: ArticleArgument,
    quantity: Annotated[int, typer.Argument(min=0, help="The new quantity; 0 removes it.")],
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Set the quantity of a Product in your Cart."""
    await change_cart(
        ctx,
        email,
        password,
        as_json,
        lambda client: client.set_cart_quantity(article_number, quantity),
    )


@cart_app.command("remove")
@run_async
async def cart_remove(
    ctx: typer.Context,
    article_number: ArticleArgument,
    quantity: Annotated[
        int | None,
        typer.Option("--quantity", min=1, help="How many to remove; leave out to remove them all."),
    ] = None,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Remove a Product, or some of it, from your Cart."""
    await change_cart(
        ctx,
        email,
        password,
        as_json,
        lambda client: client.remove_from_cart(article_number, quantity),
    )


async def change_cart(
    ctx: typer.Context,
    email: str | None,
    password: str | None,
    as_json: bool,
    change: Callable[[BilkaToGoClient], Awaitable[Cart]],
) -> None:
    found = await with_session(ctx, email, password, change, "the cart", action="Could not change")
    if as_json:
        typer.echo(to_json(found))
        return
    print_cart(found)
    for warning in found.warnings:
        typer.echo(f"Warning from Bilka: {warning}")


def print_cart(found: Cart) -> None:
    if found.lines:
        print_table(
            ["Article", "Product", "Quantity", "Price", "Line Total"],
            [
                [
                    line.article_number,
                    line.product,
                    str(line.quantity),
                    format_amount(line.price),
                    format_amount(line.line_total),
                ]
                for line in found.lines
            ],
        )
    else:
        typer.echo("No Cart Lines.")
    fields = [
        ("Pickup Point", found.pickup_point.name if found.pickup_point else ""),
        ("Cart Total", format_amount(found.total)),
        ("Deposit", format_amount(found.deposit)),
        ("Service Fee", format_amount(found.service_fee)),
        ("Packaging", format_amount(found.packaging)),
        ("Discount", format_amount(found.discount)),
    ]
    for label, value in fields:
        typer.echo(f"{label:<12} {value}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors=stream.errors)
    app()
