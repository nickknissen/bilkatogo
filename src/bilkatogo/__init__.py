from importlib.metadata import PackageNotFoundError, version

from .client import BilkaToGoClient
from .const import REQUEST_TIMEOUT_SECONDS
from .exceptions import (
    BilkaToGoError,
    BilkaUnreachableError,
    CartChangeRejectedError,
    InvalidCredentialsError,
    NoPickupPointError,
    OrderNotFoundError,
    UnexpectedResponseError,
    UnknownPickupPointError,
)
from .models import (
    Cart,
    CartLine,
    MissingItem,
    Offer,
    Order,
    OrderDetails,
    OrderHistory,
    OrderLine,
    OrderStatus,
    PickupCode,
    PickupPoint,
    PickupSlot,
    Product,
    SearchSort,
    Substitution,
)
from .token_storage import FileTokenStorage, TokenStorage

try:
    __version__ = version("bilkatogo")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "BilkaToGoClient",
    "BilkaToGoError",
    "BilkaUnreachableError",
    "Cart",
    "CartChangeRejectedError",
    "CartLine",
    "FileTokenStorage",
    "InvalidCredentialsError",
    "MissingItem",
    "NoPickupPointError",
    "Offer",
    "Order",
    "OrderDetails",
    "OrderHistory",
    "OrderLine",
    "OrderNotFoundError",
    "OrderStatus",
    "PickupCode",
    "PickupPoint",
    "PickupSlot",
    "Product",
    "REQUEST_TIMEOUT_SECONDS",
    "SearchSort",
    "Substitution",
    "TokenStorage",
    "UnexpectedResponseError",
    "UnknownPickupPointError",
    "__version__",
]
