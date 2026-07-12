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

# Flow Home FY26 (QLD / Energex). Two tariff facts drive everything below:
#   - Buy is wholesale-linked, cheapest during the 11am-4pm solar soaker window.
#   - Sell is 0c at ALL times except a fixed 45c/kWh bonus window 5:30pm-7:30pm.
# Magic mode above already picks the best import windows and protects the
# battery, so the only job here is to fence its decisions to those two windows.
# QLD has no daylight saving, so hours are plain local time.

FLOW_SOLAR_SOAK_START_HOUR = 11  # solar soaker window 11:00am-3:59pm
FLOW_SOLAR_SOAK_END_HOUR = 16
BONUS_START = 17.5  # 45c/kWh export bonus window 5:30pm-7:30pm
BONUS_END = 19.5
EXPORT_POWER_FRACTION = 0.5  # ideal export rate as a fraction of max - spreads the dump wide with headroom to catch up

hour_now = interval_time.hour + interval_time.minute / 60

# Flow does not charge for negative exports on this plan, so never curtail -
# letting surplus solar flow after the battery is full keeps monitoring accurate.
feed_in_power_limitation = None

# 1) Grid imports only during the solar soaker window. Magic mode already picks
# the cheapest half-hours - this just confines them to where the network rate is
# cheapest. Negative prices (getting paid to import) are always allowed through.
in_soaker = FLOW_SOLAR_SOAK_START_HOUR <= interval_time.hour < FLOW_SOLAR_SOAK_END_HOUR
if 'import' in action and not in_soaker and buy_price > 0:
    action = decisions.reason('auto', 'Flow QLD: grid import only in the 11am-4pm solar soaker window',
                              priority=5, buy_price=buy_price)

# 2) No exports outside the bonus window - feed-in is 0c, including RRP spikes.
in_bonus = BONUS_START <= hour_now < BONUS_END
if 'export' in action and not in_bonus:
    action = decisions.reason('auto', 'Flow QLD: feed-in is 0c outside the 5:30-7:30pm bonus window',
                              priority=5)

# 3) Bonus window: sell at 45c, but only the surplus above BATTERY_SOC_NEEDED -
# the ML-tuned SOC the site needs to make it through to tomorrow's solar. Pace
# the sale rather than dump it: each 5 minutes compute the battery-side rate
# that lands SOC exactly on the floor at window close, and start once that pace
# reaches EXPORT_POWER_FRACTION of max power. Spread over more of the window
# with headroom both ways, an unusual evening (guests, ovens, EV plugged in)
# just slows the pace down, and a quiet one speeds it up - up to full power.
if in_bonus and battery_soc is not None and battery_capacity:
    soc_floor = max(BATTERY_SOC_NEEDED, min_soc)
    surplus_wh = max(0.0, battery_soc - soc_floor) / 100 * battery_capacity
    max_export_w = optimal_discharging if optimal_discharging else 5000
    hours_left = max(BONUS_END - hour_now, 1 / 12)  # never below one 5-min interval
    pace_w = surplus_wh / hours_left  # discharge rate that empties the surplus right at window close
    if battery_soc > soc_floor and pace_w >= max_export_w * EXPORT_POWER_FRACTION:
        optimal_discharging = int(min(max_export_w, pace_w))
        action = decisions.reason('export', f'Flow bonus window 45c/kWh: pacing {round(surplus_wh / 1000, 1)}kWh '
                                  f'surplus at {round(optimal_discharging / 1000, 1)}kW to land on '
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
