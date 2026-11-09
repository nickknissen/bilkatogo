# Fixtures

Real Bilka responses, redacted (see `docs/agents/capturing.md`), except the synthetic ones below.

## Synthetic

- `order_details_missing.json`: a Missing Item with the invented field `unavailable_quantity`.
  To be replaced by a real capture (#27).

## Notes on real captures

- `recipes/`: bilkatogo.dk's public Recipe pages, captured 2026-11-08 without logging in, website
  build `1793920097` (the same build handle-mcp captured on 2026-11-06). `<name>.payload.js` is
  `GET /_nuxt/static/1793920097/inspiration/opskrifter/<path>/payload.js` as received, for `""`
  (`opskrifter`), `madplaner` (week 46), `aftensmad`, `aftensmad/danske-retter`,
  `aftensmad/sandwiches-pita-og-wraps`, `aftensmad/danske-retter/karbonader` and
  `aftensmad/pastaretter/carbonara`. `opskrifter.html` is an excerpt of
  `GET /inspiration/opskrifter/`: only the tags naming the build. `unknown-page.html` is what
  the site answers, byte for byte the same, both for a payload under an unknown build
  (`/_nuxt/static/1000000000/...`) and for a page it does not have
  (`.../aftensmad/findes-ikke/payload.js`): HTTP 200, the website's HTML naming the current
  build in `staticAssetsBase`. The pages name the Recipes' author ("Julie Bruun"); kept, it is
  public.

- `available_delivery_dates_pickup.json`: `GET shop/v3/AvailableDeliveryDates` through the
  library's session on Sunday 2026-11-08 at about 20:00, the Cart at Bilka Vejle with the slot
  Wed 11/11 14:00-16:00 chosen (#73). Redacted with `redact` and trimmed to Sun 8 to Tue 10.
  `availability_bit` seen: 0 on Sunday (today, no intervals, `closed_text` empty; the past
  days and the next Sunday were the same), 5 on Monday, whose 08:00-10:00 is 4, `closed: 1`,
  `closed_text: "udsolgt"` and the rest 1 with `closed: 0`, and 1 on Tuesday with every
  interval 1. No bit 2: the first slot was the next morning. `eid` is -1, not a personal id.
  Each interval's `info` is only the order deadline sentence. The website was not looked at
  during the capture.

- `available_delivery_dates_home.json`: the Home Delivery answer from
  [handle-mcp](https://github.com/NJohansen/handle-mcp)'s
  `tests/fixtures/bilkatogo/available_delivery_dates.json` (live capture 2026-11-02, trimmed to
  three days there), with its `"X"` ids replaced by our `uid` and `tid` placeholders and `eid`
  -1. Mon is closed past its deadline (day and interval bit 2, `closed: 1`), Tue sold out
  (`closed_text` "udsolgt", no intervals), Wed open; one night slot, 00:00-06:00, a day. It
  has `intervalStart`/`intervalEnd` but `deliveryDate: null`, and a `msg`. To be replaced by
  the maintainer's own Home Delivery capture.

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
