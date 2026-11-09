# bilkatogo

[![PyPI](https://img.shields.io/pypi/v/bilkatogo)](https://pypi.org/project/bilkatogo/)
[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz/)

Track your [BilkaToGo](https://www.bilkatogo.dk) grocery orders: an async Python library, a
command line tool and a Home Assistant integration that follow your order from the moment it is
placed until you pick it up, including which products Bilka substituted.

> **Unofficial.** This project is not affiliated with or endorsed by Bilka or Salling Group. It
> uses the same private web API as the bilkatogo.dk website, logging in with your email and
> password. That API can change or break at any time.

The library, the CLI and the Home Assistant integration are released together and always share
the same version.

- [Library](#library): an async Python client for your orders, Pickup Points, product search and
  Cart.
- [Home Assistant](#home-assistant): sensors, a calendar, actions and events for your Current
  Order.
- [Command line](#command-line): the library from your terminal or a script.

## Library

```sh
pip install bilkatogo
```

```python
import asyncio

from bilkatogo import BilkaToGoClient, FileTokenStorage


async def main() -> None:
    async with BilkaToGoClient(
        "you@example.com",
        "your-password",
        token_storage=FileTokenStorage("session.json"),
    ) as client:
        current = await client.get_current_order()
        if current:
            print(current.order_number, current.status, current.pickup_slot)


asyncio.run(main())
```

The full library documentation, covering orders, the Cart, product search, the models and the
errors, is in [docs/library.md](https://github.com/nickknissen/bilkatogo/blob/main/docs/library.md).

## Home Assistant

The integration follows your Current Order in Home Assistant: its Order Status, Pickup Slot,
Pickup Code and Order Total, which products Bilka substituted or left out, and a calendar of
your Pickup Slots. Actions search Bilka's products, add them to your Cart and choose its Pickup
Slot. It needs Home Assistant 2026.10 or later.

1. Install **BilkaToGo** from the Home Assistant Marketplace (HACS): open HACS, search for
   **BilkaToGo** and download it, or open it straight away:

   [![Open your Home Assistant instance and open the repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nickknissen&repository=bilkatogo&category=integration)

   Restart Home Assistant afterwards.
2. Go to **Settings > Devices & services > Add integration**, pick **BilkaToGo** and sign in
   with your bilkatogo.dk email and password.

An automation that sends the Pickup Code once the order is packed (replace the entity IDs with
yours):

```yaml
automation:
  - alias: BilkaToGo ready for pickup
    triggers:
      - trigger: state
        entity_id: binary_sensor.me_example_com_ready_for_pickup
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: >
            Your groceries are packed. Pickup Code:
            {{ states('sensor.me_example_com_pickup_code') }}.
```

The full integration documentation, covering the entities, actions, events, known limitations,
troubleshooting and removal, is in
[docs/home-assistant.md](https://github.com/nickknissen/bilkatogo/blob/main/docs/home-assistant.md).

## Command line

```sh
pip install 'bilkatogo[cli]'
```

Or run it without installing:

```sh
uvx --from 'bilkatogo[cli]' bilkatogo --help
```

```sh
bilkatogo login                 # log in and store the session
bilkatogo order                 # show the Current Order with its details
bilkatogo search skyr           # Products with Article Number, Price, stock and Offer
bilkatogo cart add 18381        # add a Product to the Cart by Article Number
```

The full command line documentation, covering every command, credentials and capturing a
fixture for an issue, is in
[docs/cli.md](https://github.com/nickknissen/bilkatogo/blob/main/docs/cli.md).

## Development

Library and CLI:

```sh
uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run pytest
```

Home Assistant tests use their own dependency group, because
`pytest-homeassistant-custom-component` pins its own pytest and httpx. Keep them in a
separate virtual environment so the library environment stays untouched:

```sh
UV_PROJECT_ENVIRONMENT=.venv-ha uv run --no-default-groups --group ha pytest tests/ha
UV_PROJECT_ENVIRONMENT=.venv-ha uv run --no-default-groups --group ha ty check custom_components tests/ha
```

Home Assistant does not run on Windows. There, `scripts/ha-test.sh` runs both commands in WSL:

```sh
wsl.exe -e bash scripts/ha-test.sh
```

The Home Assistant tests run the real library against the fake Bilka backend in
`tests/fake_bilka.py`. The integration creates its httpx client with Home Assistant's
`create_async_httpx_client`, and `tests/ha/conftest.py` routes every request at the
transport level to the fake backend with `respx`, so nothing in the integration is patched.

Releasing is described in [AGENTS.md](AGENTS.md#releasing).

## License

MIT
