# Recipes from the website's static pages

From #75 the library reads Bilka's Recipes and the weekly Madplan from bilkatogo.dk. There is no API for them: the website is a static Nuxt build, and each page under `/inspiration/opskrifter/` has its data in `/_nuxt/static/<build>/inspiration/opskrifter/<path>/payload.js`, a JavaScript object literal (unquoted keys, `void 0`) holding a tree of Magnolia CMS components. This is a third surface besides Bilka's shop API (ADR 0002) and Algolia (ADR 0004), and the most fragile: its format is whatever the website's build produces, and the build id changes on every deploy.

The reader reads the build id once from the HTML of `/inspiration/opskrifter/`. For a page it does not know, and for a payload under an old build alike, the website answers HTTP 200 with its HTML naming the current build (captured 2026-11-08). So HTML naming the build that was asked for means there is no such page, and HTML naming another build means a deploy: the reader takes that build and reads the page once more. A second change while reading one page, or HTML without a build, raises `WebsiteChangedError`. No other retries. At most 5 pages are read at once, since a category with sub-categories reads one or two pages per sub-category. The pages are public, so reading needs no login.

The parsing rules follow handle-mcp's recipe adapter (2026-11-06), reimplemented: cards come from a page's own grids and carousels (a Recipe's "Lignende opskrifter" is its parent's grid and is left out); the first card's page tells whether a category's cards are Recipes or sub-categories, two deep at most, and a card whose page is gone is skipped; a Recipe's text is the `Paragraph` HTML above "Det skal du bruge", split at `<h2>` headings (below it are similar Recipes and general tips); "4 personer" and "35 minutter" come from `FrontpageUsps`; the Recipe Products are `curatedSkuList`, digits only, since they go into a product search filter.

The module stays apart: `bilkatogo.recipes` is imported only by itself, the CLI's `recipes` commands and `BilkaToGoClient.get_recipe`, so a redesign of bilkatogo.dk breaks Recipes and nothing else. A Recipe's Products are priced through `get_products` and added through `add_items_to_cart`, so ADR 0005 holds: one pack each, Cart writes only, and the CLI asks before adding them.

## Consequences

- A website deploy can break Recipes at any time; `tests/test_recipes_live.py` (`-m live`) checks the real site and is run by hand before a release, not in CI.
- Recipes have no amounts per Product and no persons to scale, and the website has no recipe search; neither does the library.
- No Home Assistant action until the module has lived through a website deploy.
