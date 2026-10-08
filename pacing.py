"""
Training-pace maths: turn a recent race result into easy / marathon / tempo /
interval paces (seconds per mile) using the Daniels-Gilbert VDOT equations.

All paces are seconds per mile; "low" is the faster bound, "high" the slower.
"""
import math

METRES_PER_MILE = 1609.34

RACE_DISTANCES_M = {
    "5k": 5000,
    "10k": 10000,
    "half": 21097.5,
    "marathon": 42195,
}

# Fractions of VDOT (vVO2max) that Daniels uses for each training intensity.
EASY_FAST_FRACTION = 0.67  # Daniels goes to 0.74, but that's too quick for a day that's meant to be easy
EASY_SLOW_FRACTION = 0.62
TEMPO_FRACTION = 0.88
INTERVAL_FRACTION = 0.975

# Single-pace zones become a +/- band so the target reads as a range.
POINT_ZONE_BAND_SEC = 4


def _vo2_at_velocity(v):
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def _fraction_sustained(t_min):
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(distance_m, time_sec):
    t_min = time_sec / 60.0
    v = distance_m / t_min
    return _vo2_at_velocity(v) / _fraction_sustained(t_min)


def _velocity_for_vo2(vo2):
    a, b, c = 0.000104, 0.182258, -(4.60 + vo2)
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def _pace_sec_per_mile(velocity_m_per_min):
    return METRES_PER_MILE / velocity_m_per_min * 60.0


def predict_time_sec(v_dot, distance_m):
    """Race time (seconds) a given VDOT predicts - bisection, since the curve isn't invertible by hand."""
    lo, hi = 5 * 60.0, 12 * 3600.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if vdot(distance_m, mid) > v_dot:
            lo = mid  # still too fast a time -> VDOT too high -> need a longer time
        else:
            hi = mid
    return (lo + hi) / 2


def training_paces(distance_m, time_sec):
    """Zone paces (seconds/mile) from one race result."""
    v_dot = vdot(distance_m, time_sec)

    def at(fraction):
        return _pace_sec_per_mile(_velocity_for_vo2(fraction * v_dot))

    marathon_sec = predict_time_sec(v_dot, RACE_DISTANCES_M["marathon"])
    marathon_pace = marathon_sec / (RACE_DISTANCES_M["marathon"] / METRES_PER_MILE)
    tempo = at(TEMPO_FRACTION)
    interval = at(INTERVAL_FRACTION)
    band = POINT_ZONE_BAND_SEC
    return {
        "vdot": v_dot,
        "easy": (round(at(EASY_FAST_FRACTION)), round(at(EASY_SLOW_FRACTION))),
        "marathon": (round(marathon_pace) - band, round(marathon_pace) + band),
        "tempo": (round(tempo) - band, round(tempo) + band),
        "interval": (round(interval) - band, round(interval) + band),
        "marathon_time_sec": marathon_sec,
    }


def parse_time(text):
    """'19:20' (mm:ss) or '1:34:10' (h:mm:ss) -> seconds, or None if it doesn't parse."""
    try:
        parts = [int(p) for p in text.strip().split(":")]
    except (ValueError, AttributeError):
        return None
    if any(p < 0 for p in parts):
        return None
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s if s < 60 else None
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s if m < 60 and s < 60 else None
    return None


def parse_pace(text):
    """'6:45' or '6:45/mi' -> seconds per mile (3:00-20:00 only), or None."""
    if not text:
        return None
    seconds = parse_time(text.strip().lower().replace("/mi", ""))
    if seconds is None or not 180 <= seconds <= 1200:
        return None
    return seconds


def fmt_pace(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_pace_range(low, high):
    if low is None and high is None:
        return None
    if low is None or high is None or low == high:
        return f"{fmt_pace(low if low is not None else high)}/mi"
    return f"{fmt_pace(low)}–{fmt_pace(high)}/mi"


def fmt_time(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
