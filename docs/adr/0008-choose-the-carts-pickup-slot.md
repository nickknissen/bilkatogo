# Choose the Cart's Pickup Slot

The client may now choose the Pickup Slot in the customer's Cart (`GET shop/v3/SetDeliveryDate` with `deliveryDate`, `intervalStart` and `intervalEnd`). This supersedes the line in ADR 0005 and ADR 0006 that it never chooses a slot. Those ADRs grouped choosing a slot with placing an Order. But the chosen slot is part of the Cart: setting it reserves nothing ("kan blive fuldt booket forinden", in Bilka's own text), spends nothing and commits Bilka to nothing until the customer places the Order on the website. It is the step after "fill my Cart" that users want automated ("put my Cart on Thursday 16–18").

The rest of ADR 0005 stands. The client never chooses or changes the Pickup Point or Home Delivery address (`SetDeliveryAddress`, `SetPickupZone`), and never places, pays for or cancels an Order.

The client chooses only a slot that `AvailableDeliveryDates` lists as open for the Cart at that moment. It checks this first, because Bilka's answer to a slot it doesn't offer has never been captured. It then checks that Bilka's answer names the slot it asked for. Otherwise it raises, with Bilka's message when there is one.

## Considered Options

- **Keep slots read-only (ADR 0006):** safest, but it leaves the one remaining Cart setting a user does by hand before ordering.
- **Also allow choosing the Pickup Point:** it changes the Store, and with it the assortment, Prices and possibly the Cart Lines. It is left for its own decision.

## Consequences

- "Booking" a slot is the wrong word for this: choosing a slot does not hold it, and it can still sell out before the Order is placed. Docs and messages say "choose".
- Choosing is reversible only while the earlier slot is still open. A slot given up can sell out, so the CLI and Home Assistant say which slot they replaced.
- As with every write, it is checked by hand once against the maintainer's own Cart before a release; CI exercises only the fake backend.
- The CLI help text "Choose one on bilkatogo.dk" changes, and the "Out of Scope" list of the MCP server (#63) gains a candidate tool.
