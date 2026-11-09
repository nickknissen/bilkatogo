"""Home Assistant tests.

Home Assistant publishes `pytest-homeassistant` from 2026.11. CI's ha-test-oldest job tests
2026.10.0 with its predecessor, `pytest-homeassistant-custom-component`, so there the
modules these tests import are taken from that package under the new names. Remove this
once hacs.json requires 2026.11 or later.
"""

import importlib
import sys
from importlib.util import find_spec

if find_spec("pytest_homeassistant") is None:
    for name in ("", ".common", ".syrupy", ".typing", ".components.diagnostics"):
        old = importlib.import_module(f"pytest_homeassistant_custom_component{name}")
        sys.modules[f"pytest_homeassistant{name}"] = old
