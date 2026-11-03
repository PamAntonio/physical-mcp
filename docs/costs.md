# Costs

Filled in by hand. `scripts/dashboard_metrics.py` reads the tables below, so keep their headers
as they are. Add rows freely.

- A part only counts as **proven** with a listing URL: the store page it was bought from.
  A price without one shows on the dashboard as entered, not proven.
- Total is what we paid for this row. If it's a share of a multipack, say how in Note:
  `2 of 10 from a $15 pack`.
- Prices in USD, e.g. `12.99` or `$12.99`. Never type an estimate in here, leave it blank.
- A replacement or retrofit cost only counts with a source URL: a retailer, manufacturer or
  article page that shows the figure. Write the figures as the page shows them, with `$`.

## Parts

The rig as built (`hardware.md`, Parts). The kitchen timer isn't here, it's the machine. The
power bank and the cut USB cable are left out, they're optional.

| Item | Qty | Unit price | Total | Store | Listing URL | Note |
|---|---|---|---|---|---|---|
| SG90 servo (180°, not 360°) | 2 | $3 | $6 | | | |
| USB webcam | 1 | $4 | $4 | | | |
| Microcontroller (ESP32) | | | $7 | | | |
| Misc (wire, breadboard) | | | $3 | | | |
| Finger materials (arms, hot glue, mounting tape, blocks, base board) | | | | | | |

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
| Warehouses (label printers, scales, kiosks) | Zebra ZT411 industrial label printer, 203 dpi (ZT41142-T31E000Z) | $2,662.78 | https://www.ptsmobile.com/ZT41142-T31E000Z.html | 2026-11-02 |
| Home appliances | GE JB256DMWW 30" free-standing electric range | $649.00 to $829.00 | https://www.homedepot.com/b/Appliances-Ranges-Electric-Ranges/GE/N-5yc1vZc3obZlo | 2026-11-02 |

What each page shows:

- Research labs: TEquipment's "Your Price" across its benchtop incubators, from the Boekel 132000 ($798.59) to the Yamato IC-103CW ($1,185.00). The IC-103C itself: List Price $1,150.00, Your Price $977.50. The higher-end figure we had ($9,000 to $11,000) isn't on this page or on Cenmed's incubator page, so it's out.
- Hardware QA: igus's "Hardware Price" for two touch screen test solutions on the same page, hardware only. These are solution packages, not shelf products.
- Building ops: one controller only. No full boiler panel or freezer alarm was priced.
- Agriculture: a greenhouse controller only. No grain dryer controller was priced.
- Warehouses: the store shows $2,662.78 struck through, next to "Add to Cart for Today's Low Price!", so the real selling price is hidden. Label printer only, no scale or kiosk.
- Home appliances: $649.00 sale, $829.00 regular, same listing.

## Retrofitting with IoT

What adding sensors and connectivity to an old machine cost, from a source that says so.

| Option | Cost added | Source URL | Date checked | What the source says |
|---|---|---|---|---|
| Bosch Rexroth retrofit of a SKIL drill press (sensors + IoT Gateway) | about $2,000 | https://www.designdevelopmenttoday.com/industries/iot/video/21072198/making-a-dumb-drill-smart | 2026-11-02 | "All-in, they added about $2,000 worth of technology to the press." |
| Retrofitting legacy equipment, typical range | a couple thousand dollars, up to $10,000 to $20,000 | https://www.automate.org/robotics/industry-insights/legacy-equipment-digital-transformation | 2026-11-02 | "This kind of addition to your equipment could cost as little as a couple thousand dollars, Tatum notes, or it could cost as much as $10,000 to $20,000." |
