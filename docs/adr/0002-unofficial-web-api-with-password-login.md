# Unofficial web API with email and password login

Bilka offers no customer API (the Salling Group developer portal only exposes stores, jobs and offers), so we call the same backend the bilkatogo.dk web app uses: Gigya `accounts.login` → `accounts.getJWT` (24 h), then `v7/OrderHistory` and `v7/OrderDetails` with the JWT in the `jwt_token` header. The website also calls `POST api.bilkatogo.dk/api/auth/LoginJWT`; the shop does not need it (checked live on 2026-11-02, reads and Cart writes) and it replaces the Cart (ADR 0005), so the client skips it and sends no cookies of its own. We store the customer's email and password and log in again on our own, instead of having them paste the login token from the browser, because a pasted token expires and would break status tracking without the customer noticing.

## Consequences

- The API is undocumented and can change without notice; parsing must tolerate unknown fields and status codes.
- Automated access may conflict with Bilka's terms; the user accepted this risk.
- Polling is kept adaptive and low-frequency so the traffic looks like a person checking their order.

## 2026-11-08: the session holds Gigya's login token (#74)

The saved session now also holds the login token (`sessionInfo.cookieValue`) from `accounts.login`, alongside the JWT, as session format version 2. When the JWT runs out the client asks `accounts.getJWT` for a new one with that token, before falling back to the password. So a password is needed to log in once, not every 24 hours: the CLI and a future MCP server (#63) can run on the saved session alone. This is not the pasted token rejected above: the client gets it from its own login and still renews on its own.

The password is still accepted, and it is still the only way back when Gigya refuses the token (an `errorCode` on `getJWT`): the client drops the token and logs in with the password, or, without one, raises `InvalidCredentialsError` asking for a new login. A busy or unreachable Gigya leaves the token in place. When the shop rejects a JWT the client logs in with the password, not with the token. Home Assistant keeps the password in its config entry, so for its users nothing changes.

The token's lifetime is not documented for this site. handle-mcp has renewed from it since 2026-11-05 (its ADR 0003). Observed here: to be recorded after day 2 and day 8 of a session started with `bilkatogo login`.

- The session file now works like the password for as long as the token lives; it stays readable only by the user (0600, not on Windows) and the Home Assistant store is private.
- Sessions saved before version 2 keep working until their JWT runs out, then need the password once.
