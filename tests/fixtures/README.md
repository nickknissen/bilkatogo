# Fixtures

Real Bilka responses, redacted (see `docs/agents/capturing.md`), except the synthetic ones below.

## Synthetic

- `order_details_missing.json`: a Missing Item with the invented field `unavailable_quantity`.
  To be replaced by a real capture (#27).
- `cart_home.json`: a Home Delivery Cart. No real capture exists yet, so it is
  `cart_logged_in.json` with the fields a Home Delivery Cart carries according to
  [handle-mcp](https://github.com/NJohansen/handle-mcp)'s hand-edited `cart_home.json`:
  `deliveryAddress.dawa_uid`, `recipient_name`, the home address, `minimum_buy` (50000),
  `nextAvailableDeliveryDate`, `hd_available` (`true`), and `stat.delivery_price` (3900),
  `minimum_left` (37300) and `minimum_left_text`. To be replaced by a real capture (#76).

  `deliveryAddress.hd_available` is `false` in both real pickup captures and `true` here; the
  top-level `hd_available` is `true` in both pickup captures, so it does not tell the modes
  apart. The library does not depend on either until a real Home Delivery capture shows what
  they mean.
