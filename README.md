# bilkatogo

Track your [BilkaToGo](https://www.bilkatogo.dk) grocery orders: an async Python library, a
command line tool and a Home Assistant integration that follow your order from the moment it is
placed until you pick it up, including which products Bilka substituted.

> **Unofficial.** This project is not affiliated with or endorsed by Bilka or Salling Group. It
> uses the same private web API as the bilkatogo.dk website, logging in with your email and
> password. That API can change or break at any time.

The library, the CLI and the Home Assistant integration are released together and always share
the same version.

- [Library](#library): an async Python client for your orders, Pickup Points and product search.
- [Command line](#command-line): the same from your terminal or a script.
- [Home Assistant](#home-assistant): sensors, a calendar, actions and events for your Current
  Order.

## Library

```sh
pip install bilkatogo
```

It depends on httpx, plus tzdata on Windows.

```python
import asyncio

from bilkatogo import BilkaToGoClient, FileTokenStorage


async def main() -> None:
    async with BilkaToGoClient(
        "you@example.com",
        "your-password",
        token_storage=FileTokenStorage("session.json"),
    ) as client:
        for order in await client.get_orders(limit=5):
            print(order.order_number, order.status, order.pickup_slot)

        current = await client.get_current_order()
        if current:
            if current.pickup_code:
                print("Pickup Code:", current.pickup_code.code)
            for substitution in current.substitutions:
                print(substitution.original_product, "->", substitution.replacement_product)


asyncio.run(main())
```

- `BilkaToGoClient(email, password, *, token_storage=None, httpx_client=None)` is an async
  context manager. Pass your own `httpx.AsyncClient` to control timeouts or proxies; the
  client only closes clients it created itself.
- `login()`, `get_orders(limit=5)`, `get_order(order_number)` and `get_current_order()` are the
  public operations. The client logs in on demand, refreshes the session before it expires and
  logs in again when Bilka rejects it.
- `token_storage` is any `TokenStorage` (async `load()` and `save(data)`). `FileTokenStorage`
  stores the session in a JSON file.
- Errors derive from `BilkaToGoError`: `InvalidCredentialsError`, `BilkaUnreachableError`,
  `UnexpectedResponseError`, `OrderNotFoundError`, `UnknownPickupPointError`,
  `NoPickupPointError` and `CartChangeRejectedError`.
- `Order.status` is an `OrderStatus` (Processing, Received, Packed, Picked Up, Not Collected,
  Cancelled, Sold Out, Unknown), using the same words as the website. The raw Bilka status code
  is kept in `status_code`. The mapping is listed in [GLOSSARY.md](GLOSSARY.md).
- `Product.article_number` from `search()` is Bilka's `objectID`, the same Article Number that
  Order Lines, Cart Lines and Missing Items carry. A Substitution carries both the
  `original_article_number` and the `replacement_article_number`.
- `Order.ordered_at`, `PickupSlot.starts_at` and `ends_at` are timezone-aware datetimes in
  Europe/Copenhagen.
- Amounts are `Decimal` kroner.
- `OrderDetails.order_total` is the Order Total, broken down into `deposit`, `service_fee`,
  `packaging`, `delivery_fee` and `discount`. `ordered_total` is what the Order cost when it was
  placed, looked up in the order history (`None` when it is not among the latest 20 Orders).
  Pass an `Order` to `get_order` instead of its number to skip that lookup. An `Order` from
  `get_orders()` has the same `ordered_total` and `order_total`, the latter Bilka's estimate
  until the Order is Packed.
- `get_cart()` returns your `Cart`: its `lines` (each a `CartLine` with `product`,
  `article_number`, `quantity`, `price` and `line_total`, with any Multibuy Offer already
  applied in the line total), the Cart `total` broken down into `deposit`, `service_fee`,
  `packaging` and `discount`, and the chosen `pickup_point`. Bilka's bag and Pakkeservice lines
  are not Cart Lines; they show up as `packaging` and `service_fee`.
- `set_cart_quantity(article_number, quantity)` sets how many of a Product the Cart holds (0
  removes the Cart Line), `add_to_cart(article_number, quantity=1)` adds to what is there,
  `remove_from_cart(article_number, quantity=None)` takes `quantity` off what is there (the whole
  Cart Line when it is `None` or more than the Cart holds) and `clear_cart()` empties the Cart.
  Each returns the Cart as read after the change, with Bilka's messages about the change in
  `Cart.warnings` (e.g. asking you to call customer service before ordering 99). When Bilka
  applies a different quantity than asked, they raise `CartChangeRejectedError` with Bilka's
  message. The client never places an Order.
- Logging in on the website or in the app can undo Cart changes made through the library: Bilka
  replaces the Cart with the one that browser or app saw at its previous login, kept for up to
  7 days. Check the Cart after logging in there if you changed it from here.
- `get_cart_json()`, `get_order_json(order)` and `get_current_order_json()` return Bilka's
  answer untouched; `bilkatogo.redact.redact()` replaces the personal data in it.

## Command line

```sh
pip install 'bilkatogo[cli]'
```

Or run it without installing:

```sh
uvx --from 'bilkatogo[cli]' bilkatogo --help
```

```sh
bilkatogo login                 # log in and store the session
bilkatogo orders --limit 10     # list your most recent orders
bilkatogo order                 # show the Current Order with its details
bilkatogo order 900000000001    # show one order by Order Number
bilkatogo pickup-points         # list Pickup Points and their Store, * marks your Cart's
bilkatogo search skyr           # Products with Article Number, Price, stock and Offer at your Cart's Store
bilkatogo search skyr --pickup-point "Bilka Vejle" --limit 5
bilkatogo cart                  # your Cart Lines, the Cart total and your Pickup Point
bilkatogo cart add 18381 --quantity 2   # add 2 of a Product by Article Number
bilkatogo cart set 18381 5      # hold exactly 5
bilkatogo cart remove 18381 --quantity 2   # take 2 off the Cart Line
bilkatogo cart remove 18381     # take it out of the Cart
```

`cart add`, `set` and `remove` change the Cart without asking and print the updated Cart and any
warning from Bilka. When Bilka applies a different quantity than asked they print Bilka's
message and exit 1. There is no command to empty the Cart. Logging in on the website or in the
app afterwards can undo these changes (see the Library section).

Every command accepts `--json` for machine-readable output.

Credentials are looked up in this order:

1. `--email` and `--password`
2. the `BILKATOGO_EMAIL` and `BILKATOGO_PASSWORD` environment variables
3. `config.json` (with `email` and `password` keys) in the user config directory
   (`~/.config/bilkatogo` on Linux, `%APPDATA%\bilkatogo` on Windows,
   `~/Library/Application Support/bilkatogo` on macOS); set `BILKATOGO_CONFIG_DIR` to use
   another directory
4. an interactive prompt

The session is stored as `session.json` in the same directory, so later commands don't log in
again.

The Current Order is the unfinished order with the earliest pickup slot or, when every order
is finished, the most recently finished one.

### Capturing a fixture

When an Order or the Cart shows something the CLI gets wrong, such as a Missing Item or a
Discount, `--raw` on `order` and `cart` prints Bilka's JSON as received, so it can become a test
fixture. Add `--redact` to replace your personal data with placeholders before sharing it:
name, email, address, phone, Pickup Code, Order Number (`9000000000xx`) and the customer ids
`uid` and `tid`. Pickup Points are public and stay.

```sh
bilkatogo order 900000000001 --raw --redact > order_details_missing.json
bilkatogo cart --raw --redact > cart.json
```

Read the file and search it for your name, email and address before attaching it to an
[issue](https://github.com/nickknissen/bilkatogo/issues); redaction covers the fields Bilka is
known to send, not every field it might add. `--redact` only works together with `--raw`.

## Home Assistant

[BilkaToGo](https://www.bilkatogo.dk) is Bilka's online grocery shop in Denmark: you order on
bilkatogo.dk and pick the order up at a Bilka store, or have it delivered. The integration follows
your Current Order in Home Assistant: its Order Status, Pickup Slot, Pickup Code and Order Total,
which products Bilka substituted or left out, and a calendar of your Pickup Slots. Actions
search Bilka's products and add them to your Cart.

### Use cases

- Get a notification when the order is packed, with the Pickup Code at hand.
- Hear about substitutions and Missing Items before you drive to the store.
- Show the next Pickup Slot on a dashboard or in a shared family calendar.
- Add groceries to the Cart by voice or from a shopping-list automation.

### Supported accounts

Any bilkatogo.dk customer account, for pickup at a Bilka store and for Home Delivery. Bilka
only serves Denmark.

### Installation

The integration needs Home Assistant 2026.10 or later.

1. Add the integration from the Home Assistant Marketplace (HACS) by searching for
   **BilkaToGo**, or add `https://github.com/nickknissen/bilkatogo` as a custom repository of
   type Integration. Restart Home Assistant afterwards.
2. Go to **Settings > Devices & services > Add integration** and pick **BilkaToGo**.
3. Fill in the fields below. Add the integration once per account.

| Field | Description |
|---|---|
| Email | The email address you sign in to bilkatogo.dk with. |
| Password | Your bilkatogo.dk password. Home Assistant stores it, so it can sign in again when Bilka's session expires. |

The integration has no options. If the password changes, Home Assistant asks you to sign in
again.

### Data updates

The integration polls Bilka every 60 minutes, and every 5 minutes from 3 hours before the
Pickup Slot starts until the order is picked up, not collected or cancelled. If you set your
own polling interval, keep it at 5 minutes or more so the traffic still looks like a person
checking their order. To update now, use the `homeassistant.update_entity` action on any
BilkaToGo entity.

### Entities

All entities, except the calendar, describe the Current Order. Ready for pickup is on while the
Current Order is Packed.

| Entity | State | Attributes |
|---|---|---|
| Order Status | Processing, Received, Packed, Picked Up, Not Collected, Cancelled, Sold Out or Unknown | Bilka status code, Order Number |
| Substitutions | Number of substituted products | List of Original Product, Replacement Product, their Article Numbers, the ordered quantity and the Replacement Product's quantity |
| Missing Items | Number of products packed short without a replacement | List of Product, Article Number, ordered and missing quantity |
| Order Total | What you pay for the order now (DKK) | Ordered Total, difference from the Ordered Total (negative when you pay less), Deposit, Service Fee, Packaging, Delivery Fee, Discount |
| Pickup Slot | Start of the pickup slot (timestamp, Europe/Copenhagen) | Pickup Slot end |
| Pickup Code | The code you enter at check-in | |
| Pickup Point | The Bilka store you pick up from | |
| Ready for pickup | On while the order is Packed | |
| Substitution (event) | When the last substitution fired | Order Number, Original Product, Replacement Product, their Article Numbers, ordered quantity, quantity |
| Missing item (event) | When the last Missing Item fired | Order Number, Product, Article Number, ordered and missing quantity |
| Pickup slots (calendar) | On during a Pickup Slot | |

Substitutions, Missing Items, the Pickup Code and the Order Total breakdown (Deposit, Service
Fee, Packaging, Delivery Fee, Discount) are fetched once the order is packed. Before that the
Order Total is Bilka's estimate from the order history.

An Order Status of Unknown (`unrecognized`) means Bilka sent a status code the integration does
not know yet; the code is in the attribute. Without any order the entities have no state.

The calendar holds an event for each of your 20 most recent Orders that has a Pickup Slot,
upcoming and past, except Cancelled and Sold Out Orders. An event spans the Pickup Slot, is
called "BilkaToGo", has the Order Number as its description and the Pickup Point as its
location. A Home Delivery event has no location. The text is the same in every language; the
Order Status sensor shows the status.

### Actions

| Action | Fields | Response |
|---|---|---|
| `bilkatogo.add_to_cart` | `article_number`, `quantity` (default 1) | Optional: the Cart, with its Cart Lines (Product, Article Number, quantity, Price, line total), total, Deposit, Service Fee, Packaging, Discount, Pickup Point and Bilka's warnings |
| `bilkatogo.remove_from_cart` | `article_number`, `quantity` (default: all of it) | Optional: the Cart, as for `add_to_cart` |
| `bilkatogo.search` | `query`, `limit` (default 10) | `products`: Article Number, name, brand, Price, Unit Price and unit, in stock, Offer and Multibuy Offer |
| `bilkatogo.get_cart` | none | The Cart, as for `add_to_cart`, without changing it |

`add_to_cart` adds to what is already in the Cart and `remove_from_cart` takes `quantity` off
it, or the whole Cart Line without one; neither places an Order. `search` searches at the
Store serving the Cart's Pickup Point, so choose one on bilkatogo.dk first. With more than one
account added, pass `config_entry_id`. A script that searches and adds the first hit:

```yaml
script:
  add_skyr:
    sequence:
      - action: bilkatogo.search
        data:
          query: skyr
          limit: 1
        response_variable: found
      - action: bilkatogo.add_to_cart
        data:
          article_number: "{{ found.products[0].article_number }}"
```

### Substitution and Missing item events

The Substitution event entity fires once for every new substitution, and the Missing item
event entity once for every new Missing Item. Items that already fired are remembered across
restarts and reloads. Each event carries its data as attributes:

| Event entity | Attribute | Example |
|---|---|---|
| Substitution | `order_number` | `900000000001` |
| | `original_product` | `Arla minimælk 1 l` |
| | `original_article_number` | `18381` |
| | `ordered_quantity` | `2` |
| | `replacement_product` | `Øko minimælk 1 l` |
| | `replacement_article_number` | `19753` |
| | `quantity` | `2` |
| Missing item | `order_number` | `900000000003` |
| | `product` | `Skyr` |
| | `article_number` | `932058` |
| | `ordered_quantity` | `4` |
| | `missing_quantity` | `2` |

An automation that sends a notification for each substitution. `not_from: unavailable` keeps
it quiet when the entity comes back after a reload or after Bilka was unreachable:

```yaml
automation:
  - alias: BilkaToGo substitution
    triggers:
      - trigger: state
        entity_id: event.me_example_com_substitution
        not_from: unavailable
    actions:
      - action: notify.notify
        data:
          message: >
            {{ trigger.to_state.attributes.ordered_quantity }} x
            {{ trigger.to_state.attributes.original_product }} was replaced by
            {{ trigger.to_state.attributes.quantity }} x
            {{ trigger.to_state.attributes.replacement_product }}.
```

And one for each Missing Item:

```yaml
automation:
  - alias: BilkaToGo missing item
    triggers:
      - trigger: state
        entity_id: event.me_example_com_missing_item
        not_from: unavailable
    actions:
      - action: notify.notify
        data:
          message: >
            {{ trigger.to_state.attributes.product }}:
            {{ trigger.to_state.attributes.missing_quantity }}
            of {{ trigger.to_state.attributes.ordered_quantity }} missing.
```

### Ready for pickup notification

An automation that sends the Pickup Code once the order is packed. The entity IDs start with
your account's email; replace them with yours from **Settings > Devices & services >
BilkaToGo**.

```yaml
automation:
  - alias: BilkaToGo ready for pickup
    triggers:
      - trigger: state
        entity_id: binary_sensor.me_example_com_ready_for_pickup
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: >
            Your groceries are packed. Pickup Code:
            {{ states('sensor.me_example_com_pickup_code') }}.
```

### Known limitations

- Bilka has no public API. The integration uses the same private API as bilkatogo.dk, which can
  change without notice, and automated access may conflict with Bilka's terms.
- The sensors follow one order, the Current Order. With several open orders, the others only
  show in the calendar, which covers the 20 most recent Orders.
- Updates come from polling, so a change at Bilka can take up to 60 minutes to show, or 5
  minutes close to the Pickup Slot.
- The Pickup Code, Substitutions, Missing Items and the Order Total breakdown are only known
  once the order is packed.
- The Home Delivery address is left out of entities and the calendar, as it is personal.
- `add_to_cart` and `search` need a Pickup Point chosen on bilkatogo.dk. The integration never
  places, changes or cancels an order.

### Troubleshooting

- **Home Assistant asks you to sign in again.** Bilka rejected the stored password, usually
  because it was changed. Select **Reconfigure** on the BilkaToGo card under **Settings >
  Devices & services** and enter the new password.
- **The entities are unavailable.** Home Assistant could not reach Bilka, or Bilka answered
  unexpectedly. The integration logs this once and retries on the next poll. If it lasts, check
  that bilkatogo.dk works in a browser.
- **The Order Status is Unknown.** Bilka sent a status the integration does not know. Open an
  [issue](https://github.com/nickknissen/bilkatogo/issues) with the Bilka status code from the
  attribute.
- **`search` or `add_to_cart` fails with "no pickup point".** Choose a Pickup Point on
  bilkatogo.dk, then try again.

For more detail in the log, select **Enable debug logging** on the integration's page,
reproduce the problem, then disable it again; Home Assistant then downloads the log. Or add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.bilkatogo: debug
    bilkatogo: debug
```

Attach the diagnostics (**Download diagnostics** on the integration's page) to an issue; they
leave out your email, password, name, address, phone number and Pickup Code.

### Removal

1. Go to **Settings > Devices & services > BilkaToGo**.
2. Open the menu (⋮) next to the account and select **Delete**.

Home Assistant also deletes the stored Bilka session and the record of which Substitutions and
Missing Items it already announced. To remove the integration's files too, remove BilkaToGo in
HACS and restart Home Assistant.

The entities are tied to the account, not to this setup of it. If you add the same account
again, its entities come back with the names and entity IDs you gave them.

## Development

Library and CLI:

```sh
uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run pytest
```

Home Assistant tests use their own dependency group, because
`pytest-homeassistant-custom-component` pins its own pytest and httpx. Keep them in a
separate virtual environment so the library environment stays untouched:

```sh
UV_PROJECT_ENVIRONMENT=.venv-ha uv run --no-default-groups --group ha pytest tests/ha
UV_PROJECT_ENVIRONMENT=.venv-ha uv run --no-default-groups --group ha ty check custom_components tests/ha
```

Home Assistant does not run on Windows. There, `scripts/ha-test.sh` runs both commands in WSL:

```sh
wsl.exe -e bash scripts/ha-test.sh
```

The Home Assistant tests run the real library against the fake Bilka backend in
`tests/fake_bilka.py`. The integration creates its httpx client with Home Assistant's
`create_async_httpx_client`, and `tests/ha/conftest.py` routes every request at the
transport level to the fake backend with `respx`, so nothing in the integration is patched.

Releasing is described in [AGENTS.md](AGENTS.md#releasing).

## License

MIT
