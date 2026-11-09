# Personal data stays off shared surfaces

The client handles the customer's personal data: names (the customer's and, in a Home Address, other household members', #76), email, phone, the Pickup Code, the Home Address, Order Numbers, Bilka's account ids (`uid`, `tid`, customer id) and the session. Pickup Points and Stores are public. We show personal data only where the customer asked for it in their own session: return values, CLI output, and entity states inside their own Home Assistant (the Pickup Code and Order Status sensors). We never put it on a surface that is synced, shared or kept beyond that, because once it is in a synced calendar, a diagnostics file attached to a public issue or the repo's git history, it cannot be taken back, and the Pickup Code is what collects the Order. This was decided piece by piece (the v4 plan for the calendar on 2026-10-28, ADR 0009 for the Home Address, the capture rules) and is recorded here as one rule.

- **Calendar events** carry the Order Number as their description and the Pickup Point as their location: never the Pickup Code, and no location for Home Delivery. Calendars sync to phones and are shared with others, further than entity states.
- **The Home Address** is stricter than the rest: it stays out of Home Assistant entirely (states, attributes, action responses, the calendar). No automation needs it, and it names other people. The saved session keeps only its `id` and zip code (ADR 0009).
- **Diagnostics** redact credentials, tokens, the Pickup Code, addresses, account ids and the customer's name.
- **Captures and fixtures** go through `redact` (placeholders such as `Kunde Eksempel` and Order Numbers from `900000000001`) and are checked by hand before they are committed (`docs/agents/capturing.md`).

The cost is convenience: a calendar event can't be glanced at for the Pickup Code, and nothing in Home Assistant can use the Home Address. We accept that.

## Consequences

- A field Bilka adds is treated as personal until shown otherwise. `redact` and the diagnostics key off fields they know, so a new shape is read field by field before it is shared.
- A new surface decides which side of this line it is on before it ships. An MCP server (#63) sends its tool answers to the model's provider, which is beyond the customer's own session.
