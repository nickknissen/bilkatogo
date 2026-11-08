from datetime import timedelta
from zoneinfo import ZoneInfo

GIGYA_API_KEY = "3_tA6BbV434FQqN73HnUG1KA3qFv8KiG4OqLu9eWPh7sKRqRizH5Vfv5Larmgrb4I2"
GIGYA_BASE_URL = "https://accounts.eu1.gigya.com"
GIGYA_INVALID_LOGIN_ID = 403042
GIGYA_RATE_LIMIT = 403048
# Gigya's error codes from 500000 up are its own server errors.
GIGYA_SERVER_ERRORS = 500000
JWT_LIFETIME_SECONDS = 86400
JWT_RENEWAL_MARGIN = timedelta(minutes=5)
# The saved session's format; version 2 adds Gigya's login token (ADR 0002).
SESSION_VERSION = 2

API_HOST = "api.bilkatogo.dk"
API_BASE_URL = f"https://{API_HOST}/api"
WEB_PARAMS = {"u": "w"}
ORDER_HISTORY_PAGE_SIZE = 20
ORDER_HISTORY_PARAMS = {"muah": 1, "ordertype": "dibs,pgw"}
CART_PARAMS = {"extra": "deliveryAddress,deliveryDate"}
CURRENT_ORDER_SCAN_LIMIT = 20
# Several Cart changes go one after another, a pause apart, like a person clicking. A change
# that Bilka does not answer, or answers with HTTP 429 or 5xx, is retried after each delay;
# the quantity is absolute, so repeating it is safe.
CART_CHANGE_PAUSE_SECONDS = 0.25
CART_CHANGE_RETRY_DELAYS = (0.5, 1.0, 2.0)
# Order History can take well over httpx's default of 5 seconds to answer.
REQUEST_TIMEOUT_SECONDS = 30.0
BILKA_TIME_ZONE = ZoneInfo("Europe/Copenhagen")

ALGOLIA_APP_ID = "F9VBJLR1BK"
ALGOLIA_SEARCH_KEY = "1deaf41c87e729779f7695c00f190cc9"
ALGOLIA_QUERIES_URL = f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"
ALGOLIA_PRODUCT_INDEX = "prod_BILKATOGO_PRODUCTS"
SEARCH_LIMIT = 20
# Algolia returns at most 1000 hits per query.
PRODUCT_LOOKUP_LIMIT = 1000
