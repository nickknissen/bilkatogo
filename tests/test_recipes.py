import httpx
import pytest

from bilkatogo import UnknownCategoryError, UnknownRecipeError, WebsiteChangedError
from bilkatogo.recipes import RecipeReader, category_path, js_object, recipe_path
from tests.fake_bilka import RECIPE_FIXTURES, RECIPES_BUILD, FakeBilka

SITE = "https://www.bilkatogo.dk/inspiration/opskrifter"


@pytest.fixture
async def reader(bilka: FakeBilka):
    async with bilka.client() as http, RecipeReader(httpx_client=http) as reader:
        yield reader


@pytest.mark.asyncio
async def test_the_madplan_is_this_weeks_seven_dinners_in_order(reader: RecipeReader) -> None:
    week = await reader.madplan()

    assert [recipe.name for recipe in week] == [
        "Cowboy toast",
        "Carbonara",
        "Asiatisk salat med pulled chicken",
        "Flammkuchen",
        "Kebabsalat",
        "Kikærtegryde",
        "Minestronesuppe",
    ]
    assert {recipe.group for recipe in week} == {"Madplan til uge 46"}
    assert (week[1].id, week[1].url) == (
        "aftensmad/pastaretter/carbonara",
        f"{SITE}/aftensmad/pastaretter/carbonara/",
    )


@pytest.mark.asyncio
async def test_a_category_with_recipes(reader: RecipeReader) -> None:
    found = await reader.category("aftensmad/danske-retter", limit=25)

    assert len(found) == 15
    assert found[2].name == "Karbonader"
    assert found[2].id == "aftensmad/danske-retter/karbonader"
    assert {recipe.group for recipe in found} == {"Dansk inspirerede retter"}


@pytest.mark.asyncio
async def test_a_category_showing_its_sub_categories_recipes_takes_one_from_each_in_turn(
    reader: RecipeReader,
) -> None:
    found = await reader.category("aftensmad/sandwiches-pita-og-wraps", limit=4)

    assert [(recipe.group, recipe.id.rsplit("/", 2)[-2]) for recipe in found] == [
        ("Pita og wraps", "pita"),
        ("Sandwiches", "sandwiches"),
        ("Pita og wraps", "pita"),
        ("Sandwiches", "sandwiches"),
    ]


@pytest.mark.asyncio
async def test_a_category_of_sub_categories_collects_their_recipes(
    reader: RecipeReader, bilka: FakeBilka
) -> None:
    # Aftensmad shows 15 sub-categories; the fake answers each with the Danish dishes.
    found = await reader.category("Aftensmad", limit=5)

    assert len(found) == 5
    assert all(recipe.url.startswith(f"{SITE}/aftensmad/") for recipe in found)
    read = [request.url.path for request in bilka.calls("website")]
    assert any(path.endswith("/aftensmad/suppe/payload.js") for path in read)


@pytest.mark.asyncio
async def test_a_sub_category_whose_page_is_gone_is_skipped(
    reader: RecipeReader, bilka: FakeBilka
) -> None:
    bilka.missing_recipe_pages.add("aftensmad/fiskeretter")

    found = await reader.category("aftensmad", limit=100)

    assert found
    assert not [recipe for recipe in found if recipe.id.startswith("aftensmad/fiskeretter/")]


@pytest.mark.asyncio
async def test_at_most_five_pages_are_read_at_once(reader: RecipeReader, bilka: FakeBilka) -> None:
    await reader.category("aftensmad")

    assert bilka.most_pages_in_flight == 5


@pytest.mark.asyncio
async def test_an_unknown_category_names_the_top_level_ones(reader: RecipeReader) -> None:
    with pytest.raises(UnknownCategoryError, match="'pandekager'.*aftensmad, frokost") as raised:
        await reader.category("pandekager")

    assert raised.value.categories[:3] == ["madplaner", "aftensmad", "frokost"]


@pytest.mark.parametrize(
    ("given", "path"),
    [
        ("Aftensmad", "aftensmad"),
        ("aftensmad/danske-retter", "aftensmad/danske-retter"),
        ("Børn i køkkenet", "boern-i-koekkenet"),
        (f"{SITE}/aftensmad/", "aftensmad"),
    ],
)
def test_a_category_by_name_or_path(given: str, path: str) -> None:
    assert category_path(given) == path


@pytest.mark.asyncio
async def test_the_build_is_read_once_from_the_recipes_page(
    reader: RecipeReader, bilka: FakeBilka
) -> None:
    await reader.madplan()
    await reader.recipe("aftensmad/pastaretter/carbonara")

    assert [request.url.path for request in bilka.calls("website")] == [
        "/inspiration/opskrifter/",
        "/_nuxt/static/1793920097/inspiration/opskrifter/madplaner/payload.js",
        "/_nuxt/static/1793920097/inspiration/opskrifter/aftensmad/pastaretter/carbonara/payload.js",
    ]


@pytest.mark.asyncio
async def test_after_a_deploy_the_page_is_read_again_under_the_new_build(
    reader: RecipeReader, bilka: FakeBilka
) -> None:
    await reader.madplan()
    bilka.build = "1800000000"

    week = await reader.madplan()

    assert len(week) == 7
    assert [request.url.path for request in bilka.calls("website")][-2:] == [
        "/_nuxt/static/1793920097/inspiration/opskrifter/madplaner/payload.js",
        "/_nuxt/static/1800000000/inspiration/opskrifter/madplaner/payload.js",
    ]


def website_html(build: str) -> str:
    html = (RECIPE_FIXTURES / "unknown-page.html").read_text(encoding="utf-8")
    return html.replace(RECIPES_BUILD, build)


