# Powston rules for Flow Power's Flow Home plan

Powston custom code to work with Flow Power's Flow Home plan. The goal is to
add a thin layer on top of Powston Magic mode that charges the battery at the
lowest cost and sells into the plan's fixed export bonus, maximising earnings.

## How Flow Home billing works

The reference price fact sheets for every network Flow Home is offered on are
committed in this repo (`Flow-Home-TOU-<Network>-<State>-Price-Fact-Sheet.pdf`).
The plan has the same shape everywhere:

- **Buy (import)** is wholesale-linked (PEA adjusted). The fact sheets print a
  flat "estimate" across all TOU periods, but the real price you pay moves with
  the market plus the network (DNSP) charge for that time of day. Each network
  has cheap windows — a midday solar soaker and/or overnight off-peak — where
  the network component is lowest.
- **Sell (export)** is 0c/kWh at all times **except** a fixed bonus window
  **5:30pm–7:30pm** every day: 45c/kWh in NSW/QLD/SA, 35c/kWh in VIC. The
  window is the same in every state. Negative wholesale exports are not
  charged, so curtailment is unnecessary on this plan.
- Flow bills its windows in AEST year-round (their known DST quirk), so during
  daylight-saving months every window lands one hour later on the local clock.

Cheap import windows per network, from the FY26 fact sheets (local standard
time):

| Network | State | Cheap import windows | Bonus export |
|---|---|---|---|
| Energex | QLD | 11am–4pm (off-peak) | 45c 5:30–7:30pm |
| Ausgrid | NSW | 9pm–3pm (all but the 3–9pm peak) | 45c 5:30–7:30pm |
| Endeavour | NSW | 10am–2pm (N71 solar soak; overnight off-peak network fee is ~3x) | 45c 5:30–7:30pm |
| Essential | NSW | 10am–3pm and 10pm–7am (off-peak) | 45c 5:30–7:30pm |
| SAPN | SA | 10am–3pm (solar sponge) and 1–6am (off-peak) | 45c 5:30–7:30pm |
| Citipower / Powercor / Jemena / AusNet / United | VIC | 9pm–3pm (all but the 3–9pm peak) | 35c 5:30–7:30pm |

## The rules

- **`rules_flow.py`** — the recommended, network-generic script. Keys off the
  `network` variable in inverter_params, so one script works on every network
  above (with safe daytime defaults for TasNetworks/Evoenergy, where Flow Home
  is not offered).
- **`rules_qld.py`** — the minimal QLD/Energex-only version of the same idea.
- **`rules.py`** — the original hand-modelled NSW/Endeavour script (target SOC
  ramps, DNSP fee estimates, SAJ import-power control). Superseded by
  `rules_flow.py`, kept for reference.

`rules_flow.py` lets Powston Magic mode make all the buy/sell decisions and
then applies three tariff fences after the block:

1. **Imports only in the network's cheap windows.** Magic mode already picks
   the cheapest half-hours from the forecast; the fence just confines them to
   where the network rate is lowest. Negative buy prices (getting paid to
   import) are always allowed through.
2. **No exports outside the bonus window.** Feed-in is 0c the rest of the day,
   which also blocks RRP-spike exports that would earn nothing on this plan.
3. **Paced bonus-window export.** Sell only the surplus above the ML-tuned
   `BATTERY_SOC_NEEDED` floor (enough charge to reach tomorrow's solar). Every
   5 minutes the script computes the discharge rate that lands SOC exactly on
   the floor at 7:30pm and starts exporting once that pace reaches
   `EXPORT_POWER_FRACTION` (default 50%) of max power. A big evening load
   shrinks the surplus before it is committed and the pace backs off; a quiet
   evening lets it catch up, up to full power.

The window tables are stored in local standard time and shift +1h
automatically when `interval_time.utcoffset()` shows daylight saving is
active, matching Flow's fixed-AEST billing. QLD is unaffected; SA under DST is
the least-tested corner.

## Verifying changes

Dry-run any edit against a real site with Powston's `simulate_decision_code`
(MCP) before deploying — it replays the candidate script against a live or
historical 5-minute interval and returns the full decision log, including
which rules fired and what Magic mode wanted before the overrides ran.
