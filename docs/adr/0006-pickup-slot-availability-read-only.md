# Pickup Slot availability, read-only

From #73 the client reads which Pickup Slots Bilka offers in the coming days (`GET shop/v3/AvailableDeliveryDates`, with the JWT and no parameter besides `u=w`), as the website does before the customer picks one. Bilka answers for whatever the Cart has, its Pickup Point or Home Delivery address; the call names neither, so the client also reads the Cart to say which. The chosen slot comes from the Cart too: the answer repeats it at its top level, but one capture there has `intervalStart` and `intervalEnd` with `deliveryDate: null`, and the Cart's `deliveryDate` is the glossary's source. ADR 0005's line stands: the client never chooses a slot, a Pickup Point or an address, and never sends `SetDeliveryDate`, `SetDeliveryAddress` or `SetPickupZone`. Showing availability spends nothing and commits Bilka to nothing; choosing a slot is part of placing an Order.

Bilka marks each day and each of its intervals with an `availability_bit`. Two captures show 1 for open, 2 for closed because the order deadline passed, 4 for sold out, 5 (1 | 4) for a day with open and sold-out intervals and 0 for a day without intervals, such as a past day or a Sunday at a Pickup Point. An interval that cannot be chosen also has `closed: 1`, and a sold-out one `closed_text: "udsolgt"`. The client reads a slot as open when it is not `closed` and has bit 1, as sold out when the day or the interval has bit 4 exactly or `closed_text` "udsolgt", and as closed otherwise. Bit 2 is seen only in the Home Delivery capture, taken from handle-mcp; bit 4 on a pickup interval is seen in the maintainer's pickup capture of 2026-11-08. Neither capture shows every combination, so the meanings are unverified beyond these.

A Home Delivery night slot is listed as 00:00 to 06:00 on its date and is delivered the night before ("natten til mandag"). The client keeps it as given; a slot whose end is at or before its start ends the next day.

## Consequences

- Home Assistant reads the Cart and the slots on every update, two requests more. A failure there leaves only the Next available pickup slot sensor unavailable; the Order update goes on.
- Slots for a Pickup Point other than the Cart's would need the Cart switched first, which is a write; they stay out of reach.
- Bilka has no price per slot; the Delivery Fee is the Cart's.
- `ship_before` and `ship_time` are not understood and stay in `_raw` only.
