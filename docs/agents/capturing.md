# Capturing Bilka responses

A capture is a real Bilka response saved as a fixture in `tests/fixtures/`, replacing a guessed shape. Every committed capture is redacted.

## Picking the route

1. **Read-only calls (`Cart`, `OrderHistory`, `OrderDetails`)**: use the library's own session with `bilkatogo order <number> --raw --redact` and `bilkatogo cart --raw --redact`. Check the output for anything personal the redaction missed before committing it (a home delivery address is detected by guess). The Cart belongs to the account, so the library reads the same Cart the website shows.
2. **Anything the customer does on the website (Cart changes, warnings, rejections)**: the maintainer acts in the Chrome DevTools MCP browser while logged in; the agent reads the network log afterwards. The agent never types the password and only sends writes the maintainer has confirmed.

## Live checks

When the library and the website disagree, or before any write against the maintainer's account:

1. **Diff the requests first.** Put the browser's request next to the library's: URL and params, headers, every cookie sent, the body, and the `Set-Cookie` in the answer. Test a theory only after that diff. The website's JS bundles (loaded by bilkatogo.dk) are the next place to look for how the website calls an endpoint.
2. **One client writing.** Close every other BilkaToGo client (browser windows, the Android app) before a write, and use a fresh isolated browser context logged into the right account. A second client can undo a change minutes later.
3. **Read uncached.** Add `without_cache=1` to `shop/v6/Cart` when checking whether a change stuck; the website does the same after logging in.

### How Bilka's Cart and login behave

Found in the live check for #26 (2026-10-27):

- `auth/LoginJWT` replaces the account's Cart with the Cart stored under the `cart` cookie the login presents. It is the guest-Cart transfer (fill a Cart, log in, it moves to the account), applied also to `cart` cookies handed out while logged in. It replaces, it does not merge.
- Bilka sets `cart={"cartID": …}` itself: HttpOnly, 7 days, a new `cartID` on every `LoginJWT` (`cart=deleted` for the presented one first). Reads and `ChangeLineCount` leave it alone.
- So a browser or app that logs in again brings back the Cart as it was at its previous login. A login without a `cart` cookie, or with a fresh guest Cart that is empty, leaves the Cart alone. The library never calls `LoginJWT` (see below).
- Choosing a Pickup Point sends `shop/v6/SetDeliveryAddress?id=…&zipcode=…` and adds a `pz={"id":…}` cookie to later requests.
- Unknown: whether a `cartID` answered with `cart=deleted` still restores, and how long Bilka keeps a stored Cart.

Found on 2026-11-02:

- A shop call without a valid JWT (none, a bad signature, or expired) answers HTTP 200 with `uid: -1`, never 401. `OrderHistory` answers `{"code": "ACCESS_DENIED", "msg": "Metoden kræver login", "eid": 401, "uid": -1}`, `OrderDetails` `{"code": null, "msg": "Ordren kunne ikke findes.", "eid": 404, "uid": -1}`, and `Cart` and `AvailableDeliveryAddresses` their normal guest answer. Logged in, `uid` is the customer's id.
- A JWT fresh from Gigya's `accounts.getJWT`, sent as `jwt_token` with no cookies and without ever calling `auth/LoginJWT`, reads the account's Cart and Order History, and its `ChangeLineCount` changes the account's Cart (0 → 1 → 0 on an empty Cart, read back uncached). `ChangeLineCount` answers with a `Set-Cookie` for `cart` and `PHPSESSID`; the shop does not need them back. So the library relies on the JWT alone and stores no cookies with its session (httpx may still send back cookies Bilka set within one client's lifetime).

## Reading the browser's network log

- List with `list_network_requests` filtered to `resourceTypes: ["xhr", "fetch"]` and a `pageSize` of 20 or less, paging from the end; the unfiltered log runs to 90k characters of analytics, and even the filtered one carries long analytics and map-tile URLs.
- Fetch every request with `get_network_request` and both `requestFilePath` and `responseFilePath` outside the repo, then inspect the saved files with a script. The tool prints the request headers inline, including `jwt_token` and cookies, so fetch only the requests you need and ask the maintainer to log out of the browser when done.
- Read the page with `take_snapshot` and a `filePath`, then grep the file. `wait_for` and `click` with `includeSnapshot` print the whole page inline.

## Redaction

Replace before committing: name, email, address, phone, Pickup Code, real Order Numbers (use `9000000000xx`), and the customer ids `uid` (use `900001`) and `tid` (use `00000000-0000-0000-0000-000000000000`). Pickup Points and Stores are public and stay. Grep the result for the maintainer's name and email before committing.

`redact` keys off fields it knows, so a capture of a new shape can carry personal data it misses. The first Home Delivery Cart (#76) had another household member's name in `deliveryAddress.name`, unlike `recipient_name`, and the typed-in zip code in `temporary_zipcode`; `redact` now replaces both in a home address. Write a capture outside the repo first, list every field of the parts that changed (`deliveryAddress`, `customer`, free text) and grep for the names of everyone in the household before copying it into `tests/fixtures/`.

## Finding a Product with a limit per customer

Query Algolia (constants in `src/bilkatogo/const.py`) with `isInAssortmentIn:<sid> AND nonsearchable:false` and check `storeData[<sid>].offerMax > 0`. Algolia returns at most 1,000 hits per query, so an empty result over the whole assortment is not proof; split the query by category or text.