@pytest.mark.asyncio
async def test_a_build_that_changes_twice_while_reading_a_page_is_a_website_change(
    reader: RecipeReader, bilka: FakeBilka
) -> None:
    await reader.madplan()
    bilka.script(
        "website",
        httpx.Response(200, text=website_html("1800000000")),
        httpx.Response(200, text=website_html("1800000001")),
    )

    with pytest.raises(WebsiteChangedError, match="build changed twice"):
        await reader.madplan()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [httpx.Response(200, text="<html>no build here</html>"), httpx.Response(404, text="")],
)
async def test_a_recipes_page_without_a_build_is_a_website_change(
    reader: RecipeReader, bilka: FakeBilka, answer: httpx.Response
) -> None:
    bilka.script("website", answer)

    with pytest.raises(WebsiteChangedError):
        await reader.madplan()


def test_a_payload_is_a_javascript_object() -> None:
    data = js_object(
        '__NUXT_JSONP__("/x", {data:[{page:{name:"say \\"hej\\"",hierarchy:void 0,'
        'path:"\\u002Fx",gridDisplay:false,n:3,$key:null}}],fetch:{}});'
    )

    assert data == {
        "data": [
            {
                "page": {
                    "name": 'say "hej"',
                    "hierarchy": None,
                    "path": "/x",
                    "gridDisplay": False,
                    "n": 3,
                    "$key": None,
                }
            }
        ],
        "fetch": {},
    }


@pytest.mark.parametrize("source", ["__NUXT_JSONP__()", '__NUXT_JSONP__("/x", {data: [1, }})'])
def test_an_unreadable_payload_is_a_website_change(source: str) -> None:
    with pytest.raises(WebsiteChangedError):
        js_object(source)


KARBONADER = f"{SITE}/aftensmad/danske-retter/karbonader/"


@pytest.mark.parametrize(
    "given",
    [
        KARBONADER,
        f"{KARBONADER}?utm_source=x#top",
        "/inspiration/opskrifter/aftensmad/danske-retter/karbonader",
        "aftensmad/danske-retter/karbonader",
    ],
)
def test_a_recipe_by_link_or_path(given: str) -> None:
    assert recipe_path(given) == "aftensmad/danske-retter/karbonader"


@pytest.mark.parametrize("given", ["../etc", "karbo nader", "", "https://www.bilkatogo.dk/"])
def test_anything_but_a_recipe_path_is_an_unknown_recipe(given: str) -> None:
    with pytest.raises(UnknownRecipeError):
        recipe_path(given)


@pytest.mark.asyncio
async def test_an_unknown_recipe_names_the_categories(reader: RecipeReader) -> None:
    with pytest.raises(UnknownRecipeError, match="'frokost/findes-ikke'.*aftensmad, frokost"):
        await reader.recipe("frokost/findes-ikke")


@pytest.mark.asyncio
async def test_a_recipe_or_category_that_is_not_a_path_names_the_categories(
    reader: RecipeReader,
) -> None:
    with pytest.raises(UnknownRecipeError, match="'karbo nader'.*aftensmad, frokost"):
        await reader.recipe("karbo nader")
    with pytest.raises(UnknownCategoryError, match="'../etc'.*aftensmad, frokost"):
        await reader.category("../etc")


@pytest.mark.asyncio
async def test_a_category_page_is_not_a_recipe(reader: RecipeReader) -> None:
    with pytest.raises(UnknownRecipeError, match="'aftensmad'"):
        await reader.recipe("aftensmad")


@pytest.mark.asyncio
async def test_a_recipe_has_its_facts_ingredients_method_and_article_numbers(
    reader: RecipeReader,
) -> None:
    recipe = await reader.recipe(KARBONADER)

    assert (recipe.id, recipe.name, recipe.url) == (
        "aftensmad/danske-retter/karbonader",
        "Karbonader",
        KARBONADER,
    )
    assert (recipe.persons, recipe.time) == (4, "35 min")
    assert recipe.image is not None
    assert recipe.image.startswith("https://digitalassets.sallinggroup.com/image/upload/")
    # The first banner's image.
    assert recipe.image.endswith("/next/jcr:26b30edc-769c-401f-aae1-cde78d53047a")
    # "Karbonader" above the first lines is a sub-heading in <strong>.
    assert recipe.ingredients[:3] == ["500 g hakket svin", "1 æg", "1 løg"]
    assert "Karbonader" not in recipe.ingredients
    assert [(section.title, len(section.steps)) for section in recipe.method] == [
        ("Fremgangsmåde", 11),
        ("Alternativer og justeringer", 2),
    ]
    assert recipe.method[0].steps[0].startswith("Bland farsen: løg snittes fint")
    # 16 in the page's list, one of them null.
    assert recipe.article_numbers[:3] == ["81498", "125887", "137133"]
    assert len(recipe.article_numbers) == 15
    assert recipe.products == []


@pytest.mark.asyncio
async def test_the_method_stops_above_the_products_and_similar_recipes(
    reader: RecipeReader,
) -> None:
    recipe = await reader.recipe("aftensmad/pastaretter/carbonara")

    # "Alternativer og justeringer" shares a paragraph with the method; "Tips og tricks til
    # carbonara" and "Hvad betyder carbonara?" are below "Det skal du bruge".
    assert [section.title for section in recipe.method] == [
        "Fremgangsmåde",
        "Alternativer og justeringer",
    ]
    assert recipe.time == "20 min"
    assert recipe.article_numbers == [
        "128254",
        "166050",
        "125823",
        "81422",
        "170956",
        "16868",
        "93861",
    ]
