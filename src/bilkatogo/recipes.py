"""Recipes and the weekly Madplan from bilkatogo.dk's static pages (ADR 0007).

Bilka has no recipe API: the website is a static Nuxt build, and each page under
/inspiration/opskrifter/ has its data in /_nuxt/static/<build>/<path>/payload.js, a tree of
Magnolia CMS components. The build id changes on every deploy. Read-only and best effort:
when the website changes, this module raises `WebsiteChangedError` and nothing else in the
library is affected. Keep every import of it inside this module, the CLI's `recipes`
commands and `BilkaToGoClient.get_recipe`.
"""

import asyncio
import html
import json
import re
from itertools import zip_longest
from types import TracebackType
from typing import Any, Self

import httpx

from .const import REQUEST_TIMEOUT_SECONDS
from .exceptions import (
    BilkaBusyError,
    BilkaUnreachableError,
    UnknownCategoryError,
    UnknownRecipeError,
    WebsiteChangedError,
)
from .models.recipe import Recipe, RecipeDetails, RecipeSection

SITE = "https://www.bilkatogo.dk"
ROOT = "/inspiration/opskrifter/"
IMAGES = (
    "https://digitalassets.sallinggroup.com/image/upload/w_1000,dpr_1,c_fill,q_auto,f_auto/"
    "t_DigitalAiLogo/external_content_providers/magnolia/next/"
)
MADPLAN = "madplaner"
# Pages read at once, to be polite to the website.
PAGES_AT_ONCE = 5
# Categories nest at most two deep: aftensmad → pizza → recipes.
CATEGORY_DEPTH = 2
PAYLOAD_START = "__NUXT_JSONP__"
BUILD = re.compile(r"/_nuxt/static/(\d+)")
PAGE_PATH = re.compile(r"[a-z0-9-]+(/[a-z0-9-]+)*")
IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
NUMBER = re.compile(r"\d+")
DANISH_LETTERS = (("æ", "ae"), ("ø", "oe"), ("å", "aa"))
INGREDIENTS_HEADING = "Ingrediens"


class _NoPage(Exception):
    """bilkatogo.dk has no page at that path."""


def recipe_path(recipe: str) -> str:
    """A Recipe's path below /inspiration/opskrifter/ from a link, an absolute path or the
    path itself: "https://www.bilkatogo.dk/inspiration/opskrifter/aftensmad/pizza/flammkuchen/"
    → "aftensmad/pizza/flammkuchen"."""
    path = _page_path(recipe)
    if path is None:
        raise UnknownRecipeError(recipe, [])
    return path


def category_path(category: str) -> str:
    """A category's path from its name or path: "Børn i køkkenet" → "boern-i-koekkenet",
    "aftensmad/danske-retter" stays."""
    text = category.strip().lower().replace(" ", "-")
    for letter, spelled in DANISH_LETTERS:
        text = text.replace(letter, spelled)
    path = _page_path(text)
    if path is None:
        raise UnknownCategoryError(category, [])
    return path


def _page_path(text: str) -> str | None:
    path = text.strip().split("?")[0].split("#")[0].lower()
    path = path.split("bilkatogo.dk", 1)[-1].strip("/")
    path = path.removeprefix(ROOT.strip("/")).strip("/")
    return path if PAGE_PATH.fullmatch(path) else None


def js_object(source: str) -> dict[str, Any]:
    """The object in a payload.js, `__NUXT_JSONP__("/path", {data: ...})`.

    It is a JavaScript object literal, not JSON: keys have no quotes and undefined is
    `void 0`. Its strings are already valid JSON strings.
    """
    try:
        text = source[source.index("{") : source.rindex("}") + 1]
    except ValueError:
        raise WebsiteChangedError("a recipe page has no data") from None
    parts: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == '"':
            end = i + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            parts.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("void 0", i):
            parts.append("null")
            i += len("void 0")
        elif match := IDENTIFIER.match(text, i):
            word = match.group()
            parts.append(word if word in ("true", "false", "null") else json.dumps(word))
            i = match.end()
        else:
            parts.append(char)
            i += 1
    try:
        data = json.loads("".join(parts))
    except ValueError:
        raise WebsiteChangedError("a recipe page's data is not readable") from None
    if not isinstance(data, dict):
        raise WebsiteChangedError("a recipe page's data is not an object")
    return data


