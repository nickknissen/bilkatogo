# Fixtures

Real Bilka responses, redacted (see `docs/agents/capturing.md`), except the synthetic ones below.

## Synthetic

- `order_details_missing.json`: a Missing Item with the invented field `unavailable_quantity`.
  To be replaced by a real capture (#27).

## Notes on real captures

- `algolia_offers.json`: one hit, verbatim, from
  [handle-mcp](https://github.com/NJohansen/handle-mcp)'s `tests/fixtures/bilkatogo/algolia_offers.json`
  (live capture 2026-11-02, `isInOffer:1653`, a narrower `attributesToRetrieve` than ours). Bilka
  writes "Se Her" as the `offerDescription` of a leaflet Offer without a text of its own. The hit
  has no `isInAssortmentIn`, `isInOffer` or `inStockStore`, so a test that serves it adds them.

- `cart_home.json`: a Home Delivery Cart, captured 2026-11-08 (#76) right after switching the
  website Cart to Home Delivery by typing in a zip code, before a full address was entered:
  `missing_hd_address` is `true`, `street` and `address` are empty, `dawa_uid` is `null` and
  the zip code is in `temporary_zipcode`. `recipient_name` is what marks it as Home Delivery.
  `name` named another member of the household than `recipient_name`. `facility` is 8 (3 on
  both pickup captures). Bilka adds the Delivery Fee as a "Leveringsgebyr" line in the
  "Services" group, which is not a Cart Line.

  `deliveryAddress.hd_available` is `true` here and `false` in both pickup captures
  (`cart.json`, `cart_logged_in.json`); the top-level `hd_available` is `true` in all three, so
  it does not tell the modes apart. The library does not depend on either until a second Home
  Delivery capture, with a full address, confirms what they mean.
