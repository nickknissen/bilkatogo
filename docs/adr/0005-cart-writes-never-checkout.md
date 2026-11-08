# Cart writes, never checkout

From v3 the client changes the customer's Cart: it sets the quantity of a Product by Article Number (`shop/v6/ChangeLineCount`, 0 removes the Cart Line) and empties the Cart (`shop/v6/EmptyCart`). These are the first writes against Bilka's private API. We stop at the Cart: the client never chooses a Pickup Slot or Pickup Point, places, pays for or cancels an Order. A Cart change is reversible and costs nothing; a placed Order spends the customer's money and commits Bilka to packing it, so a bug or a misread response there has real consequences that a test against a fake backend cannot rule out.

Bilka enforces its own rules on a Cart change (stock, assortment, Offer limit per customer) and the client does not check them in advance. Every captured change answered HTTP 200. An applied change has `eid: -1`, and Bilka's Danish `message` or `offerLimitMessage` can accompany it (99 cucumbers: "Ring venligst til kundeservice ..."), so a message is a warning, not a failure. A change Bilka refused outright, such as an unavailable Product, answers with an error body instead (`{"msg": "Produktet ... er desværre ikke aktuelt tilgængeligt", "code": null, "eid": 410}`). The client treats a change as rejected when Bilka refused it or applied a different quantity than asked (`productAmount` differs from `count`), and raises that with Bilka's message.

## Consequences

- Anything past the Cart, including choosing the Pickup Point, needs a new decision that supersedes this one.
- The CLI changes the Cart without a confirmation prompt, but offers no command to empty it, so one mistyped command cannot throw away a Cart the customer built up. The exception is `recipes add` (ADR 0007), which asks first because it adds a whole Recipe's Products at once.
- Every write is checked by hand once against the maintainer's own Cart before a release; CI only exercises the fake backend.
- A Cart change can be undone by Bilka, not the client: logging in on the website or in the app replaces the Cart with the one stored under that browser's `cart` cookie (see `docs/agents/capturing.md`, How Bilka's Cart and login behave). The client never calls `auth/LoginJWT` (the shop accepts Gigya's JWT on its own, checked live on 2026-11-02), so its own logins never undo a change, and documents the rest.

## Pickup and Home Delivery Carts

The client reads the Cart the same way whether it is collected at a Pickup Point or brought by Home Delivery (#71). Bilka sends both as `deliveryAddress`; it is the customer's home when it has a `dawa_uid` (the Danish address register id) or a `recipient_name`, both empty for a Pickup Point. The name is no guide: standalone Pickup Points are named e.g. "Pickup Fredericia", not "Bilka …". The first real Home Delivery capture (#76) has `recipient_name` but no `dawa_uid`, because the customer had typed in only a zip code, so either field is enough. In that capture `deliveryAddress.hd_available` is `true`, against `false` on both pickup captures, but it is not used until a second capture confirms it; the top-level `hd_available` is `true` for both modes. For Home Delivery `Cart.pickup_point` is `None`, so the home address never reaches the CLI table or Home Assistant, and `Cart.store_id` (`sid`) still gives the Store that serves the Cart, which product search uses.

Reading the mode, the Minimum Order, the Delivery Fee and the chosen Pickup Slot changes nothing above: the client still never changes the delivery address or the Pickup Slot.
