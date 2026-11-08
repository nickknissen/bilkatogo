from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .base import BilkaDataClass
from .money import kroner


@dataclass
class Offer(BilkaDataClass):
    label: str
    price: Decimal
    unit_price: Decimal | None
    ends: date | None
    limit_per_customer: int | None


@dataclass
class Product(BilkaDataClass):
    article_number: str
    name: str
    brand: str
    store_id: str
    price: Decimal
    unit_price: Decimal | None
    unit: str | None
    in_stock: bool
    offer: Offer | None
    multibuy_offer: str | None
    ean: str | None
    """The barcode number, Bilka's `gtin`."""
    size: str | None
    """How much one unit holds, as the website writes it, e.g. "500 g" or "1,5 l"."""
    category: str | None
    """Bilka's category path and product type, e.g. "Frugt & grønt > Frugt > Bananer"."""
    _raw: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_hit(cls, hit: dict[str, Any], store_id: str) -> Product | None:
        store = (hit.get("storeData") or {}).get(store_id)
        if not store:
            return None
        unit = (store.get("unitsOfMeasurePriceUnit") or "").rstrip(".").lower()
        label = store.get("offerDescription") or ""
        multibuy = bool(store.get("multipromo"))
        offer = None
        if label and not multibuy:
            offer = Offer(
                label=_offer_label(label),
                price=kroner(store.get("price")),
                unit_price=kroner(store.get("unitsOfMeasureOfferPrice")) if unit else None,
                ends=_date(store.get("offerUntil")),
                limit_per_customer=_whole_number(store.get("offerMax")),
            )
        price = (store.get("beforePrice") or hit.get("price")) if offer else store.get("price")
        return cls(
            _raw=hit,
            article_number=str(hit["objectID"]),
            name=hit.get("name") or "",
            brand=hit.get("brand") or "",
            store_id=store_id,
            price=kroner(price),
            unit_price=kroner(store.get("unitsOfMeasurePrice")) if unit else None,
            unit=unit or None,
            in_stock=bool(store.get("inStock")),
            offer=offer,
            multibuy_offer=label if multibuy and label else None,
            ean=hit.get("gtin") or None,
            size=size_of(hit),
            category=" > ".join(
                part for part in (hit.get("deepestCategoryPath"), hit.get("productType")) if part
            )
            or None,
        )


def size_of(product: dict[str, Any]) -> str | None:
    """A Product's Size as the website writes it, e.g. "750 g" or "1,5 l", from Bilka's
    `units` and `unitsOfMeasure`; None when either is missing."""
    unit = product.get("unitsOfMeasure") or ""
    try:
        units = Decimal(str(product.get("units") or 0))
    except ArithmeticError:
        return None
    if not unit or not units.is_finite() or units <= 0:
        return None
    return f"{units.normalize():f}".replace(".", ",") + f" {unit}"


def _offer_label(label: str) -> str:
    """Bilka's label for an Offer; "Se Her", its placeholder for a leaflet Offer without a text
    of its own, becomes "Tilbud"."""
    return "Tilbud" if label.strip().casefold() == "se her" else label


def product_attributes(store_id: str) -> list[str]:
    """The Algolia attributes `Product.from_hit` reads for a Store."""
    return [
        "name",
        "brand",
        "price",
        "gtin",
        "units",
        "unitsOfMeasure",
        "productType",
        "deepestCategoryPath",
        f"storeData.{store_id}",
    ]


class SearchSort(StrEnum):
    """How `search()` orders the Products it found."""

    RELEVANCE = "relevance"
    """Bilka's own order, best match first."""
    PRICE = "price"
    """Cheapest first, by the Offer Price where there is an Offer."""
    UNIT_PRICE = "unit_price"
    """Cheapest Unit Price first, by the Offer's where there is one; Products without one last.
    Units are not converted, so compare Products sold by the same unit."""


def sort_products(products: list[Product], sort: SearchSort) -> list[Product]:
    """Order Products for `search()`; ties keep Bilka's relevance order."""
    if sort is SearchSort.PRICE:
        return sorted(products, key=_price_paid)
    if sort is SearchSort.UNIT_PRICE:
        return sorted(products, key=_unit_price_paid)
    return products


def _price_paid(product: Product) -> Decimal:
    return product.offer.price if product.offer else product.price


def _unit_price_paid(product: Product) -> tuple[bool, Decimal]:
    unit_price = product.offer.unit_price if product.offer else None
    unit_price = unit_price if unit_price is not None else product.unit_price
    return (unit_price is None, unit_price or Decimal(0))


def _whole_number(value: Any) -> int | None:
    try:
        return int(value) or None
    except TypeError, ValueError:
        return None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
