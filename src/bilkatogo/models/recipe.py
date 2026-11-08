from dataclasses import dataclass, field

from .base import BilkaDataClass
from .product import Product


@dataclass
class Recipe(BilkaDataClass):
    """A Recipe as a card on bilkatogo.dk: in the Madplan or a category."""

    id: str
    """The path below /inspiration/opskrifter/, e.g. "aftensmad/pizza/flammkuchen"."""
    name: str
    url: str
    group: str | None
    """The heading the card sat under, e.g. "Madplan til uge 46" or "Pita og wraps"."""


@dataclass
class RecipeSection(BilkaDataClass):
    """One part of a Recipe's method under its heading, e.g. "Fremgangsmåde" and its steps."""

    title: str | None
    steps: list[str]


@dataclass
class RecipeDetails(BilkaDataClass):
    """One Recipe's page: its facts, text and Recipe Products."""

    id: str
    name: str
    url: str
    persons: int | None
    """How many it is written for; None when the page gives no number of persons."""
    time: str | None
    """As written on the page, e.g. "35 min"."""
    image: str | None
    ingredients: list[str]
    method: list[RecipeSection]
    """The sections after the ingredients, each under its heading."""
    article_numbers: list[str]
    """The Recipe Products' Article Numbers, in page order, one pack each."""
    products: list[Product] = field(default_factory=list)
    """The Recipe Products at the Store, from `BilkaToGoClient.get_recipe`, in page order;
    those the Store does not sell are left out. Empty from `RecipeReader`."""
