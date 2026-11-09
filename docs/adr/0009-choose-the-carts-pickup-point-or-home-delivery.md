# Choose the Cart's Pickup Point or Home Delivery

The client may now choose how the Cart is received (`GET shop/v6/SetDeliveryAddress`): it can move the Cart to another Pickup Point, or to Home Delivery at a Home Address the account already has. This supersedes what was left of ADR 0005's "never chooses a Pickup Point". ADR 0008 left this for its own decision because the switch also changes the Store, and with it the assortment, Prices, Offers and stock of everything in the Cart.

Like choosing a Pickup Slot, the switch only changes the Cart. It spends nothing and commits Bilka to nothing until the Order is placed. tkjaergaard switched live from home to Bilka Odense and back on 2026-11-02, and the Cart ended as it started, with its lines and chosen slot kept ([research.md:72](https://github.com/tkjaergaard/bilkatogo-mcp/blob/43b6d91743d77dba41877baf0e007341067bca3b/docs/research.md#L72)). ha-middagsro sends it before every Cart write. Without it, the user must open the website to move the Cart before `search`, `add_to_cart` and Pickup Slots work for another Store.

The client chooses only among the places `AvailableDeliveryAddresses` lists for the account: a Pickup Point by name, or a Home Address Bilka already has. It never enters or edits an address. That would mean a typed address and Denmark's address register (`dawa_uid`), and it would put a new personal address into Bilka's records from a script. The client still never places, pays for or cancels an Order (ADR 0005).

## Consequences

- A switch can change every Cart Line's Price and Offer and the Cart's fees. Home Delivery adds the Delivery Fee and its Minimum Order. The client reads the Cart before and after the switch and reports what changed: the Store, the total, and lines that are gone or that Bilka now marks unavailable.
- Whether Bilka drops Cart Lines the new Store does not sell is unknown until captured. If it does, switching back does not bring them back, so the report names them and lets the user add them again.
- The chosen Pickup Slot stays, but the new place may not offer it (Bilka marks it `closed: 1`). The report says so, and choosing a slot (ADR 0008) is the next step.
- `get_pickup_points` must stop returning the account's Home Address as a Pickup Point. Pickup Points and Home Addresses become separate in the model, and the Home Address stays out of Home Assistant states and redacted output like the rest of the customer's personal data.
- As with every write, it is checked by hand once against the maintainer's own Cart before a release, putting the original place back afterwards.

## 2026-11-09: the client remembers the Home Address (#89)

Captured on the maintainer's Cart: `AvailableDeliveryAddresses` lists the account's Home Address (`facility` 8) only while the Cart is on Home Delivery, and Bilka holds it as a zip code and a name, without a street or `dawa_uid`. Past Orders do not carry it. Sending `SetDeliveryAddress` with its `id` and zip code still moves the Cart to Home Delivery when it is not listed.

So the client remembers the last Home Address it has seen in the Cart or in that list, by `id` and zip code only, in the saved session, and switches to it by `id` like to a Pickup Point. Until it has seen one, choosing Home Delivery fails and asks the user to choose it once on bilkatogo.dk. The client still never enters or edits an address. There is at most one Home Address.

The captures also settled the open consequences above. Bilka keeps the Cart Lines the new place cannot supply and marks them unavailable with a reason ("Udsolgt", "Leveres ikke via Cleveron Container"), and makes them available again when the Cart moves back. So the report names the lines that became unavailable or available again, rather than lines that are gone. Bilka adds and removes the Delivery Fee and Packaging itself, and a standalone Pickup Point charges a Delivery Fee too.
