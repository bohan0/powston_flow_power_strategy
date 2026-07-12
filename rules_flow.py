# PowstonAutoTuned: Decision Script
min_soc = 5  # % Minimum battery SOC
action = decisions.reason('auto', 'Starting in auto mode - default', buy_price=buy_price, sell_price=sell_price)
c_rating = 6  # number of half hours to charge/discharge
peak_hour_start = 16  # noqa
soaker_hour_start = 11  # noqa
# Pricing Decisions - ALL CAPS variables are tuned by machine learning
GOOD_SUN_DAY = 32
BATTERY_SOC_NEEDED = 49  # noqa
GOOD_SUN_HOUR = 6  # noqa
BAD_SUN_DAY_KEEP_SOC = 33  # noqa
ALWAYS_IMPORT_SOC = 21  # The battery SOC at which we always import
DEFAULT_EXPORT = 15

# PowstonBlock

# Custom code

# Flow Home FY26 - all networks. Two tariff facts drive everything below:
#   - Buy is wholesale-linked; each network has cheap windows (solar soaker /
#     off-peak) where the network component is lowest.
#   - Sell is 0c at ALL times except the 5:30pm-7:30pm bonus window
#     (45c/kWh in NSW/QLD/SA, 35c/kWh in VIC) - same window in every state.
# Magic mode above already picks the best import half-hours and protects the
# battery, so the custom code just fences its decisions to the tariff windows.

# Cheap import windows per network, (start_hour, end_hour) in local STANDARD
# time, end exclusive, may wrap midnight. From the Flow Home TOU fact sheets:
FLOW_CHEAP_WINDOWS = {
    'energex': [(11, 16)],             # off-peak 11am-4pm
    'ausgrid': [(21, 15)],             # off-peak 9pm-3pm (all but the 3-9pm peak)
    'endeavour': [(10, 14)],           # N71 solar soak 10am-2pm (overnight off-peak network fee is ~3x the soak)
    'essential': [(10, 15), (22, 7)],  # off-peak 10am-3pm and 10pm-7am
    'sapn': [(10, 15), (1, 6)],        # solar sponge 10am-3pm and off-peak 1-6am
    'citipower': [(21, 15)],           # VIC networks: off-peak 9pm-3pm
    'powercor': [(21, 15)],
    'jemena': [(21, 15)],
    'ausnet': [(21, 15)],
    'united': [(21, 15)],
    # Flow Home is not currently offered on these networks - safe daytime defaults
    'tasnetworks': [(10, 16)],
    'evoenergy': [(10, 16)],
}
VIC_NETWORKS = ['citipower', 'powercor', 'jemena', 'ausnet', 'united']

net = (network or '').lower()
cheap_windows = FLOW_CHEAP_WINDOWS.get(net, [(11, 16)])
BONUS_RATE = 35 if net in VIC_NETWORKS else 45  # c/kWh, used for reporting only
BONUS_START = 17.5  # bonus export window 5:30pm-7:30pm local standard time
BONUS_END = 19.5
EXPORT_POWER_FRACTION = 0.5  # ideal export rate as a fraction of max - spreads the dump wide with headroom to catch up

# Flow bills its windows in AEST year-round (their known DST quirk), so during
# daylight saving every window lands one hour LATER on the local clock. SA
# standard time is UTC+9:30; the rest of the NEM is UTC+10. QLD has no DST.
std_utc_offset = 9.5 if net == 'sapn' else 10.0
utc_off = interval_time.utcoffset()
dst_shift = 1 if (utc_off is not None and utc_off.total_seconds() / 3600 > std_utc_offset) else 0


def in_time_windows(hour, windows, shift):
    for w_start, w_end in windows:
        w_start = (w_start + shift) % 24
        w_end = (w_end + shift) % 24
        if w_start <= w_end:
            if w_start <= hour < w_end:
                return True
        elif hour >= w_start or hour < w_end:  # window wraps midnight
            return True
    return False


hour_now = interval_time.hour + interval_time.minute / 60
bonus_start = BONUS_START + dst_shift
bonus_end = BONUS_END + dst_shift

# Flow does not charge for negative exports on this plan, so never curtail -
# letting surplus solar flow after the battery is full keeps monitoring accurate.
feed_in_power_limitation = None

# 1) Grid imports only during the network's cheap windows. Magic mode already
# picks the cheapest half-hours - this just confines them to where the network
# rate is lowest. Negative prices (getting paid) are always allowed through.
in_cheap_window = in_time_windows(interval_time.hour, cheap_windows, dst_shift)
if 'import' in action and not in_cheap_window and buy_price > 0:
    action = decisions.reason('auto', f'Flow {net or "unknown network"}: grid import only in cheap windows '
                              f'{cheap_windows} (+{dst_shift}h DST)',
                              priority=5, buy_price=buy_price)

# 2) No exports outside the bonus window - feed-in is 0c, including RRP spikes.
in_bonus = bonus_start <= hour_now < bonus_end
if 'export' in action and not in_bonus:
    action = decisions.reason('auto', f'Flow: feed-in is 0c outside the {BONUS_RATE}c bonus window '
                              f'({bonus_start}-{bonus_end}h)',
                              priority=5)

# 3) Bonus window: sell the surplus above BATTERY_SOC_NEEDED - the ML-tuned SOC
# the site needs to make it through to tomorrow's solar. Pace the sale rather
# than dump it: each 5 minutes compute the battery-side rate that lands SOC
# exactly on the floor at window close, and start once that pace reaches
# EXPORT_POWER_FRACTION of max power. Spread over more of the window with
# headroom both ways, an unusual evening (guests, ovens, EV plugged in) just
# slows the pace down, and a quiet one speeds it up - up to full power.
if in_bonus and battery_soc is not None and battery_capacity:
    soc_floor = max(BATTERY_SOC_NEEDED, min_soc)
    surplus_wh = max(0.0, battery_soc - soc_floor) / 100 * battery_capacity
    max_export_w = optimal_discharging if optimal_discharging else 5000
    hours_left = max(bonus_end - hour_now, 1 / 12)  # never below one 5-min interval
    pace_w = surplus_wh / hours_left  # discharge rate that empties the surplus right at window close
    if battery_soc > soc_floor and pace_w >= max_export_w * EXPORT_POWER_FRACTION:
        optimal_discharging = int(min(max_export_w, pace_w))
        action = decisions.reason('export', f'Flow bonus window {BONUS_RATE}c/kWh: pacing '
                                  f'{round(surplus_wh / 1000, 1)}kWh surplus at '
                                  f'{round(optimal_discharging / 1000, 1)}kW to land on '
                                  f'{round(soc_floor)}% at window close',
                                  priority=5, battery_soc=battery_soc, soc_floor=soc_floor,
                                  pace_w=int(pace_w), hours_left=round(hours_left, 2))
    elif 'export' in action:
        if battery_soc > soc_floor:
            action = decisions.reason('auto', 'Flow bonus window: surplus still fits later at an easy pace - '
                                      'letting tonight\'s real house usage settle before we commit it',
                                      priority=5, battery_soc=battery_soc, soc_floor=soc_floor,
                                      pace_w=int(pace_w), hours_left=round(hours_left, 2))
        else:
            action = decisions.reason('auto', 'Flow bonus window: holding - battery needed to reach tomorrow\'s solar',
                                      priority=5, battery_soc=battery_soc, soc_floor=soc_floor)