class RecipeReader:
    """Reads the Madplan, a category's Recipes and one Recipe from bilkatogo.dk. The pages are
    public: no login. At most 5 pages are read at once."""

    def __init__(self, httpx_client: httpx.AsyncClient | None = None) -> None:
        self._owns_http = httpx_client is None
        self._http = httpx_client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._build: str | None = None
        self._build_read = asyncio.Lock()
        self._pages_at_once = asyncio.Semaphore(PAGES_AT_ONCE)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def madplan(self) -> list[Recipe]:
        """This week's Madplan: seven dinners, Monday to Sunday, in the website's order."""
        recipes = _cards(await self._page(MADPLAN))
        if not recipes:
            raise WebsiteChangedError("the Madplan shows no recipes")
        return recipes

    async def category(self, category: str, *, limit: int = 20) -> list[Recipe]:
        """The Recipes in a category, e.g. "aftensmad" or "aftensmad/danske-retter". A
        category with sub-categories gives theirs, one from each in turn."""
        try:
            path = category_path(category)
            pages_read: dict[str, dict[str, Any] | None] = {path: await self._page(path)}
        except UnknownCategoryError, _NoPage:
            raise UnknownCategoryError(category, await self.categories()) from None
        groups = await self._collect(path, CATEGORY_DEPTH, pages_read)
        mixed = [recipe for row in zip_longest(*groups) for recipe in row if recipe]
        return mixed[:limit]

    async def recipe(self, recipe: str) -> RecipeDetails:
        """One Recipe's page, without its Products' Prices (see `BilkaToGoClient.get_recipe`)."""
        try:
            path = recipe_path(recipe)
            page = await self._page(path)
        except UnknownRecipeError, _NoPage:
            raise UnknownRecipeError(recipe, await self.categories()) from None
        sections = _sections(page)
        ingredients = [
            line for heading, lines in sections if _is_ingredients(heading) for line in lines
        ]
        article_numbers = _article_numbers(page)
        if not ingredients and not article_numbers:  # a category page, not a Recipe
            raise UnknownRecipeError(recipe, await self.categories())
        persons, time = _facts(page)
        title = next((node.get("header") for node in _nodes(page, "Heading")), None)
        return RecipeDetails(
            id=path,
            name=(title or _name(page)).strip(),
            url=_url(path),
            persons=persons,
            time=time,
            image=_image(page),
            ingredients=ingredients,
            method=_method(sections),
            article_numbers=article_numbers,
        )

    async def categories(self) -> list[str]:
        """The top-level categories, e.g. "aftensmad" and "frokost"."""
        paths = (card.id for card in _cards(await self._page("")))
        return list(dict.fromkeys(path for path in paths if "/" not in path))

    async def _collect(
        self, path: str, depth: int, pages_read: dict[str, dict[str, Any] | None]
    ) -> list[list[Recipe]]:
        """The Recipes below `path`, per group. The first card's page tells whether the cards
        are Recipes or sub-categories; pages are kept in `pages_read` to be read only once."""
        page = await self._page_if_there(path, pages_read)
        cards = _cards(page) if page is not None else []
        if not cards:
            return []
        first_card = await self._page_if_there(cards[0].id, pages_read) if depth else None
        if first_card is None or _is_recipe(first_card):
            groups: dict[str | None, list[Recipe]] = {}
            for card in cards:
                groups.setdefault(card.group, []).append(card)
            return list(groups.values())
        per_sub_category = await asyncio.gather(
            *(self._collect(card.id, depth - 1, pages_read) for card in cards)
        )
        return [group for groups in per_sub_category for group in groups]

    async def _page_if_there(
        self, path: str, pages_read: dict[str, dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        """A page, or None when the website no longer has it: a card can outlive its page."""
        if path not in pages_read:
            try:
                pages_read[path] = await self._page(path)
            except _NoPage:
                pages_read[path] = None
        return pages_read[path]

    async def _page(self, path: str) -> dict[str, Any]:
        """A page's data. The website answers its HTML, naming the current build, both for a
        page it does not know and for a payload under an old build: an old build is replaced
        and the page read once more."""
        async with self._pages_at_once:
            build = self._build or await self._read_build()
            for _ in range(2):
                text = await self._get(
                    f"{SITE}/_nuxt/static/{build}{(ROOT + path).rstrip('/')}/payload.js"
                )
                if text.startswith(PAYLOAD_START):
                    return _page_data(text)
                current = _build_in(text)
                if current is None:
                    raise WebsiteChangedError("a recipe page answered without a build")
                if current == build:
                    raise _NoPage(path)
                build = self._build = current
            raise WebsiteChangedError("the website's build changed twice while reading a page")

    async def _read_build(self) -> str:
        async with self._build_read:
            if self._build is None:
                build = _build_in(await self._get(f"{SITE}{ROOT}"))
                if build is None:
                    raise WebsiteChangedError("the recipes page names no build")
                self._build = build
            return self._build

    async def _get(self, url: str) -> str:
        try:
            response = await self._http.get(url)
        except httpx.TransportError as err:
            raise BilkaUnreachableError(f"Could not reach {httpx.URL(url).host}: {err}") from err
        if response.status_code == 429 or response.status_code >= 500:
            raise BilkaBusyError(f"bilkatogo.dk answered HTTP {response.status_code}")
        if response.status_code != 200:
            raise WebsiteChangedError(f"a recipe page answered HTTP {response.status_code}")
        return response.text


def _build_in(text: str) -> str | None:
    match = BUILD.search(text)
    return match.group(1) if match else None


def _page_data(text: str) -> dict[str, Any]:
    data = js_object(text).get("data")
    page = data[0].get("page") if isinstance(data, list) and data and data[0] else None
    if not isinstance(page, dict):
        raise WebsiteChangedError("a recipe page has no page data")
    return page


def _nodes(node: Any, *kinds: str) -> list[dict[str, Any]]:
    """Every Magnolia component of these kinds, in page order."""
    found = []
    if isinstance(node, dict):
        if _kind(node) in kinds:
            found.append(node)
        for value in node.values():
            found += _nodes(value, *kinds)
    elif isinstance(node, list):
        for value in node:
            found += _nodes(value, *kinds)
    return found


def _kind(node: dict[str, Any]) -> str:
    return str(node.get("componentType") or "").removeprefix("MagnoliaComponents")


def _cards(page: dict[str, Any]) -> list[Recipe]:
    """The pages a page shows as cards, in page order: the grids of its own or a
    sub-category's pages, and carousels under a heading. A card is a Recipe or a category
    (see `_collect`). A Recipe's "Lignende opskrifter" is its parent's grid, so it is left
    out."""
    own = str(page.get("path") or "").removeprefix("/bilka-to-go").rstrip("/") + "/"
    group: str | None = None
    found: dict[str, tuple[str, str | None]] = {}
    for node in _nodes(page, "Heading", "ContentCard", "CarouselContentCard"):
        kind = _kind(node)
        if kind == "Heading":
            group = str(node.get("header") or "").strip() or group
        elif kind == "ContentCard":
            grid = _object(node.get("hierarchy"))
            if str(grid.get("path") or "").startswith(own):
                for child in map(_object, grid.get("children") or []):
                    found.setdefault(str(child.get("path") or ""), (_name(child), _name(grid)))
        else:
            for card in map(_object, node.get("contentCards") or []):
                target = _object(card.get("page"))
                name = str(card.get("header") or _name(target)).strip()
                found.setdefault(str(target.get("path") or ""), (name, group))
    return [
        _recipe(path.removeprefix(ROOT).strip("/"), name, card_group)
        for path, (name, card_group) in found.items()
        if path.startswith(ROOT) and path != ROOT
    ]


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _name(page: dict[str, Any]) -> str:
    return str(page.get("displayName") or page.get("title") or "").strip()


def _url(path: str) -> str:
    return f"{SITE}{ROOT}{path}/"


def _recipe(path: str, name: str, group: str | None) -> Recipe:
    return Recipe(id=path, name=name, url=_url(path), group=group)


def _is_recipe(page: dict[str, Any]) -> bool:
    return any(_is_ingredients(heading) for heading, _ in _sections(page))


def _is_ingredients(heading: str) -> bool:
    """ "Ingredienser", or "Ingredienser til ..." for one part of a Recipe."""
    return heading.startswith(INGREDIENTS_HEADING)


def _text(fragment: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _sections(page: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """The Recipe's text as (heading, lines) per `<h2>`, from the paragraphs above "Det skal
    du bruge" (the Products); below it are similar Recipes and general tips."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for node in _nodes(page, "Paragraph", "CuratedSlider"):
        if _kind(node) == "CuratedSlider":
            break
        content = str(node.get("htmlContent") or "")
        for part in re.split(r"(<h2[^>]*>.*?</h2>)", content, flags=re.DOTALL):
            if part.startswith("<h2"):
                sections.append((_text(part), []))
            else:
                sections[-1][1].extend(_lines(part))
    return sections


def _lines(fragment: str) -> list[str]:
    """List items, or else the lines of each paragraph; a `<strong>` line is a sub-heading and
    a paragraph in `<em>` a note, both left out."""
    if "<li" in fragment:
        lines = re.findall(r"<li[^>]*>(.*?)</li>", fragment, flags=re.DOTALL)
    else:
        lines = [
            line
            for paragraph in re.findall(r"<p>(.*?)</p>", fragment, flags=re.DOTALL)
            if not paragraph.lstrip().startswith("<em>")
            for line in re.split(r"<br\s*/?>", paragraph)
            if not re.fullmatch(r"\s*<strong>.*</strong>\s*", line, flags=re.DOTALL)
        ]
    return [text for line in lines if (text := _text(line))]


def _method(sections: list[tuple[str, list[str]]]) -> list[RecipeSection]:
    """The sections after the first ingredients, other than more ingredients."""
    start = next(
        (i for i, (heading, _) in enumerate(sections) if _is_ingredients(heading)),
        None,
    )
    if start is None:
        return []
    return [
        RecipeSection(title=heading or None, steps=lines)
        for heading, lines in sections[start + 1 :]
        if lines and not _is_ingredients(heading)
    ]


def _article_numbers(page: dict[str, Any]) -> list[str]:
    """The Recipe Products' Article Numbers, in page order; anything but digits is left out,
    since they go into a product search filter."""
    numbers = [
        str(number)
        for slider in _nodes(page, "CuratedSlider")
        for number in (slider.get("sourceProperties") or {}).get("curatedSkuList") or []
        if isinstance(number, str) and number.isascii() and number.isdigit()
    ]
    return list(dict.fromkeys(numbers))


def _facts(page: dict[str, Any]) -> tuple[int | None, str | None]:
    """ "4 personer" and "35 minutter" from the row of facts under the title."""
    persons = time = None
    for facts in _nodes(page, "FrontpageUsps"):
        for fact in facts.get("contentElements") or []:
            title = str(fact.get("title") or "").strip().lower()
            if title.endswith("personer") and (number := NUMBER.search(title)):
                persons = int(number.group())
            elif "minut" in title or "time" in title:  # "35 minutter", "2,5 timer"
                time = title.replace("minutter", "min").replace("minut", "min")
    return persons, time


def _image(page: dict[str, Any]) -> str | None:
    banner = next(iter(_nodes(page, "Banner")), {})
    jcr = str(banner.get("imageJCR") or "")
    return f"{IMAGES}{jcr}" if jcr.startswith("jcr:") else None
