class BilkaToGoError(Exception):
    """Base class for all errors raised by the bilkatogo client."""


class InvalidCredentialsError(BilkaToGoError):
    """Bilka rejected the email or password. Ask the user for new credentials."""


class BilkaUnreachableError(BilkaToGoError):
    """Bilka could not be reached (connection error or timeout). Retry later."""


class BilkaBusyError(BilkaToGoError):
    """Bilka or its login service is temporarily unavailable (rate limit or server error).
    Retry later; the credentials are not at fault."""

    def __init__(self, detail: str | None = None) -> None:
        cause = f" ({detail})" if detail else ""
        super().__init__(
            f"Bilka or its login service is temporarily unavailable{cause}; try again later"
        )


class UnknownPickupPointError(BilkaToGoError):
    """No Pickup Point has the given name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown Pickup Point {name!r}")
        self.name = name


class NoPickupPointError(BilkaToGoError):
    """No Pickup Point was named and the Cart has neither a Pickup Point nor a Home Delivery
    address."""


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


class WebsiteChangedError(BilkaToGoError):
    """bilkatogo.dk's recipe pages are not in the format the recipes module reads; only
    recipes are affected. Report it, with the bilkatogo version."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"bilkatogo.dk's recipe pages have changed ({detail})")


class UnknownRecipeError(BilkaToGoError):
    """bilkatogo.dk has no recipe at the given path."""

    def __init__(self, recipe: str, categories: list[str]) -> None:
        hint = f"; categories: {', '.join(categories)}" if categories else ""
        super().__init__(f"No recipe {recipe!r} on bilkatogo.dk{hint}")
        self.recipe = recipe
        self.categories = categories


class UnknownCategoryError(BilkaToGoError):
    """bilkatogo.dk has no recipe category at the given path."""

    def __init__(self, category: str, categories: list[str]) -> None:
        hint = f"; categories: {', '.join(categories)}" if categories else ""
        super().__init__(f"No recipe category {category!r} on bilkatogo.dk{hint}")
        self.category = category
        self.categories = categories


class UnexpectedResponseError(BilkaToGoError):
    """Bilka answered with something the client does not understand. Retry later."""
