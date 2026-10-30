class BilkaToGoError(Exception):
    """Base class for all errors raised by the bilkatogo client."""


class InvalidCredentialsError(BilkaToGoError):
    """Bilka rejected the email or password. Ask the user for new credentials."""


class BilkaUnreachableError(BilkaToGoError):
    """Bilka could not be reached (connection error or timeout). Retry later."""


class UnknownPickupPointError(BilkaToGoError):
    """No Pickup Point has the given name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown Pickup Point {name!r}")
        self.name = name


class NoPickupPointError(BilkaToGoError):
    """No Pickup Point was named and none is chosen in the Cart."""


class OrderNotFoundError(BilkaToGoError):
    """Bilka has no Order with the given Order Number on this account."""

    def __init__(self, order_number: str) -> None:
        super().__init__(f"Order {order_number} was not found")
        self.order_number = order_number


class CartChangeRejectedError(BilkaToGoError):
    """Bilka refused a change to a Cart Line, or applied a different quantity than asked;
    the message is Bilka's.

    `applied` is None when Bilka refused the change outright or did not say which quantity it
    applied.
    """

    def __init__(
        self, message: str, article_number: str, requested: int, applied: int | None
    ) -> None:
        super().__init__(message)
        self.article_number = article_number
        self.requested = requested
        self.applied = applied


class UnexpectedResponseError(BilkaToGoError):
    """Bilka answered with something the client does not understand. Retry later."""
