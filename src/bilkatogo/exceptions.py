from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PickupSlot, SlotAvailability


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
    """No Pickup Point has the given name, or Bilka refused to move the Cart to it because it
    does not know it; `reason` is then Bilka's message. The Cart did not move."""

    def __init__(self, name: str, reason: str | None = None) -> None:
        super().__init__(f"Unknown Pickup Point {name!r}" + (f": {reason}" if reason else ""))
        self.name = name
        self.reason = reason


class NoHomeAddressError(BilkaToGoError):
    """Home Delivery can't be chosen: the client has not seen the account's Home Address yet,
    or Bilka no longer knows the one it remembers. Choose Home Delivery once on bilkatogo.dk,
    then the client remembers it. The Cart did not move."""

    def __init__(self, reason: str | None = None) -> None:
        cause = f" ({reason})" if reason else ""
        super().__init__(
            f"No Home Address is known for Home Delivery{cause}; choose Home Delivery once on "
            "bilkatogo.dk, then try again"
        )
        self.reason = reason


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


class PickupSlotUnavailableError(BilkaToGoError):
    """The Pickup Slot cannot be chosen for the Cart: Bilka does not offer it, lists it as
    closed or sold out, or refused it when asked. Nothing in the Cart changed.

    `availability` is how Bilka listed the slot, None when it does not list it or refused it
    itself; `refused` is True when Bilka refused it when asked, e.g. because it sold out in
    between; `reason` says why, in Bilka's words when it gave any.
    """

    def __init__(
        self,
        slot: PickupSlot,
        reason: str,
        availability: SlotAvailability | None = None,
        *,
        refused: bool = False,
    ) -> None:
        super().__init__(f"The Pickup Slot {slot} cannot be chosen: {reason}")
        self.slot = slot
        self.reason = reason
        self.availability = availability
        self.refused = refused


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
