# Quality scale plan: Platinum on HACS, then core

Researched on 2026-10-28 against the rule list in Home Assistant core's `script/hassfest/quality_scale.py` (54 rules: 20 Bronze, 10 Silver, 21 Gold, 3 Platinum) and the rule pages on contributors.home-assistant.io. Measured on `main` at v0.4.0: `scripts/ha-test.sh` passes 98 tests at 99% coverage, `mypy --strict` on the integration finds one error, and HA core's ruff config finds 63 findings.

## Where we stand

The integration already meets nearly every rule in code. The gaps are one CI failure, the user documentation, a few small code changes and a `quality_scale.yaml` that records the result.

### Fixed: hassfest failed for v0.4.0

CI's `hassfest` job failed for v0.4.0 because the calendar's event description held placeholders in an entity state translation. b26f10f builds the description from existing labels instead, and CI passes again.

The calendar then still read its texts through `self.platform_data.platform_translations`, using fake `state_attributes` keys, which core reviewers would not accept. No core calendar translates its event text; they use the service's own data or fixed text. So the events now use Bilka's data only: the summary "BilkaToGo", the Order Number as the description and the Pickup Point as the location.

### Rule by rule

`done` means met today. `todo` means work is listed below. `exempt` gives the reason that goes into `quality_scale.yaml`.

| Tier | Rule | Status | Note |
|---|---|---|---|
| 🥉 | action-setup | done | Registered in `async_setup`; `_entry` raises `ServiceValidationError` for an unloaded entry |
| 🥉 | appropriate-polling | done | Adaptive 60 min / 5 min (ADR 0002) |
| 🥉 | brands | done (HACS) / todo (core) | `brand/` folder serves HACS; core needs a PR to home-assistant/brands |
| 🥉 | common-modules | done | `coordinator.py`, `entity.py` |
| 🥉 | config-flow | done | Selectors and `data_description` |
| 🥉 | config-flow-test-coverage | done | 100% |
| 🥉 | dependency-transparency | done | MIT, PyPI sdist and wheel from trusted publishing, tagged releases |
| 🥉 | docs-actions | done | README "Actions" |
| 🥉 | docs-conditions | exempt | No integration conditions |
| 🥉 | docs-triggers | exempt | No integration triggers (events go on the bus) |
| 🥉 | docs-high-level-description | todo | One paragraph on what BilkaToGo is |
| 🥉 | docs-installation-instructions | done | |
| 🥉 | docs-removal-instructions | todo | Missing |
| 🥉 | entity-event-setup | exempt | Entities subscribe to nothing; the coordinator handles updates |
| 🥉 | entity-unique-id | done | |
| 🥉 | has-entity-name | done | |
| 🥉 | runtime-data | done | Typed `BilkaToGoConfigEntry` |
| 🥉 | test-before-configure | done | Logs in during the flow |
| 🥉 | test-before-setup | done | `async_config_entry_first_refresh` |
| 🥉 | unique-config-entry | done | Lower-cased email |
| 🥈 | action-exceptions | done | |
| 🥈 | config-entry-unloading | done | |
| 🥈 | docs-configuration-parameters | exempt | No options flow |
| 🥈 | docs-installation-parameters | todo | Describe the email and password fields explicitly |
| 🥈 | entity-unavailable | done | `CoordinatorEntity` |
| 🥈 | integration-owner | done | `@nickknissen` |
| 🥈 | log-when-unavailable | done | Coordinator logs once |
| 🥈 | parallel-updates | done | `PARALLEL_UPDATES = 0` on every platform |
| 🥈 | reauthentication-flow | done | |
| 🥈 | test-coverage | done | 99% (`diagnostics.py:56` uncovered) |
| 🥇 | devices | todo | Add `configuration_url="https://www.bilkatogo.dk"` |
| 🥇 | diagnostics | done | Redacts personal data; snapshot test |
| 🥇 | discovery | exempt | Cloud account, nothing to discover |
| 🥇 | discovery-update-info | exempt | Same |
| 🥇 | docs-data-update | done | Polling is documented |
| 🥇 | docs-examples | done | Script and two automations; add a dashboard card for the Pickup Code |
| 🥇 | docs-known-limitations | todo | Unofficial API, Denmark only, one Current Order, Home Delivery address left out |
| 🥇 | docs-supported-devices | todo | One line: any bilkatogo.dk account, pickup and home delivery |
| 🥇 | docs-supported-functions | todo | The entity table is missing the calendar and Ready for pickup |
| 🥇 | docs-troubleshooting | todo | Reauth, "Unknown" status, empty search (no Pickup Point), enabling debug logs |
| 🥇 | docs-use-cases | todo | Short list |
| 🥇 | dynamic-devices | exempt | One service device per config entry |
| 🥇 | entity-category | exempt | All entities are primary |
| 🥇 | entity-device-class | done | ENUM, MONETARY, TIMESTAMP where they fit |
| 🥇 | entity-disabled-by-default | exempt | No noisy or rarely used entity |
| 🥇 | entity-translations | done | |
| 🥇 | exception-translations | done | |
| 🥇 | icon-translations | todo | Add state icons for Ready for pickup and the calendar; drop `order_total` and `pickup_slot` overrides that repeat their device-class icon |
| 🥇 | reconfiguration-flow | exempt | Only credentials, handled by reauth; another email is another account |
| 🥇 | repair-issues | exempt | Nothing the user can fix besides the password (reauth) |
| 🥇 | stale-devices | exempt | One service device tied to the config entry |
| 🏆 | async-dependency | done | httpx `AsyncClient` |
| 🏆 | inject-websession | done | `create_async_httpx_client` (own client because Bilka uses cookies); HA closes it on shutdown and forbids closing it earlier |
| 🏆 | strict-typing | todo | `py.typed` ships; one `mypy --strict` error; CI runs ty, not mypy |

