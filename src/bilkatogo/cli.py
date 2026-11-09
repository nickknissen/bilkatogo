import asyncio
import functools
import io
import json
import sys
from collections.abc import Awaitable, Callable, Coroutine
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import typer

from . import config
from .client import BilkaToGoClient
from .const import CHOOSABLE_SLOT_DAYS, SEARCH_LIMIT
from .exceptions import (
    BilkaToGoError,
    BilkaUnreachableError,
    InvalidCredentialsError,
    UnexpectedResponseError,
)
from .models import (
    BilkaDataClass,
    Cart,
    CartChanges,
    CartChangeStatus,
    ChosenPickupSlot,
    DeliveryChange,
    Offer,
    Order,
    OrderDetails,
    OrderStatus,
    PickupPoint,
    PickupSlot,
    Product,
    RecipeDetails,
    SearchSort,
)
from .redact import redact as redact_personal_data
from .token_storage import FileTokenStorage

if TYPE_CHECKING:
    from .recipes import RecipeReader

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
            return {key: text_keys(value) for key, value in obj}
        if isinstance(obj, datetime | date | time):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError(f"Cannot serialise {type(obj).__name__}")

    return json.dumps(text_keys(data), default=default, ensure_ascii=False)


def text_keys(value: Any) -> Any:
    """JSON keys must be text; a date key, as in `days_without_slots`, becomes an ISO date."""
    if not isinstance(value, dict):
        return value
    return {
        key.isoformat() if isinstance(key, date) else key: text_keys(item)
        for key, item in value.items()
    }


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
    saved_email = ((await storage.load()) or {}).get("email")
    if email is None:
        email = saved_email or typer.prompt("Email")
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
            has_saved_session = email == saved_email
            if has_saved_session:
                # It can no longer be renewed without the password.
                raise fail("Session expired. Run `bilkatogo login`.") from None
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


def sized(product: str, size: str) -> str:
    return f"{product} {size}" if size else product


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
            f"{substitution.ordered_quantity} x "
            f"{sized(substitution.original_product, substitution.original_size)} -> "
            f"{substitution.quantity} x "
            f"{sized(substitution.replacement_product, substitution.replacement_size)}"
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


pickup_points_app = typer.Typer(
    help="Show the Pickup Points, or move your Cart to one or to Home Delivery."
)
app.add_typer(pickup_points_app, name="pickup-points")


