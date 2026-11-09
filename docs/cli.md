# Command line

The `bilkatogo` command follows your BilkaToGo orders, searches Bilka's products and changes your
Cart from a terminal or a script. See the [README](../README.md) for the Home Assistant
integration and [library.md](library.md) for the Python library.

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
bilkatogo pickup-points choose "Bilka Odense"     # move your Cart there and show what changed
bilkatogo pickup-points choose --home-delivery    # move your Cart to Home Delivery
bilkatogo pickup-slots          # the next 7 days' Pickup Slots for your Cart: open, closed or sold out
bilkatogo pickup-slots --days 3 # today and the next 2 days
bilkatogo pickup-slots choose 2026-11-12 16:00   # choose the Cart's Pickup Slot by date and start
bilkatogo search skyr           # Products with Article Number, Size, Price, stock and Offer at your Cart's Store; "not delivered here" marks a Delivery Block
bilkatogo search skyr --pickup-point "Bilka Vejle" --limit 5
bilkatogo search mælk --in-stock --sort price   # the cheapest of the 20 best matches, counting Offers
bilkatogo search kaffe --on-offer --in-leaflet  # Offers and Multibuy Offers in this week's Leaflet
bilkatogo products 18381 19753  # the same, for these Article Numbers (--pickup-point, --json)
bilkatogo cart                  # your Cart Lines, the Cart total, Pickup Point or Home Delivery, Pickup Slot, fees and Minimum Order
bilkatogo cart add 18381 --quantity 2   # add 2 of a Product by Article Number
bilkatogo cart add-items 18381:10 19753 140787:2   # add several; 19753 gets --quantity (1)
bilkatogo cart set 18381 5      # hold exactly 5
bilkatogo cart remove 18381 --quantity 2   # take 2 off the Cart Line
bilkatogo cart remove 18381     # take it out of the Cart
bilkatogo recipes               # this week's Madplan: day, Recipe and its path
bilkatogo recipes list aftensmad --limit 10        # the Recipes in a category
bilkatogo recipes show aftensmad/danske-retter/karbonader   # description, ingredients, method, Products with Price and stock
bilkatogo recipes add aftensmad/danske-retter/karbonader    # asks, then adds one of each Product
```

`recipes` and `recipes list` read bilkatogo.dk's public Recipe pages without logging in; a
category is a name or path, e.g. `aftensmad`, `aftensmad/danske-retter` or "børn i køkkenet".
`recipes show` and `add` take the Recipe's link or path and log in for the Prices at your
Cart's Store. `recipes add` lists the Products your Store sells and asks "Add N Products to the
Cart? [y/N]" before adding one of each, the one command that asks, because it adds many Cart
Lines at once; `--yes` skips the question and `--json` needs it. Products your Store does not
sell are named and left out. The Recipe pages are Bilka's website, not an API: when it
changes, these commands fail with "bilkatogo.dk's recipe pages have changed" and the rest keep
working.

`cart add`, `set` and `remove` change the Cart without asking and print the updated Cart and any
warning from Bilka. When Bilka applies a different quantity than asked they print Bilka's
message and exit 1. `cart add-items` prints a line per Product with its result, then the Cart,
and exits 1 when any Product was rejected or skipped. There is no command to empty the Cart.
Logging in on the website or in the app afterwards can undo these changes (see the
[library documentation](library.md)).

`pickup-slots` shows the Pickup Slots for whatever your Cart has, its Pickup Point or Home
Delivery, in a table of Date, Start, End, Availability (Open, Closed once its order deadline
has passed, Sold out), Order before (Bilka's deadline) and Note, with a "No slots" row for a
day Bilka offers none. It ends with the next open slot, or "No open Pickup Slots in the next N
days.". With `--json` it adds `next_open`.

`pickup-slots choose DATE START` chooses one of the open slots in the next 14 days for your
Cart, without asking, like `cart add`. `DATE` is ISO (`2026-11-12`) and `START` is `HH:MM`. A
Home Delivery night slot is chosen by the date it is listed under and `00:00`. Add `--end HH:MM`
if two slots on that day start at the same time. It prints the chosen slot and the one it
replaced, so you can go back while that one is still open. A slot that is closed, sold out or
not offered fails with a message and exit 1, and the Cart keeps its slot. Choosing holds
nothing: the slot can still sell out until you place the Order on bilkatogo.dk.

`pickup-points` lists only Pickup Points, never your Home Address. `pickup-points choose NAME`
moves your Cart to a Pickup Point, and `pickup-points choose --home-delivery` to Home Delivery,
without asking, like `cart add`. Your Cart Lines then follow that Store's Prices, Offers and
stock, and `search` and `pickup-slots` use it. It prints what changed, before and after: the
place, the Store, the Cart total, Service Fee, Packaging and Delivery Fee, and the Minimum
Order for Home Delivery. Then come warnings for Cart Lines Bilka can't supply there (Bilka
keeps them in the Cart, marked unavailable with its reason) and for a chosen Pickup Slot that
is closed at the new place, plus the lines available again. Fix those before you order. It
never prints your Home Address, only "Home Delivery". `--json` gives the whole report: the
Cart `before` and `after`, `unavailable`, `available_again`, `gone` and `pickup_slot_closed`.
An unknown name fails with a message and exit 1, and the Cart stays.

Bilka shows your Home Address only while the Cart is on Home Delivery, so choose Home Delivery
once on bilkatogo.dk. The next command that reads the Cart or the Pickup Points remembers it
(its id and zip code, in the session file), and from then on `choose --home-delivery` works.
Until then it fails and says so.

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
again. It renews itself without the password for days, as long as Bilka's login service
accepts it, so after `bilkatogo login` the password need not be in `config.json` or the
environment. When it can no longer be renewed, commands without a password fail with
"Session expired. Run `bilkatogo login`." With a password from the options, the environment
or `config.json`, they log in again on their own. `session.json` lets anyone read and change
your Cart, so keep it as private as the password; it is readable only by you on Linux and
macOS.

The Current Order is the unfinished order with the earliest pickup slot or, when every order
is finished, the most recently finished one.

## Capturing a fixture

When an Order or the Cart shows something the CLI gets wrong, such as a Missing Item or a
Discount, `--raw` on `order`, `cart` and `pickup-slots` prints Bilka's JSON as received, so it can become a test
fixture. Add `--redact` to replace your personal data with placeholders before sharing it:
name, email, address, phone, Pickup Code, Order Number (`9000000000xx`) and the customer ids
`uid` and `tid`. Pickup Points are public and stay.

```sh
bilkatogo order 900000000001 --raw --redact > order_details_missing.json
bilkatogo cart --raw --redact > cart.json
bilkatogo pickup-slots --raw --redact > available_delivery_dates_pickup.json
```

Read the file and search it for your name, email and address before attaching it to an
[issue](https://github.com/nickknissen/bilkatogo/issues); redaction covers the fields Bilka is
known to send, not every field it might add. `--redact` only works together with `--raw`.