## Work for Platinum on HACS

Each step is a small PR. Run `uv run python scripts/check.py` and `scripts/ha-test.sh` before calling it done.

1. **Share the Order Status option.** Move `_status_option` from `sensor.py` to `entity.py`, since `calendar.py` imports it as a private name. Add this plan.
2. **Strict typing.**
   - Fix `sensor.py:108`: `value_fn` returns `Decimal`, which `StateType | datetime` does not allow. Widen it to `StateType | date | datetime | Decimal`, matching `SensorEntity.native_value`.
   - Library: two `no-any-return` errors in `client.py` (around lines 319 and 333). Annotate the parsed JSON.
   - Add `mypy --strict` for `custom_components/` to CI's `ha-test` job, since HA core type-checks with mypy, not ty. Optionally use core's `mypy.ini` settings.
3. **Devices and icons.** Add `configuration_url` to `DeviceInfo`. Add icons for `binary_sensor.ready_for_pickup` (`mdi:basket-check` when on, `mdi:basket-outline` when off) and the calendar. Give `order_status` state icons and remove the redundant `order_total` and `pickup_slot` icons.
4. **Documentation (README).** Add the missing sections: high-level description, removal, installation parameters, supported functions (calendar and binary sensor included), known limitations, troubleshooting, use cases, supported devices. Use the headings of a home-assistant.io integration page, so the text moves over to core unchanged.
5. **`quality_scale.yaml`.** Add `custom_components/bilkatogo/quality_scale.yaml` with every rule from the table above, using `status: exempt` and `comment:` where needed. Hassfest only validates it for core integrations, so check it against the rule list in core's `script/hassfest/quality_scale.py` by hand.
6. **HACS default.** Done: [hacs/default#13209](https://github.com/hacs/default/pull/13209) is merged.
   - Add `"country": ["DK"]` to `hacs.json`; HACS requires it for services limited to one country.
   - Release (`release.py publish`) once CI is green.
   - Fork `hacs/default`, add `nickknissen/bilkatogo` to `integration` in alphabetical order, and fill in the PR template from a personal account. The repo already has a description, topics, issues, an MIT license, releases and a brand icon.

After these steps every rule is either `done` or `exempt` with a reason. That is the highest level the scale offers (custom integrations show as 📦 Custom, but the file documents the result).

## Path into core (separate and optional)

Core's own guide recommends what this plan does first: ship through HACS, build a user base, then ask the maintainers. Core admits a new integration at **Bronze and nothing more**, then raises its tier through follow-up PRs. Expect a series of PRs, not one move.

### Ask before writing code

- **Unofficial API with a stored password.** ADR 0002 says automated access may conflict with Bilka's terms. The core guide says nothing about undocumented APIs or terms of service and sends such questions to #core on Discord. Ask there before investing work; this is the most likely reason for rejection.
- **Established user base.** Core requires a service with real users. Bilka has them, but maintainers also judge whether the integration has users, which is what the HACS period shows.

### What core will ask to change

- **First PR: one platform, Bronze only.** Only `sensor`, with no diagnostics, custom actions, reauth or reconfigure flow. The binary sensor, calendar, reauth, diagnostics, actions and events each come in a later PR, and each raises the `quality_scale` entry in the manifest.
- **Core lint and style.** Core's ruff config reports 63 findings: 52 missing docstrings (D100–D107), 9 import-order findings (core treats `homeassistant` as first-party), and 2 `TID251`: **core now bans `voluptuous` and wants `probatio`** in `config_flow.py` and `services.py`. Look up probatio's API in core before porting.
- **Manifest.** Drop `version`, point `documentation` at `https://www.home-assistant.io/integrations/bilkatogo`, add `"quality_scale": "bronze"` and `"loggers": ["bilkatogo"]`. Core generates `translations/` from `strings.json` and takes brand images from the brands repo, so neither folder moves over.
- **Likely reviewer requests (not quality-scale rules, but routine in review):**
  - State attributes: the repeats are gone. The Order Number is only on the Order Status sensor and the Order Total only on its own sensor. Substitutions and Missing Items still carry lists, which core may ask to drop now that the event entities carry each item.
  - Custom bus events: done. The Substitution and Missing item `event` entities fire the same data, show in the UI and work in the automation editor. The bus events `bilkatogo_substitution` and `bilkatogo_missing_item` were removed before 1.0.
  - Unique IDs: done. Entities and the device are keyed on the config entry's unique ID (the lower-cased email) instead of the entry ID, so they survive removing and re-adding the account; config entry 1.2 migrates the registries. Gigya's `UID` from `accounts.login` would survive an email change too, but the library does not expose it yet.
  - The `add_to_cart` action writes to the user's account. ADR 0005 limits it to the Cart and it never checks out, which is easy to explain in the PR.
- **Tests.** Port `tests/ha` to core's layout: `MockConfigEntry`, an `init_integration` fixture, `snapshot_platform` for entities, and patching the library client instead of `respx`. Keep 100% config-flow coverage.
- **Side PRs:** brands (`core_integrations/bilkatogo`), home-assistant.io docs (separate PR, written from the README sections in step 5), and `CODEOWNERS` and `requirements_all.txt`, which `script.hassfest` and `script.gen_requirements_all` regenerate.
- **Library fit.** Core needs Python `>=3.14.2`; the library's `>=3.12` fits. The httpx floor `>=0.28.1` must fit core's pin in `package_constraints.txt`. The CLI extra (`typer`) stays optional and never installs in HA.

## Sources

- Rule list: https://github.com/home-assistant/core/blob/dev/script/hassfest/quality_scale.py
- Rule pages: https://github.com/home-assistant/developers.home-assistant/tree/master/docs/core/integration-quality-scale/rules
- Scale overview: https://www.home-assistant.io/docs/quality_scale/
- Creating a core integration (eligibility, Bronze only, one platform): https://contributors.home-assistant.io/core/integrations/create/
- Development checklist: https://contributors.home-assistant.io/core/development/checklist/
- HACS default inclusion: https://www.hacs.xyz/docs/publish/include/
