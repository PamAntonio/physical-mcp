# Costs

Filled in by hand. `scripts/dashboard_metrics.py` reads the two tables below, so keep their
headers as they are. Add rows freely.

- A part only counts as **proven** with an order number or a proof file. A price without one
  shows on the dashboard as entered, not proven.
- Proof files go in [`receipts/`](receipts/README.md), linked from the Proof file column, e.g.
  `[2026-10-28-servos.pdf](receipts/2026-10-28-servos.pdf)`. The file has to be there. A listing
  URL isn't proof of what we paid.
- Total is what we paid for this row. If it's a share of a multipack, say how in Note:
  `2 of 10 from a $15 pack`.
- A comparison only counts with a cost **and** a source URL.
- Prices in USD, e.g. `12.99` or `$12.99`. Never type an estimate in here, leave it blank.

## Parts

The rig as built (`hardware.md`, Parts). The kitchen timer isn't here, it's the machine.

| Item | Qty | Unit price | Total | Store | Order number | Proof file | Note |
|---|---|---|---|---|---|---|---|
| SG90 servo (180°, not 360°) | 2 | $3 | $6 | | | | |
| USB webcam | 1 | $4 | $4 | | | | |
| Microcontroller (ESP32) | | | $7 | | | | |
| Misc (wire, breadboard) | | | $3 | | | | |
| Always-on USB power bank | | | | | | | |
| USB cable (one end cut off for servo power) | | | | | | | |
| Finger materials (arms, hot glue, mounting tape, blocks, base board) | | | | | | | |

## Comparison

What the alternatives cost. An option with "retrofit" in its name counts as the IoT retrofit,
the rest as replacing the machine.

| Option | Cost | Source URL |
|---|---|---|
| Buy a new 4-channel timer | | |
| Replace the lab machine the timer stands in for | | |
| IoT retrofit of the lab machine | | |
