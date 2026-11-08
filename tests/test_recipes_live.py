"""Reads the real bilkatogo.dk, whose recipe pages are Bilka's to change (ADR 0007).

Not run by default; before a release: `uv run pytest -m live tests/test_recipes_live.py`.
"""

import pytest

from bilkatogo.recipes import RecipeReader

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_the_real_madplan_and_its_first_recipe_are_readable() -> None:
    async with RecipeReader() as reader:
        week = await reader.madplan()
        recipe = await reader.recipe(week[0].id)

    assert len(week) == 7
    assert recipe.name
    assert recipe.ingredients
    assert recipe.method
    assert recipe.article_numbers
