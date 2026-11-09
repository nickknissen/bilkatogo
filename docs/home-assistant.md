# Home Assistant

[BilkaToGo](https://www.bilkatogo.dk) is Bilka's online grocery shop in Denmark: you order on
bilkatogo.dk and pick the order up at a Bilka store, or have it delivered. The integration follows
your Current Order in Home Assistant: its Order Status, Pickup Slot, Pickup Code and Order Total,
which products Bilka substituted or left out, and a calendar of your Pickup Slots. Actions
search Bilka's products, add them to your Cart and choose its Pickup Slot.

See the [README](../README.md) for the Python library and the command line tool.

## Use cases

- Get a notification when the order is packed, with the Pickup Code at hand.
- Hear about substitutions and Missing Items before you drive to the store.
- Show the next Pickup Slot on a dashboard or in a shared family calendar.
- Get reminded when no Pickup Slot is open in the coming days, or when one opens.
- Add groceries to the Cart by voice or from a shopping-list automation.

## Supported accounts

Any bilkatogo.dk customer account, for pickup at a Bilka store and for Home Delivery. Bilka
only serves Denmark.

## Installation

The integration needs Home Assistant 2026.10 or later.

1. Install **BilkaToGo** from the Home Assistant Marketplace (HACS): open HACS, search for
   **BilkaToGo** and download it, or open it straight away:

   [![Open your Home Assistant instance and open the repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nickknissen&repository=bilkatogo&category=integration)

   Restart Home Assistant afterwards.
2. Go to **Settings > Devices & services > Add integration** and pick **BilkaToGo**.
3. Fill in the fields below. Add the integration once per account.

| Field | Description |
|---|---|
| Email | The email address you sign in to bilkatogo.dk with. |
| Password | Your bilkatogo.dk password. Home Assistant stores it, so it can sign in again when Bilka's session expires. |

The integration has no options. If the password changes, Home Assistant asks you to sign in
again.

## Data updates

The integration polls Bilka every 60 minutes, and every 5 minutes from 3 hours before the
Pickup Slot starts until the order is picked up, not collected or cancelled. While Bilka fails
to answer, it waits twice as long after each failed update in a row, up to 60 minutes (so 10,
20, 40, then 60 minutes close to the Pickup Slot), and goes back to its normal interval as soon
as Bilka answers. If you set your
own polling interval, keep it at 5 minutes or more so the traffic still looks like a person
checking their order. To update now, use the `homeassistant.update_entity` action on any
BilkaToGo entity.

Each update also reads the Cart and the Pickup Slots on offer for it, two requests more, for
the Next available pickup slot sensor. When Bilka fails to answer those, only that sensor is
unavailable until the next update; the Order's entities keep their values.

## Entities

All entities, except the calendar and Next available pickup slot, describe the Current Order.
Ready for pickup is on while the Current Order is Packed.

| Entity | State | Attributes |
|---|---|---|
| Order Status | Processing, Received, Packed, Picked Up, Not Collected, Cancelled, Sold Out or Unknown | Bilka status code, Order Number |
| Substitutions | Number of substituted products | List of Original Product, Replacement Product, their Article Numbers, the ordered quantity and the Replacement Product's quantity |
| Missing Items | Number of products packed short without a replacement | List of Product, Article Number, ordered and missing quantity |
| Order Total | What you pay for the order now (DKK) | Ordered Total, difference from the Ordered Total (negative when you pay less), Deposit, Service Fee, Packaging, Delivery Fee, Discount |
| Pickup Slot | Start of the pickup slot (timestamp, Europe/Copenhagen) | Pickup Slot end |
| Pickup Code | The code you enter at check-in | |
| Pickup Point | The Bilka store you pick up from | |
| Ready for pickup | On while the order is Packed | |
| Substitution (event) | When the last substitution fired | Order Number, Original Product, Replacement Product, their Article Numbers, ordered quantity, quantity |
| Missing item (event) | When the last Missing Item fired | Order Number, Product, Article Number, ordered and missing quantity |
| Pickup slots (calendar) | On during a Pickup Slot | |
| Next available pickup slot | Start of the earliest open Pickup Slot in the next 7 days for your Cart (timestamp) | End, availability, order before (Bilka's deadline, e.g. "i morgen kl. 6:00"), note, Pickup Point (left out for Home Delivery), number of open slots |

Substitutions, Missing Items, the Pickup Code and the Order Total breakdown (Deposit, Service
Fee, Packaging, Delivery Fee, Discount) are fetched once the order is packed. Before that the
Order Total is Bilka's estimate from the order history.

An Order Status of Unknown (`unrecognized`) means Bilka sent a status code the integration does
not know yet; the code is in the attribute. Without any order the entities have no state.

Next available pickup slot follows whatever your Cart has on bilkatogo.dk, its Pickup Point or
Home Delivery, not the Current Order. It has no state when every slot in the next 7 days is
closed or sold out; the number of open slots is then 0. The `bilkatogo.choose_pickup_slot`
action chooses one of the open slots for your Cart. A Home Delivery night slot starts at
00:00 on its date, and Bilka delivers it from the evening before.

The calendar holds an event for each of your 20 most recent Orders that has a Pickup Slot,
upcoming and past, except Cancelled and Sold Out Orders. An event spans the Pickup Slot, is
called "BilkaToGo", has the Order Number as its description and the Pickup Point as its
location. A Home Delivery event has no location. The text is the same in every language; the
Order Status sensor shows the status.

## Actions

| Action | Fields | Response |
|---|---|---|
| `bilkatogo.add_to_cart` | `article_number`, `quantity` (default 1) | Optional: the Cart, with its Cart Lines (Product, Article Number, quantity, Price, line total), total, Deposit, Service Fee, Packaging, Delivery Fee, Discount, `home_delivery`, Pickup Point, Pickup Slot, Minimum Order and how much is left to it, the next available date and Bilka's warnings |
| `bilkatogo.add_items_to_cart` | `items`: a list of `article_number` and `quantity` (default 1) | Optional: `changes`, per Product the quantity requested and applied, `status` (`applied`, `rejected`, `skipped`) and Bilka's message; and the `cart`, as for `add_to_cart`. Fails only when none was added |
| `bilkatogo.remove_from_cart` | `article_number`, `quantity` (default: all of it) | Optional: the Cart, as for `add_to_cart` |
| `bilkatogo.search` | `query`, `limit` (default 10), `in_stock`, `on_offer`, `in_leaflet` (default false), `sort` (`relevance`, `price`, `unit_price`) | `products`: Article Number, name, brand, Price, Unit Price and unit, in stock, Offer and Multibuy Offer |
| `bilkatogo.get_cart` | none | The Cart, as for `add_to_cart`, without changing it |
| `bilkatogo.choose_pickup_slot` | `date`, `start`, `end` (only when two slots start at the same time) | Optional: `chosen` and `replaced` (each `starts_at` and `ends_at`; `replaced` is `null` when the Cart had no slot ahead) and `changed` (`false` when the slot was already chosen) |

`choose_pickup_slot` chooses one of the open Pickup Slots in the next 14 days for your Cart,
for its Pickup Point or Home Delivery. A Home Delivery night slot is chosen by the date it is
listed under, starting at 00:00. Choosing holds nothing: the slot can still sell out until you
place the Order on bilkatogo.dk. A slot you give up can sell out too, so the response names the
slot it replaced. A closed, sold-out or unknown slot, or one Bilka refuses, fails with a
message saying so, and the Cart keeps its slot. Afterwards the integration updates its
entities, so Next available pickup slot follows. The Pickup Point or Home Delivery is still
chosen on bilkatogo.dk.

`add_to_cart` adds to what is already in the Cart and `remove_from_cart` takes `quantity` off
it, or the whole Cart Line without one; neither places an Order. In the Cart response
`pickup_point` is the Pickup Point's name, `null` for Home Delivery (the response never holds
your home address); `pickup_slot` has `starts_at` and `ends_at`, `null` when none is chosen or
the chosen one has passed; `minimum_order` is `null` when Bilka sets none, as for pickup; and
`warnings` includes how far the Cart is from the Minimum Order. `search` searches at the Store
serving the Cart, for pickup or Home Delivery, so choose either on bilkatogo.dk first. `sort`
orders the `limit` best matches by what one costs now, the Offer Price where there is one; it
does not search the whole assortment for the cheapest. With more than one account added, pass
`config_entry_id`. A script that searches and adds the first hit:

```yaml
script:
  add_skyr:
    sequence:
      - action: bilkatogo.search
        data:
          query: skyr
          limit: 1
        response_variable: found
      - action: bilkatogo.add_to_cart
        data:
          article_number: "{{ found.products[0].article_number }}"
```

A script that adds a weekly basket in one go and notifies about anything Bilka rejected:

```yaml
script:
  weekly_basket:
    sequence:
      - action: bilkatogo.add_items_to_cart
        data:
          items:
            - article_number: "18381"
              quantity: 10
            - article_number: "19753"
              quantity: 2
        response_variable: added
      - if: "{{ added.changes | rejectattr('status', 'eq', 'applied') | list | count > 0 }}"
        then:
          - action: notify.notify
            data:
              message: >-
                Not added: {{ added.changes | rejectattr('status', 'eq', 'applied')
                | map(attribute='article_number') | join(', ') }}
```

## Substitution and Missing item events

The Substitution event entity fires once for every new substitution, and the Missing item
event entity once for every new Missing Item. Items that already fired are remembered across
restarts and reloads. Each event carries its data as attributes:

| Event entity | Attribute | Example |
|---|---|---|
| Substitution | `order_number` | `900000000001` |
| | `original_product` | `Arla minimælk 1 l` |
| | `original_article_number` | `18381` |
| | `ordered_quantity` | `2` |
| | `replacement_product` | `Øko minimælk 1 l` |
| | `replacement_article_number` | `19753` |
| | `quantity` | `2` |
| Missing item | `order_number` | `900000000003` |
| | `product` | `Skyr` |
| | `article_number` | `932058` |
| | `ordered_quantity` | `4` |
| | `missing_quantity` | `2` |

An automation that sends a notification for each substitution. `not_from: unavailable` keeps
it quiet when the entity comes back after a reload or after Bilka was unreachable:

```yaml
automation:
  - alias: BilkaToGo substitution
    triggers:
      - trigger: state
        entity_id: event.me_example_com_substitution
        not_from: unavailable
    actions:
      - action: notify.notify
        data:
          message: >
            {{ trigger.to_state.attributes.ordered_quantity }} x
            {{ trigger.to_state.attributes.original_product }} was replaced by
            {{ trigger.to_state.attributes.quantity }} x
            {{ trigger.to_state.attributes.replacement_product }}.
```

And one for each Missing Item:

```yaml
automation:
  - alias: BilkaToGo missing item
    triggers:
      - trigger: state
        entity_id: event.me_example_com_missing_item
        not_from: unavailable
    actions:
      - action: notify.notify
        data:
          message: >
            {{ trigger.to_state.attributes.product }}:
            {{ trigger.to_state.attributes.missing_quantity }}
            of {{ trigger.to_state.attributes.ordered_quantity }} missing.
```

## Ready for pickup notification

An automation that sends the Pickup Code once the order is packed. The entity IDs start with
your account's email; replace them with yours from **Settings > Devices & services >
BilkaToGo**.

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

## Known limitations

- Bilka has no public API. The integration uses the same private API as bilkatogo.dk, which can
  change without notice, and automated access may conflict with Bilka's terms.
- The sensors follow one order, the Current Order. With several open orders, the others only
  show in the calendar, which covers the 20 most recent Orders.
- Updates come from polling, so a change at Bilka can take up to 60 minutes to show, or 5
  minutes close to the Pickup Slot.
- The Pickup Code, Substitutions, Missing Items and the Order Total breakdown are only known
  once the order is packed.
- The Home Delivery address is left out of entities and the calendar, as it is personal.
- `add_to_cart` and `search` need a Pickup Point chosen on bilkatogo.dk. The actions change
  the Cart, its Cart Lines and Pickup Slot, but the Pickup Point is still chosen on
  bilkatogo.dk. The integration never places, changes or cancels an order.

## Troubleshooting

- **Home Assistant asks you to sign in again.** Bilka rejected the stored password, usually
  because it was changed. Select **Reconfigure** on the BilkaToGo card under **Settings >
  Devices & services** and enter the new password.
- **The entities are unavailable.** Home Assistant could not reach Bilka, or Bilka answered
  unexpectedly. The integration logs this once and retries on the next poll. If it lasts, check
  that bilkatogo.dk works in a browser.
- **The Order Status is Unknown.** Bilka sent a status the integration does not know. Open an
  [issue](https://github.com/nickknissen/bilkatogo/issues) with the Bilka status code from the
  attribute.
- **`search` or `add_to_cart` fails with "no pickup point".** Choose a Pickup Point on
  bilkatogo.dk, then try again.

For more detail in the log, select **Enable debug logging** on the integration's page,
reproduce the problem, then disable it again; Home Assistant then downloads the log. Or add this
to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.bilkatogo: debug
    bilkatogo: debug
```

Attach the diagnostics (**Download diagnostics** on the integration's page) to an issue; they
leave out your email, password, name, address, phone number and Pickup Code.

## Removal

1. Go to **Settings > Devices & services > BilkaToGo**.
2. Open the menu (⋮) next to the account and select **Delete**.

Home Assistant also deletes the stored Bilka session and the record of which Substitutions and
Missing Items it already announced. To remove the integration's files too, remove BilkaToGo in
HACS and restart Home Assistant.

The entities are tied to the account, not to this setup of it. If you add the same account
again, its entities come back with the names and entity IDs you gave them.