@pickup_points_app.callback(invoke_without_command=True)
@run_async
async def pickup_points(
    ctx: typer.Context,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show every Pickup Point with its Store, marking the one chosen in your Cart. Move your
    Cart with `pickup-points choose`."""
    if ctx.invoked_subcommand is not None:
        check_no_group_options(f"pickup-points {ctx.invoked_subcommand}", as_json, [])
        return

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


@pickup_points_app.command("choose")
@run_async
async def pickup_points_choose(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(metavar="NAME", help='The Pickup Point, e.g. "Bilka Odense".'),
    ] = None,
    home_delivery: Annotated[
        bool,
        typer.Option(
            "--home-delivery",
            help="Move to Home Delivery at your Home Address. Choose it once on bilkatogo.dk "
            "first, so that it is known.",
        ),
    ] = False,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Move your Cart to a Pickup Point or to Home Delivery, and show what that changed: its
    Store, total and fees, Cart Lines Bilka can't supply there, and whether your Pickup Slot
    is still open."""
    if name is not None and home_delivery:
        raise fail("Name a Pickup Point or give --home-delivery, not both.")
    if name is None and not home_delivery:
        raise fail("Name a Pickup Point or give --home-delivery.")

    async def choose(client: BilkaToGoClient) -> DeliveryChange:
        if name is None:
            return await client.choose_home_delivery()
        return await client.choose_pickup_point(name)

    what = "Home Delivery" if home_delivery else "the pickup point"
    change = await with_session(ctx, email, password, choose, what, action="Could not choose")
    if as_json:
        typer.echo(to_json(change))
        return
    print_delivery_change(change)


CHANGED_AMOUNTS: list[tuple[str, Callable[[Cart], Decimal]]] = [
    ("Cart Total", lambda cart: cart.total),
    ("Service Fee", lambda cart: cart.service_fee),
    ("Packaging", lambda cart: cart.packaging),
    ("Delivery Fee", lambda cart: cart.delivery_fee),
]


def print_delivery_change(change: DeliveryChange) -> None:
    """The move as before -> after, then warnings for what needs fixing before ordering. Never
    the Home Address: the Cart has only "Home Delivery" for it."""
    before, after = change.before, change.after
    place = format_point(after.pickup_point, after.home_delivery)
    if not change.changed:
        typer.echo(f"Your Cart is {'on' if after.home_delivery else 'at'} {place} already.")
        return
    fields = [
        ("Moved", f"{format_point(before.pickup_point, before.home_delivery)} -> {place}"),
        ("Store", f"{before.store_id or ''} -> {after.store_id or ''}"),
    ] + [
        (label, f"{format_amount(amount(before))} -> {format_amount(amount(after))}")
        for label, amount in CHANGED_AMOUNTS
    ]
    if after.minimum_order is not None:
        fields.append(("Minimum Order", format_amount(after.minimum_order)))
    for label, value in fields:
        typer.echo(f"{label:<14} {value}")
    for line in change.unavailable:
        typer.echo(
            f"Warning: {line.product} ({line.article_number}) is unavailable here: "
            f"{line.unavailable_reason}"
        )
    for line in change.gone:
        typer.echo(f"Warning: {line.product} ({line.article_number}) is no longer in your Cart.")
    if change.available_again:
        again = ", ".join(
            f"{line.product} ({line.article_number})" for line in change.available_again
        )
        typer.echo(f"Available again: {again}")
    if change.pickup_slot_closed:
        typer.echo(
            f"Warning: Your Pickup Slot {format_slot(after.pickup_slot)} is closed here; "
            "choose another with `bilkatogo pickup-slots choose`."
        )


DaysOption = Annotated[
    int | None,
    typer.Option("--days", min=1, help="Number of days to show, today included; 7 by default."),
]
DEFAULT_SLOT_DAYS = 7

pickup_slots_app = typer.Typer(
    help="Show the coming days' Pickup Slots for your Cart, or choose one."
)
app.add_typer(pickup_slots_app, name="pickup-slots")


@pickup_slots_app.callback(invoke_without_command=True)
@run_async
async def pickup_slots(
    ctx: typer.Context,
    days: DaysOption = None,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
    raw: RawOption = False,
    redact: RedactOption = False,
) -> None:
    """Show the coming days' Pickup Slots for your Cart and whether they are open. Choose one
    with `pickup-slots choose`."""
    if ctx.invoked_subcommand is not None:
        check_no_group_options(
            f"pickup-slots {ctx.invoked_subcommand}",
            as_json,
            [("--days", days is not None), ("--raw", raw), ("--redact", redact)],
        )
        return
    days = days or DEFAULT_SLOT_DAYS
    check_redact(raw, redact)
    if raw:
        data = await with_session(
            ctx, email, password, BilkaToGoClient.get_pickup_slots_json, "pickup slots"
        )
        print_raw(data, redact)
        return
    found = await with_session(
        ctx, email, password, lambda client: client.get_pickup_slots(days=days), "pickup slots"
    )
    if as_json:
        typer.echo(to_json({**dict(found), "next_open": found.next_open}))
        return
    typer.echo(f"{'Pickup Point':<13} {format_point(found.pickup_point, found.home_delivery)}")
    typer.echo(f"{'Chosen':<13} {format_slot(found.chosen)}")
    rows = [
        (
            slot.slot.date,
            slot.slot.start,
            [
                format_day(slot.slot.date),
                f"{slot.slot.start:%H:%M}",
                f"{slot.slot.end:%H:%M}",
                slot.availability.value.replace("_", " ").capitalize(),
                slot.order_before or "",
                slot.note or "",
            ],
        )
        for slot in found.slots
    ] + [
        (day, time.min, [format_day(day), "", "", "No slots", "", reason])
        for day, reason in found.days_without_slots.items()
    ]
    print_table(
        ["Date", "Start", "End", "Availability", "Order before", "Note"],
        [row for _, _, row in sorted(rows, key=lambda row: row[:2])],
    )
    next_open = found.next_open
    if next_open is None:
        typer.echo(f"No open Pickup Slots in the next {days} days.")
    else:
        typer.echo(f"Next open: {format_slot(next_open.slot)}")


def format_day(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d/%m}"


@pickup_slots_app.command("choose")
@run_async
async def pickup_slots_choose(
    ctx: typer.Context,
    day: Annotated[str, typer.Argument(metavar="DATE", help="The slot's date, e.g. 2026-11-12.")],
    start: Annotated[str, typer.Argument(metavar="START", help="The slot's start, e.g. 16:00.")],
    end: Annotated[
        str | None,
        typer.Option("--end", help="The slot's end, when two slots start at the same time."),
    ] = None,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Choose your Cart's Pickup Slot among the open ones. It holds nothing: the slot can still
    sell out until you order on bilkatogo.dk."""
    on = parse_date(day)
    starts = parse_time(start)
    ends = parse_time(end) if end is not None else None

    async def choose(client: BilkaToGoClient) -> ChosenPickupSlot:
        if ends is not None:
            return await client.choose_pickup_slot(PickupSlot(on, starts, ends))
        slots = await client.get_pickup_slots(days=CHOOSABLE_SLOT_DAYS)
        matches = slots.starting_at(on, starts)
        if not matches:
            raise fail(f"No Pickup Slot on {format_day(on)} starts at {starts:%H:%M}.")
        if len(matches) > 1:
            raise fail(
                f"Several Pickup Slots on {format_day(on)} start at {starts:%H:%M}; "
                "give the end with --end."
            )
        return await client.choose_pickup_slot(matches[0].slot)

    result = await with_session(
        ctx, email, password, choose, "the pickup slot", action="Could not choose"
    )
    if as_json:
        typer.echo(to_json(result))
    elif not result.changed:
        typer.echo(f"{'Chosen':<9} {format_slot(result.chosen)} (already chosen)")
    else:
        typer.echo(f"{'Chosen':<9} {format_slot(result.chosen)}")
        typer.echo(f"{'Replaced':<9} {format_slot(result.replaced) or 'none'}")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise fail(f"{value!r} is not a date like 2026-11-12.") from None


def parse_time(value: str) -> time:
    hours, _, minutes = value.partition(":")
    try:
        return time(int(hours), int(minutes))
    except ValueError:
        raise fail(f"{value!r} is not a time like 16:00.") from None


SearchTextArgument = Annotated[str, typer.Argument(help="What to search for, e.g. skyr.")]
PickupPointOption = Annotated[
    str | None,
    typer.Option(
        "--pickup-point",
        help="Use this Pickup Point's Store. Defaults to the Store that serves your Cart.",
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
    print_products(found, as_json)


@app.command()
@run_async
async def products(
    ctx: typer.Context,
    article_numbers: Annotated[
        list[str], typer.Argument(metavar="ARTICLE...", help="Article Numbers, e.g. 18381 19753.")
    ],
    pickup_point: PickupPointOption = None,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show Products by Article Number with their Price, stock and Offer at a Store."""
    found = await with_session(
        ctx,
        email,
        password,
        lambda client: client.get_products(article_numbers, pickup_point=pickup_point),
        "products",
    )
    print_products(found, as_json)


def print_products(found: list[Product], as_json: bool) -> None:
    if as_json:
        typer.echo(to_json(found))
    elif not found:
        typer.echo("No products.")
    else:
        print_table(
            ["Article", "Product", "Size", "Brand", "Price", "Unit Price", "Stock", "Offer"],
            [product_row(product) for product in found],
        )


def product_row(product: Product) -> list[str]:
    unit_price = f"{format_amount(product.unit_price)}/{product.unit}" if product.unit else ""
    stock = "In stock" if product.in_stock else "Out of stock"
    return [
        product.article_number,
        product.name,
        product.size or "",
        product.brand,
        format_amount(product.price),
        unit_price,
        stock if product.deliverable else f"{stock}, not delivered here",
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
    """Show your Cart: its Cart Lines, the Cart total, Pickup Point or Home Delivery and fees."""
    if ctx.invoked_subcommand is not None:
        check_no_group_options(
            f"cart {ctx.invoked_subcommand}", as_json, [("--raw", raw), ("--redact", redact)]
        )
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


def check_no_group_options(command: str, as_json: bool, options: list[tuple[str, bool]]) -> None:
    """Refuse a group's own options, e.g. `cart --raw add`, which only apply without a
    subcommand; `command` is the group and subcommand, e.g. "cart add"."""
    for option, given in options:
        if given:
            raise fail(f"{option} only works without a subcommand, not with {command}.")
    if as_json:
        raise fail(f"Put --json after the subcommand: {command} --json.")


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
    print_changes(result, as_json)


def print_changes(result: CartChanges, as_json: bool) -> None:
    """A line per Product with its result, then the Cart; exits 1 when any was not added."""
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
        ("Pickup Point", format_point(found.pickup_point, found.home_delivery)),
        ("Pickup Slot", format_slot(found.pickup_slot)),
        (
            "Next Available",
            found.next_available_date.isoformat() if found.next_available_date else "",
        ),
        ("Cart Total", format_amount(found.total)),
        ("Deposit", format_amount(found.deposit)),
        ("Service Fee", format_amount(found.service_fee)),
        ("Packaging", format_amount(found.packaging)),
        ("Delivery Fee", format_amount(found.delivery_fee)),
        ("Discount", format_amount(found.discount)),
    ]
    if found.minimum_order is not None:
        minimum = format_amount(found.minimum_order)
        if found.minimum_left > 0:
            minimum += f" ({format_amount(found.minimum_left)} left)"
        fields.append(("Minimum Order", minimum))
    for label, value in fields:
        typer.echo(f"{label:<14} {value}")
    for warning in found.warnings:
        typer.echo(f"Warning from Bilka: {warning}")


recipes_app = typer.Typer(help="Bilka's Recipes and this week's Madplan from bilkatogo.dk.")
app.add_typer(recipes_app, name="recipes")

RecipeArgument = Annotated[
    str,
    typer.Argument(
        help="The Recipe's link or path, e.g. aftensmad/danske-retter/karbonader.",
    ),
]


async def read_recipes[T](
    ctx: typer.Context, read: Callable[["RecipeReader"], Awaitable[T]], what: str
) -> T:
    """Read public recipe pages, without logging in."""
    from .recipes import RecipeReader

    http = http_client(ctx)
    try:
        async with RecipeReader(httpx_client=http) as reader:
            return await read(reader)
    except BilkaToGoError as err:
        raise failure(f"Could not fetch {what}", err) from None
    finally:
        if http is not None:
            await http.aclose()


@recipes_app.callback(invoke_without_command=True)
@run_async
async def recipes(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """Show this week's Madplan: seven dinners, Monday to Sunday."""
    if ctx.invoked_subcommand is not None:
        if as_json:
            raise fail(f"Put --json after the subcommand: recipes {ctx.invoked_subcommand} --json.")
        return
    week = await read_recipes(ctx, lambda reader: reader.madplan(), "the Madplan")
    if as_json:
        typer.echo(to_json(week))
        return
    if week[0].group:
        typer.echo(week[0].group)
    # The website lists the Madplan's seven dinners from Monday to Sunday.
    days = WEEKDAYS if len(week) == len(WEEKDAYS) else [""] * len(week)
    print_table(
        ["Day", "Recipe", "Path"],
        [[day, recipe.name, recipe.id] for day, recipe in zip(days, week, strict=True)],
    )


@recipes_app.command("list")
@run_async
async def recipes_list(
    ctx: typer.Context,
    category: Annotated[
        str, typer.Argument(help="A category's name or path, e.g. aftensmad or 'børn i køkkenet'.")
    ],
    limit: Annotated[int, typer.Option("--limit", min=1, help="Number of Recipes to show.")] = 20,
    as_json: JsonOption = False,
) -> None:
    """Show the Recipes in a category; one from each sub-category in turn."""
    found = await read_recipes(
        ctx, lambda reader: reader.category(category, limit=limit), "the recipes"
    )
    if as_json:
        typer.echo(to_json(found))
    elif not found:
        typer.echo("No recipes.")
    else:
        print_table(
            ["Recipe", "Group", "Path"],
            [[recipe.name, recipe.group or "", recipe.id] for recipe in found],
        )


@recipes_app.command("show")
@run_async
async def recipes_show(
    ctx: typer.Context,
    recipe: RecipeArgument,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show a Recipe and its Recipe Products with Price and stock at your Cart's Store."""
    details = await with_session(
        ctx, email, password, lambda client: client.get_recipe(recipe), "the recipe"
    )
    if as_json:
        typer.echo(to_json(details))
        return
    print_recipe(details)


@recipes_app.command("add")
@run_async
async def recipes_add(
    ctx: typer.Context,
    recipe: RecipeArgument,
    yes: Annotated[bool, typer.Option("--yes", help="Add without asking.")] = False,
    email: EmailOption = None,
    password: PasswordOption = None,
    as_json: JsonOption = False,
) -> None:
    """Add one of each of a Recipe's Products your Store sells to your Cart, after asking."""
    if as_json and not yes:
        raise fail("--json needs --yes: recipes add asks before it changes the Cart.")
    details = await with_session(
        ctx, email, password, lambda client: client.get_recipe(recipe), "the recipe"
    )
    if not details.products:
        raise fail(f"Your Store sells none of the Products for {details.name}.")
    if not as_json:
        print_recipe_products(details)
        if not yes and not typer.confirm(
            f"Add {len(details.products)} Products to the Cart?", default=False
        ):
            typer.echo("Nothing added.")
            return
    items = {product.article_number: 1 for product in details.products}
    result = await with_session(
        ctx,
        email,
        password,
        lambda client: client.add_items_to_cart(items),
        "the cart",
        action="Could not change",
    )
    print_changes(result, as_json)


def print_recipe(details: RecipeDetails) -> None:
    fields = [
        ("Recipe", details.name),
        ("Link", details.url),
        ("Persons", str(details.persons) if details.persons else ""),
        ("Time", details.time or ""),
    ]
    for label, value in fields:
        typer.echo(f"{label:<8} {value}")
    print_list("Ingredients", details.ingredients)
    for section in details.method:
        print_list(section.title or "Method", section.steps)
    typer.echo()
    print_recipe_products(details)


def print_recipe_products(details: RecipeDetails) -> None:
    if details.products:
        print_products(details.products, as_json=False)
    else:
        typer.echo("Your Store sells none of the Products.")
    sold = {product.article_number for product in details.products}
    missing = [number for number in details.article_numbers if number not in sold]
    if missing:
        typer.echo(f"Not sold at your Store: {', '.join(missing)}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors=stream.errors)
    app()
