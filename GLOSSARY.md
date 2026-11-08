# BilkaToGo

Tracking a customer's BilkaToGo grocery orders (Salling Group's online shop at bilkatogo.dk) from the moment they are placed until they are collected, including which products Bilka swapped for something else or could not deliver, what the Order cost, and looking up products and their prices in the shop.

## Language

### Orders

**Order**:
One purchase placed in BilkaToGo, identified by its Order Number, collected or delivered in one Pickup Slot.
_Avoid_: Purchase, basket, cart (a Cart is the not-yet-placed order)

**Cart**:
The customer's Order before it is placed: its Cart Lines and how it is to be received, either collected at the Pickup Point they have chosen or brought by Home Delivery. Either way one Store serves it, and product search uses that Store unless told otherwise.
_Avoid_: Basket, kurv

**Cart Line**:
One Product with its quantity in the Cart, with its Price and line total. Becomes an Order Line once the Order is placed.
_Avoid_: Basket item, Order Line (that belongs to a placed Order)

**Current Order**:
The unfinished Order with the earliest Pickup Slot or, when every Order is finished, the most recently finished one. Chosen from the Order History.
_Avoid_: Active order, latest order, open order

**Order History**:
The customer's most recently placed Orders, the ones the Current Order is chosen from.
_Avoid_: Order list, orders

**Finished Order**:
An Order nothing more will happen to: its Order Status is Picked Up, Not Collected, Cancelled or Sold Out.
_Avoid_: Closed order, completed order, done

**Order Number**:
The 12-digit identifier Bilka shows as `#902852320314`.
_Avoid_: Order ID, reference

**Order Line**:
One ordered product with its quantity inside an Order.
_Avoid_: Item, row

**Pickup Slot**:
The date and time window in which the Order is collected or delivered, e.g. Tuesday 20/10, 12:00 to 14:00. Bilka's `deliveryDate` with `deliveryInterval.start` and `end` on an Order; on the Cart, the one chosen there, `deliveryDate.deliveryDate`, `intervalStart` and `intervalEnd`.
_Avoid_: Delivery interval, time slot, delivery date

**Pickup Point**:
Where the customer collects the Order: a Bilka store such as Bilka Vejle, or a standalone point such as Pickup Fredericia. Each Pickup Point is served by one Store.
_Avoid_: Pickup zone, delivery address, location

**Store**:
The Bilka that packs the Order and whose assortment, Prices, Offers and stock apply, e.g. Bilka Vejle. Bilka's `sid` on a Pickup Point and on the Cart's `deliveryAddress`.
_Avoid_: Warehouse, shop, sid

**Pickup Code**:
The 8-digit code the customer enters at check-in when collecting the Order.
_Avoid_: Check-in code, PIN

**Order Total**:
What the customer pays for the Order as it stands now, products after Discount plus Deposit, fees and delivery. It changes as Bilka packs the Order, Substitutes products and finds Missing Items. Bilka's `price` on the Order and `packedPrice` in the order history, where it is an estimate until the Order is Packed.
_Avoid_: Price, amount, invoice, packed price

**Ordered Total**:
What the Order cost when the customer placed it. The Order Total is compared with it to see what packing changed. Bilka's `orderedPrice` in the order history.
_Avoid_: Checkout price, original price

**Deposit**:
The bottle and can deposit added to the Order Total, shown on the website as "Pant". Bilka's `stat.deposit` on the Order and the Cart.

**Service Fee**:
What Bilka charges for packing the Order, shown on the website as "Pakkeservice". Bilka's `stat.packing` on the Order and the Cart.
_Avoid_: Packing fee, picking fee

**Packaging**:
What Bilka charges for bags, shown on the website as "Emballage". Bilka's `stat.packaging_price` on the Order and the Cart.
_Avoid_: Bags

**Delivery Fee**:
What Bilka charges for Home Delivery. Bilka's `stat.delivery_price` on the Order and the Cart.

**Discount**:
The total saved on the Order through Offers, membership and vouchers. The sum of Bilka's `stat.promo`, `stat.membership_offer_discount`, `stat.personalized_offer_discount` and the `amount` of each voucher in `stat.vouchers`, on the Order and the Cart.
_Avoid_: Promo, savings

**Home Delivery**:
An Order that is brought to the customer's address instead of collected at a Pickup Point. Bilka's `pickupzone.home_delivery` on an Order. The Cart has no such flag: its `deliveryAddress` is a home address when it has a `dawa_uid` or a `recipient_name`, both empty for a Pickup Point (ADR 0005).

**Minimum Order**:
The smallest Cart total Bilka accepts for Home Delivery; `minimum_buy`. Pickup has none in every capture.
_Avoid_: Minimum buy, minimumskøb (use only as the Danish alias)

### Order Status

**Order Status**:
Where an Order is in its lifecycle, expressed in the same states the BilkaToGo website shows the customer.
_Avoid_: State, stage

