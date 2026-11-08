# Library

The `bilkatogo` package is an async Python client for your BilkaToGo orders, Pickup Points,
product search and Cart. See the [README](../README.md) for the command line tool and the Home
Assistant integration.

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
- `get_order_history()` returns the `OrderHistory`: the latest 20 Orders in `orders` and the
  Current Order among them in `current_order` (`None` without Orders), from one look at the
  order history. Use it when you need both, instead of `get_orders()` plus
  `get_current_order()`. `Order.finished` tells whether nothing more will happen to an Order
  (Picked Up, Not Collected, Cancelled or Sold Out).
- `token_storage` is any `TokenStorage` (async `load()` and `save(data)`). `FileTokenStorage`
  stores the session in a JSON file.
- Errors derive from `BilkaToGoError`: `InvalidCredentialsError`, `BilkaUnreachableError`,
  `BilkaBusyError`, `UnexpectedResponseError`, `OrderNotFoundError`, `UnknownPickupPointError`,
  `NoPickupPointError` and `CartChangeRejectedError`. `BilkaBusyError` means Bilka or its
  login service is rate limiting or failing for now (HTTP 429 or 5xx from the shop, Gigya error
  403048 or 500000 and up): try again later; the credentials are not at fault.
- `search(text, pickup_point=None, limit=20, ...)` finds Products at the Store of a Pickup
  Point, by default the Store that serves the Cart. When no Product has every word, Bilka's
  index drops words until something matches, as the website does. `get_products([article_number,
  ...], pickup_point=None)` looks Products up by Article Number at that Store, in the order
  asked, leaving out those the Store does not sell; it finds Products kept out of search too,
  and takes at most 1000 at once. Anything but digits raises `ValueError`. Each `Product` has its `ean`, `size` (e.g. "1 kg") and
  `category` (Bilka's category path and product type, e.g. "Frugt & grønt > Frugt > Bananer"),
  each `None` when Bilka has none. Bilka's placeholder Offer label "Se Her" reads "Tilbud".
- `Order.status` is an `OrderStatus` (Processing, Received, Packed, Picked Up, Not Collected,
  Cancelled, Sold Out, Unknown), using the same words as the website. The raw Bilka status code
  is kept in `status_code`. The mapping is listed in [GLOSSARY.md](../GLOSSARY.md).
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
  `packaging`, `delivery_fee` and `discount`, and the chosen `pickup_point`. Bilka's bag and
  Pakkeservice lines are not Cart Lines; they show up as `packaging` and `service_fee`.
  `home_delivery` is `True` when the Cart is brought to the customer's address, as on `Order`;
  `pickup_point` is then `None`, and `store_id` is the Store that serves the Cart either way.
  `minimum_order` is the Minimum Order (`None` when Bilka sets none, as for pickup) and
  `minimum_left` how much more the Cart needs to reach it. `pickup_slot` is the Pickup Slot
  chosen in the Cart, `None` once it has ended (Bilka keeps the last Order's slot there for
  weeks), and `next_available_date` the next day Bilka can deliver. `warnings` holds Bilka's
  notices on the Cart, such as how far it is from the Minimum Order.
- `set_cart_quantity(article_number, quantity)` sets how many of a Product the Cart holds (0
  removes the Cart Line), `add_to_cart(article_number, quantity=1)` adds to what is there,
  `remove_from_cart(article_number, quantity=None)` takes `quantity` off what is there (the whole
  Cart Line when it is `None` or more than the Cart holds) and `clear_cart()` empties the Cart.
  Each returns the Cart as read after the change, with Bilka's messages about the change in
  `Cart.warnings` (e.g. asking you to call customer service before ordering 99). When Bilka
  applies a different quantity than asked, they raise `CartChangeRejectedError` with Bilka's
  message. The client never places an Order.
- `set_cart_quantities({article_number: quantity, ...})` and `add_items_to_cart({article_number:
  quantity, ...})` change several Products one after another, a short pause apart, and read the
  Cart once at the end. They return `CartChanges`: the `cart` and one `CartChange` per Product
  with the quantity `requested` (after adding, for `add_items_to_cart`), the quantity `applied`,
  a `status` (`applied`, `rejected` or `skipped`) and Bilka's `message`. A rejected Product does
  not stop the rest. A change Bilka does not answer, or answers with HTTP 429 or 5xx, is tried
  again up to three times; if it still fails, it and the rest are `skipped` and the Cart shows
  what got through.
- Logging in on the website or in the app can undo Cart changes made through the library: Bilka
  replaces the Cart with the one that browser or app saw at its previous login, kept for up to
  7 days. Check the Cart after logging in there if you changed it from here.
- `get_cart_json()`, `get_order_json(order)` and `get_current_order_json()` return Bilka's
  answer untouched; `bilkatogo.redact.redact()` replaces the personal data in it.
