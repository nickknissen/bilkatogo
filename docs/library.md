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
  stores the session in a JSON file. The session holds the JWT, which lasts 24 hours, and
  Gigya's login token (`"version": 2`), from which the client gets a new JWT when it runs out.
  So after one login with the password, `BilkaToGoClient(email, token_storage=...)` works
  without one for as long as Gigya accepts the token. When Gigya refuses it, or the session
  was saved before login tokens, or the shop rejects a JWT, the client logs in with the
  password; without one it raises `InvalidCredentialsError` ("The saved session has expired;
  log in again with your password"). Treat the session file like the password. The session
  also remembers the Home Address by its id and zip code, without the name (`home_address`,
  see `get_home_address()` below); a session without it works as before.
- Errors derive from `BilkaToGoError`: `InvalidCredentialsError`, `BilkaUnreachableError`,
  `BilkaBusyError`, `UnexpectedResponseError`, `OrderNotFoundError`, `UnknownPickupPointError`,
  `NoPickupPointError`, `NoHomeAddressError`, `CartChangeRejectedError`,
  `PickupSlotUnavailableError` and, for Recipes, `WebsiteChangedError`,
  `UnknownRecipeError` and `UnknownCategoryError`. `BilkaBusyError` means Bilka or its
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
- `get_pickup_points()` lists the `PickupPoint`s the Cart can go to, Bilka stores and
  standalone points, each with its `name`, `address`, `zip_code`, `city`, `store_id` and Bilka's
  `id`. It never lists the Home Address, which Bilka lists among them while the Cart is on Home
  Delivery. `get_pickup_point(name)` finds one by name, ignoring case, and raises
  `UnknownPickupPointError` when none has it.
- `get_cart()` returns your `Cart`: its `lines` (each a `CartLine` with `product`,
  `article_number`, `quantity`, `price`, `line_total` and `unavailable_reason`, with any
  Multibuy Offer already applied in the line total), the Cart `total` broken down into `deposit`, `service_fee`,
  `packaging`, `delivery_fee` and `discount`, and the chosen `pickup_point`. Bilka's bag and
  Pakkeservice lines are not Cart Lines; they show up as `packaging` and `service_fee`.
  `home_delivery` is `True` when the Cart is brought to the customer's address, as on `Order`;
  `pickup_point` is then `None`, and `store_id` is the Store that serves the Cart either way.
  `minimum_order` is the Minimum Order (`None` when Bilka sets none, as for pickup) and
  `minimum_left` how much more the Cart needs to reach it. `pickup_slot` is the Pickup Slot
  chosen in the Cart, `None` once it has ended (Bilka keeps the last Order's slot there for
  weeks), `pickup_slot_closed` whether Bilka marks it closed where the Cart is now, and
  `next_available_date` the next day Bilka can deliver. `warnings` holds Bilka's notices on
  the Cart, such as how far it is from the Minimum Order. A Cart Line Bilka can't supply where
  the Cart is received stays in `lines` with Bilka's `unavailable_reason`, e.g. "Udsolgt"
  (`None` while it is available), and still counts in the total.
- `choose_pickup_point(name)` moves the Cart to a Pickup Point (ADR 0009), and
  `choose_home_delivery()` moves it to Home Delivery at the Home Address. The Store, and with it
  the Prices, Offers and stock of every Cart Line, follow the Cart, so `search`, `add_to_cart`
  and Pickup Slots work for the new Store at once. Both read the Cart before and after and
  return a `DeliveryChange`: the Cart `before` and `after` (place, `store_id`, `total`, fees and,
  for Home Delivery, the `minimum_order`), the Cart Lines that became `unavailable` (each with
  its `unavailable_reason`), those `available_again` and those `gone` (none in any capture:
  Bilka keeps lines it can't supply), and `pickup_slot_closed` when the chosen Pickup Slot is
  not offered at the new place; choose another with `choose_pickup_slot`. Bilka adds and removes
  the Delivery Fee and Packaging itself. `changed` is `False` when the Cart was there already
  and nothing was sent. An unknown name raises `UnknownPickupPointError` without sending
  anything; when Bilka does not know the place, `reason` is its message and the Cart stays. A
  move after which the Cart is somewhere else raises `UnexpectedResponseError`. The client
  never enters or edits an address.
- `get_home_address()` returns the `HomeAddress` Home Delivery goes to: the Cart's while it is
  on Home Delivery (with `id`, `zip_code`, `recipient_name`, `store_id` and `minimum_order`),
  otherwise the one the client remembers (only `id` and `zip_code`), otherwise `None`. Bilka
  shows the Home Address only while the Cart is on Home Delivery, and holds no street until
  checkout, so choose Home Delivery once on bilkatogo.dk: the next time the client reads the
  Cart or the Pickup Points it remembers it, in the saved session when there is token storage.
  Until then `choose_home_delivery()` raises `NoHomeAddressError` without sending anything.
- `get_pickup_slots(days=7)` returns `PickupSlots` for whatever the Cart has, its Pickup Point
  or Home Delivery address, over the next `days` calendar days in Copenhagen, today included.
  `slots` lists each `AvailablePickupSlot` with its `slot` (a `PickupSlot`), `availability` (a
  `SlotAvailability`: `open`, `closed` once its order deadline has passed, or `sold_out`),
  `order_before` (Bilka's deadline as the website words it, e.g. "på søndag kl. 15:00") and
  `note` (Bilka's text, e.g. when a night slot is delivered, or else why it is closed).
  `days_without_slots` maps each day without slots to Bilka's reason, e.g. "udsolgt", empty
  when it gives none. `next_open` is the earliest open slot, `chosen` the slot chosen in the
  Cart while it lies ahead, and `pickup_point` and `home_delivery` say what the slots are for.
  `starting_at(day, start)` lists the slots on a day that start at a time. It reads the Cart
  and Bilka's `AvailableDeliveryDates`. A Home Delivery night slot runs from 00:00 to 06:00 on
  its date: Bilka delivers it the night before. A `PickupSlot` whose end is at or before its
  start ends the next day.
- `choose_pickup_slot(slot)` chooses the Cart's Pickup Slot (ADR 0008). The slot must be one
  `get_pickup_slots(days=14)` lists as open, with the same date, start and end. A Home Delivery
  night slot is chosen as listed, e.g. `PickupSlot(date(2026, 11, 12), time(0), time(6))`. It
  returns a `ChosenPickupSlot`: the `chosen` slot, the one it `replaced` (`None` when the Cart
  had none ahead) and `changed`, which is `False` when the slot was already chosen and nothing
  was sent, even if it has sold out since. A slot that is not listed, closed or sold out raises
  `PickupSlotUnavailableError` without changing anything. The error has the `slot`, a `reason`
  and the listed `availability`. When Bilka refuses the slot itself, e.g. because it sold out
  in between, `refused` is `True`, the `reason` is Bilka's message and the Cart keeps its slot. Choosing holds nothing: the slot
  can still sell out until the Order is placed, and a slot given up can sell out too, so keep
  `replaced` if you may want to go back. Choose the Pickup Point or Home Delivery first, with
  `choose_pickup_point` or `choose_home_delivery`.
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
- `get_cart_json()`, `get_order_json(order)`, `get_current_order_json()` and
  `get_pickup_slots_json()` return Bilka's answer untouched; `bilkatogo.redact.redact()` replaces the personal data in it.

## Recipes

`bilkatogo.recipes.RecipeReader(httpx_client=None)` reads Bilka's Recipes from bilkatogo.dk's
public pages, without logging in. It is an async context manager, like the client.

- `madplan()` returns this week's Madplan, seven `Recipe`s from Monday to Sunday, each with its
  `id` (the path below /inspiration/opskrifter/, e.g. "aftensmad/pastaretter/carbonara"),
  `name`, `url` and `group` (the heading it sat under, "Madplan til uge 46").
- `category(category, limit=20)` returns a category's Recipes; `category` is a name or path
  ("Aftensmad", "aftensmad/danske-retter", "børn i køkkenet"). A category with
  sub-categories gives one Recipe from each in turn. `categories()` lists the top-level ones.
- `recipe(recipe)` returns `RecipeDetails` for a link or path: `persons`, `time` as written
  ("35 min"), `image`, `ingredients`, `method` (a `RecipeSection` with `title` and `steps`
  per heading after the ingredients) and `article_numbers`, the Recipe Products in page order.
- `BilkaToGoClient.get_recipe(recipe)` adds `products`: the Recipe Products at the Store that
  serves the Cart, through `get_products`, leaving out those the Store does not sell. To add
  them, pass `{product.article_number: 1 for product in recipe.products}` to
  `add_items_to_cart`; the library has no call that adds a Recipe on its own.
- The pages are Bilka's website, not an API (ADR 0007). When their format changes the reader
  raises `WebsiteChangedError`; an unknown path raises `UnknownRecipeError` or
  `UnknownCategoryError`, naming the top-level categories. Nothing else in the library depends
  on them. At most 5 pages are read at once.
