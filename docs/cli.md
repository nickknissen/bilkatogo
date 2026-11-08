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
bilkatogo search skyr           # Products with Article Number, Price, stock and Offer at your Cart's Store
bilkatogo search skyr --pickup-point "Bilka Vejle" --limit 5
bilkatogo search mælk --in-stock --sort price   # the cheapest of the 20 best matches, counting Offers
bilkatogo search kaffe --on-offer --in-leaflet  # Offers and Multibuy Offers in this week's Leaflet
bilkatogo cart                  # your Cart Lines, the Cart total, Pickup Point or Home Delivery, Pickup Slot, fees and Minimum Order
bilkatogo cart add 18381 --quantity 2   # add 2 of a Product by Article Number
bilkatogo cart add-items 18381:10 19753 140787:2   # add several; 19753 gets --quantity (1)
bilkatogo cart set 18381 5      # hold exactly 5
bilkatogo cart remove 18381 --quantity 2   # take 2 off the Cart Line
bilkatogo cart remove 18381     # take it out of the Cart
```

`cart add`, `set` and `remove` change the Cart without asking and print the updated Cart and any
warning from Bilka. When Bilka applies a different quantity than asked they print Bilka's
message and exit 1. `cart add-items` prints a line per Product with its result, then the Cart,
and exits 1 when any Product was rejected or skipped. There is no command to empty the Cart.
Logging in on the website or in the app afterwards can undo these changes (see the
[library documentation](library.md)).

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

## Capturing a fixture

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
