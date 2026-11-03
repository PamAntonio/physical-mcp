# Costs

Filled in by hand. `scripts/dashboard_metrics.py` reads the tables below, so keep their headers
as they are. Add rows freely.

- A part only counts as **proven** with a listing URL **and** the date the listing was checked.
  A price without both shows on the dashboard as entered, not proven.
- Total is what the listing shows for this row. If it's a share of a multipack, say how in Note:
  `2 of 10 from a $15 pack`.
- A part we already had gets `already owned (we had them at home), not counted` in Note: it's left
  out of the total and doesn't hold it back.
- Prices in USD, e.g. `12.99` or `$12.99`. Never type an estimate in here, leave it blank.
- A replacement or retrofit cost only counts with a source URL: a retailer, manufacturer or
  article page that shows the figure. Write the figures as the page shows them, with `$`.

## Parts

The rig as built (`hardware.md`, Parts). The kitchen timer isn't here, it's the machine.

Prices are what these listings show, not receipts.

| Item | Qty | Unit price | Total | Store | Listing URL (price as listed, checked &lt;date&gt;) | Checked | Note |
|---|---|---|---|---|---|---|---|
| MG90S micro servo, metal gear (180°) | 2 | $3 | $6 | Amazon | https://www.amazon.com/dp/B0CP98TZJ2 | 2026-11-03 | &lt;N&gt; of &lt;M&gt; from a $&lt;pack price&gt; pack |
| Kyusrd 1080P USB webcam | 1 | $4.02 | $4.02 | Walmart | https://www.walmart.com/ip/16109623001 | 2026-11-03 | plus $6.39 shipping, not counted |
| Microcontroller (ESP32) | | | $7 | | | | |
| Jumper wires | | | | AliExpress | https://www.aliexpress.us/item/3256809267678383.html | 2026-11-03 | |
| Mini breadboard, Olimex BREADBOARD-MINI | | | | DigiKey | https://www.digikey.com/en/products/detail/olimex-ltd/BREADBOARD-MINI/21662495 | 2026-11-03 | |
| Finger materials (arms, hot glue, mounting tape, blocks, base board) | | | | | | | |
| Always-on USB power bank | | | | | | | already owned (we had them at home), not counted |
| USB cable (one end cut off for servo power) | | | | | | | already owned (we had them at home), not counted |

## Replacing the machine

One specific example machine per card on the Where It Fits slide, priced from a page that shows
the price. A range is the lowest and highest price for that kind of machine on the same page.

| Card | Example machine | Replacement cost range | Source URL | Date checked |
|---|---|---|---|---|
| Research labs (incubators, shakers, hot plates, ovens) | Yamato IC-103C benchtop incubator, 37 L | $798.59 to $1,185.00 | https://www.tequipment.net/life-sciences-equipment/Incubators/ | 2026-11-02 |
| Legacy manufacturing (old controllers and HMIs) | AutomationDirect C-more EA9-T10CL 10" HMI touch panel | $1,718.00 | https://www.automationdirect.com/c-more/ea9/hardware/10_tft | 2026-11-02 |
| Hardware QA (pressing device buttons to test firmware) | igus RBTX touch screen test robot (3-axis, presses displays and buttons) | $12,243.75 to $12,288.75 | https://rbtx.com/en-US/solutions/automate-touch-screen-test-with-robot | 2026-11-02 |
| Building ops (boiler panels, HVAC controllers, freezer alarms) | Honeywell T775B2040 electronic temperature controller (2 inputs, 4 relays) | $540.52 | https://www.supplyhouse.com/T775-2000-Series-10000000 | 2026-11-02 |
| Agriculture (greenhouse and grain dryer controllers) | Link4 S750 iGrow greenhouse environment control kit | $8,745.00 | https://www.growerssupply.com/prod/gs-thermostats-controllers/link4-greenhouse-environmental.html | 2026-11-02 |
| Commercial kitchens (ovens and fryers) | Frymaster PH155-C 50 lb natural gas fryer, programmable | $6,765.00 | https://www.webstaurantstore.com/frymaster-ph155-c-natural-gas-high-efficiency-fryer-50-lb-with-programmable-computer-controls-and-stainless-steel-door-80-000-btu/369PH155CN.html | 2026-11-02 |
| Warehouses (label printers, scales, kiosks) | Mettler-Toledo BC60 parcel scale, 150 lb (Uline H-5275) | $945.00 | https://www.uline.com/BL_2221/Mettler-Toledo-Parcel-Scale | 2026-11-02 |
| Home appliances | GE JB256DMWW 30" free-standing electric range | $649.00 to $829.00 | https://www.homedepot.com/b/Appliances-Ranges-Electric-Ranges/GE/N-5yc1vZc3obZlo | 2026-11-02 |

What each page shows:

- Research labs: TEquipment's "Your Price" across its benchtop incubators, from the Boekel 132000 ($798.59) to the Yamato IC-103CW ($1,185.00). The IC-103C itself: List Price $1,150.00, Your Price $977.50. The higher-end figure we had ($9,000 to $11,000) isn't on this page or on Cenmed's incubator page, so it's out.
- Hardware QA: igus's "Hardware Price" for two touch screen test solutions on the same page, hardware only. These are solution packages, not shelf products.
- Building ops: one controller only. No full boiler panel or freezer alarm was priced.
- Agriculture: a greenhouse controller only. No grain dryer controller was priced.
- Warehouses: Uline's price each for one scale ($920 each for 2 or more). A scale only, no label printer
  or kiosk. The Zebra ZT411 label printer we had first is out: its store only showed a struck-through
  list price, with the real selling price hidden until it's in the cart.
- Home appliances: $649.00 sale, $829.00 regular, same listing.

## Retrofitting with IoT

What adding sensors and connectivity to an old machine cost, from a source that says so.

| Option | Cost added | Source URL | Date checked | What the source says |
|---|---|---|---|---|
| Bosch Rexroth retrofit of a SKIL drill press (sensors + IoT Gateway) | about $2,000 | https://www.designdevelopmenttoday.com/industries/iot/video/21072198/making-a-dumb-drill-smart | 2026-11-02 | "All-in, they added about $2,000 worth of technology to the press." |
| Retrofitting legacy equipment, typical range | a couple thousand dollars, up to $10,000 to $20,000 | https://www.automate.org/robotics/industry-insights/legacy-equipment-digital-transformation | 2026-11-02 | "This kind of addition to your equipment could cost as little as a couple thousand dollars, Tatum notes, or it could cost as much as $10,000 to $20,000." |

## Model prices

List prices per 1M tokens, copied from each provider's own page. `scripts/dashboard_metrics.py` times
these by `job.usage` for the API cost per job. Hackathon credits paid for every call, so that's cost at
list price, not what we spent. Cached input has its own lower price on both pages, but `job.usage` doesn't
record cached tokens, so everything counts at the input price.

| Model | Input $/1M | Output $/1M | Source URL | Date checked | Note |
|---|---|---|---|---|---|
| muse-spark-1.3 | $1.25 | $4.25 | https://dev.meta.ai/docs/pricing-rate-limits | 2026-11-03 | Standard mode. The Contributor mode price is for a different model id, which we don't call |
| meta-models/muse-spark-open-1026 | $1.25 | $4.25 | https://www.baseten.co/library/muse-spark-open/ | 2026-11-03 | The page lists meta-models/muse-spark-open. It doesn't show a separate price for the 1026 id the organizers gave us |