**Bilka Status Code**:
The raw Danish status string Bilka's backend returns (e.g. `Bestilt`, `Faktureret`); several codes map to one Order Status.
_Avoid_: Raw status, API status

| Order Status | Danish (as shown on bilkatogo.dk) | Bilka Status Codes |
|---|---|---|
| **Processing** | Ubehandlet (and Bilka's generic "in progress" label) | `Ubehandlet`, `Capture igang` |
| **Received** | Modtaget | `Bestilt` |
| **Packed** | Pakket | `Pakket` |
| **Picked Up** | Afhentet og faktureret | `Leveret`, `Faktureret` |
| **Not Collected** | Uafhentet | `Uafhentet` |
| **Cancelled** | Annulleret | `Annulleret` |
| **Sold Out** | Fuldt udsolgt | `Fuldt udsolgt` |
| **Unknown** | Ukendt | any other code |

Note that Bilka says "Modtaget" (received) for the code `Bestilt` (ordered); the Order Status follows what the customer sees, not the code.

### Substitutions

**Substitution**:
An Order Line Bilka could not fulfil and replaced with a different product, shown on the website as "erstattet". It pairs the Original Product and the quantity ordered with its Replacement Product and the quantity packed, e.g. 2 Letmælk replaced by 3 Minimælk.
_Avoid_: Replacement (on its own), swap, erstatningsvare (use only as the Danish alias)

**Original Product**:
The product the customer ordered on a Substituted Order Line.

**Replacement Product**:
The product Bilka packed instead of the Original Product, e.g. Kiwi instead of Blommer. When it is dearer, Bilka charges the Original Product's price for it: its line total (Bilka's `amount`) can be lower than its Price.
_Avoid_: Erstatningsvare in code

**Missing Item**:
An Order Line Bilka packed fewer of than ordered without a Replacement Product, e.g. 2 of 4 skyr. It carries the missing quantity. Distinct from a Substitution. An Order where nothing at all could be packed has the Order Status Sold Out.
_Avoid_: Udsolgt vare, OOS line, shortage

### Products

**Product**:
One article in the BilkaToGo assortment, identified by its Article Number. An Order Line refers to a Product.
_Avoid_: Item, vare (use only as the Danish alias)

**Article Number**:
Bilka's identifier for a Product, e.g. `19753`: what Order Lines and Cart Lines refer to and what the Cart is changed by. Bilka calls it `objectID` (and `productId` when changing the Cart). Algolia's separate `article` field is not the Article Number.
_Avoid_: SKU, product ID, objectID, EAN (a barcode is something else)

**EAN**:
The barcode number printed on a Product, e.g. `5711953011399`. Algolia's `gtin`. Not the Article Number.
_Avoid_: GTIN, barcode, stregkode

**Size**:
How much one unit of a Product holds, e.g. 750 g or 1,5 l. Bilka's `units` and `unitsOfMeasure`, on an Order Line's `product` and on an Algolia hit. A Replacement Product often has the Original Product's name and differs only in Size, e.g. Klementiner 750 g replaced by Klementiner 900 g.
_Avoid_: Weight, volume, pack size, netcontent

**Category**:
Where a Product sits in Bilka's assortment, most general first, e.g. "Mejeri & køl > Mejeri > Ost > Revet ost & topping > Revet mozzarella". Algolia's `deepestCategoryPath` with its `productType` after it, unless the path already ends with it.
_Avoid_: Department, hierarchy, product type (on its own)

**Price**:
What one unit of a Product normally costs at a Store. On a Cart Line it is Bilka's `product.price`; the line's `unitprice` already has the Offer or Multibuy Offer applied.
_Avoid_: List price, normal price

**Offer**:
A temporary lower Price for a Product at a Store, with Bilka's label (e.g. "Skarp pris"), an end date and optionally a limit per customer, shown on the website as "tilbud". The label is Algolia's `storeData.<sid>.offerDescription`; "Se Her", Bilka's placeholder for a leaflet Offer without a text of its own, reads "Tilbud".
_Avoid_: Promo, sale, campaign

**Offer Price**:
The Price of a Product while it has an Offer.
_Avoid_: Sale price, discount (a Discount is the amount saved)

**Multibuy Offer**:
A deal on buying several units, e.g. "3 for 50 kr". Distinct from an Offer, which lowers the Price of one unit.
_Avoid_: Bundle, mix-and-match

**Unit Price**:
The Price per unit of measure, e.g. 24,95 kr/kg, used to compare Products.
_Avoid_: Kilo price, comparison price

**Leaflet**:
Bilka's weekly printed and online offer leaflet, "Bilka avisen". Whether a Product is in this week's Leaflet is one flag for all Stores, Algolia's `isInCurrentLeaflet`; its Offers still differ per Store.
_Avoid_: Flyer, catalogue, tilbudsavis (use only as the Danish alias)
