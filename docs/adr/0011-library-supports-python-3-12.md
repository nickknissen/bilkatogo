# The library supports Python 3.12; the integration follows Home Assistant

Until 0.12.0 the whole package required Python 3.14.2, the version Home Assistant needs, and the code used 3.14-only syntax (unparenthesised multi-type `except`, annotations evaluated lazily). From 0.13.0 the library supports Python 3.12 and up (#96), so other clients can depend on it instead of keeping their own copy of Bilka's login, shop and Algolia calls: the Python MCP servers for BilkaToGo (handle-mcp and ugeplan-mcp require 3.12, stigvoss's 3.10) would otherwise have to raise their floor to 3.14 to use it. We chose that reach over the newest syntax. 3.12 still gets security fixes until October 2028. The Home Assistant integration and its tests keep Home Assistant's floor, through the `ha` dependency group's own `requires-python` in uv.

## Consequences

- Raising the floor again strands users on older Pythons, so it waits for a reason: 3.12's end of life, or a library feature that needs a newer Python.
- Code under `src/` must run on 3.12. ruff and ty check the syntax for that target, but not that an annotation evaluated at import names something defined then, so a module whose annotations name a class defined later or imported under `TYPE_CHECKING` needs `from __future__ import annotations` (`AGENTS.md`). CI and `scripts/check.py` run the tests on 3.12, and CI also on 3.13, 3.14 and 3.15.
- `custom_components/` may use anything Home Assistant's Python allows.
