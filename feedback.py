"""
Rule-based "how it went" feedback for a completed run.

Pure functions: they take the workout's target paces plus the run's mile splits
and laps (from Strava) and return everything the card needs to draw - verdict,
sentence, chart bars, stat tiles. No AI is involved; every sentence is a fixed
template filled with numbers, and the cut-offs below are the only judgement calls.

Paces are seconds per mile throughout. "low" is the faster end of a range.
"""

MILE_M = 1609.34

ON_TARGET_TOL = 2      # seconds either side of a quality range still counts as on target
EASY_TOL = 5           # looser for easy runs - nobody needs to hit an easy range to the second
KEY_MARGIN = 15        # a mile within this much of the slow end of the range counts as part of the effort
EASY_GAP = 60          # warm-up/cool-down should be at least this much slower than the key effort
MIN_FULL_SPLIT_M = 1450    # ignore a short final partial mile
MIN_LAP_M = 150            # ignore tiny laps (a watch beep at the very end)
STEADY_MAX_SPREAD = 5      # "very even" if key miles are within this many seconds
FAST_CALLOUT_SEC = 10      # how much faster than target before we mention re-pacing

WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}

SUPPORTED_TYPES = ("Easy Run", "Long Run", "Tempo", "Intervals")


def fmt(sec):
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _word(n):
    return WORDS.get(n, str(n))


def _plural(n, singular, plural=None):
    return singular if n == 1 else (plural or singular + "s")


def _paced(items, min_dist):
    """[(pace_sec_per_mile, distance_m, time_s)] for entries long enough to trust."""
    out = []
    for it in items or []:
        d, t = it.get("d") or 0, it.get("t") or 0
        if d >= min_dist and t > 0:
            out.append((t / (d / MILE_M), d, t))
    return out


def _avg_pace(entries):
    dist = sum(e[1] for e in entries)
    time = sum(e[2] for e in entries)
    return time / (dist / MILE_M) if dist else 0


def _compare(avg, low, high, tol):
    """('on'|'fast'|'slow', seconds outside the range)."""
    if avg < low - tol:
        return "fast", round(low - avg)
    if avg > high + tol:
        return "slow", round(avg - high)
    return "on", 0


def _bar_class(pace, low, high, tol, is_key):
    if not is_key:
        return "is-other"
    kind, _ = _compare(pace, low, high, tol)
    return {"on": "is-key", "fast": "is-fast", "slow": "is-slow"}[kind]


def _chart(bars, low=None, high=None):
    """Heights as % (faster = taller) and where the target band sits (None when there's no target)."""
    has_band = low is not None and high is not None
    paces = [b["pace"] for b in bars] + ([low, high] if has_band else [])
    lo, hi = min(paces) - 10, max(paces) + 10
    span = hi - lo

    def height(p):
        return max(6.0, min(100.0, (hi - p) / span * 100))

    n = len(bars)
    return {
        "bars": [{"h": round(height(b["pace"]), 1), "cls": b["cls"], "label": fmt(b["pace"])} for b in bars],
        "band_bottom": round((hi - high) / span * 100, 1) if has_band else None,
        "band_height": round((high - low) / span * 100, 1) if has_band else None,
        "gap": 8 if n <= 6 else (6 if n <= 8 else 3),
        "label_size": 10 if n <= 6 else (9 if n <= 8 else 8),
    }


NO_TARGET_NOTE = "No target pace is set for this session, so there's no verdict - just your splits."
NO_BLOCK_NOTE = "We couldn't find a tempo block in the mile splits - nothing was close to the target pace."
NO_REPS_NOTE = ("We can't see individual reps in this run. Press lap at the start and end of each rep, "
                "or use a structured workout on your watch, to get a rep-by-rep breakdown.")


def _splits_only(splits, totals, note):
    """The card when we can't (or shouldn't) judge the run: just the mile splits and the basics."""
    paces = [s[0] for s in splits]
    avg = _avg_pace(splits)
    n = len(splits)
    if n > 1:
        text = f"{n} {_plural(n, 'mile')} at an average of {fmt(avg)}/mi, from {fmt(min(paces))} to {fmt(max(paces))}."
    else:
        text = f"One mile at {fmt(avg)}/mi."
    bars = [{"pace": p, "cls": "is-key"} for p in paces]
    dist_mi, time_s = totals
    return {"state": "ok", "verdict": "", "tone": "", "headline": text, "chart_title": "Mile splits",
            "band_label": "", "others_label": "", "has_others": False, "callout": "", "note": note,
            "stats": [_stat("Average pace", fmt(avg), "/mi"), _stat("Distance", f"{dist_mi:.1f}", "mi"),
                      _stat("Time", _clock(time_s))],
            **_chart(bars)}


def _mile_chart(splits):
    """A plain mile-splits chart to sit under an interval session's rep chart."""
    if not splits:
        return None
    bars = [{"pace": s[0], "cls": "is-key"} for s in splits]
    return {"title": "Mile splits", **_chart(bars)}


def _stat(label, value, unit="", sub="", tone=""):
    return {"label": label, "value": value, "unit": unit, "sub": sub, "tone": tone}


def _verdict(kind, off, tone_kind):
    """(chip text, tone) for a comparison. tone_kind: 'quality' or 'easy'."""
    if tone_kind == "easy":
        return {"on": ("Stayed easy", "good"),
                "fast": ("A bit quick for an easy day", "warn"),
                "slow": ("Nice and relaxed", "good")}[kind]
    if kind == "on":
        return "On target", "good"
    if kind == "fast":
        return "Faster than target", "ahead"
    return ("Slightly slower than target" if off <= 5 else "Slower than target"), "warn"


def _totals(entries_all):
    dist_mi = sum(e[1] for e in entries_all) / MILE_M
    time_s = sum(e[2] for e in entries_all)
    return dist_mi, time_s


def _clock(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _planned(planned_mi, planned_min):
    return f"planned {planned_mi:g} mi" if planned_mi else ""


def _difference_text(kind, off, noun):
    if kind == "on":
        return "inside your target range"
    if kind == "fast":
        return f"{off}s/mi faster than your target range"
    return f"{off}s/mi slower than your target range"


def build_feedback(workout_type, low, high, mp_low, mp_high, splits, laps, planned_mi, planned_min):
    """Return the card data (state='ok'), or state='no_detail' with a message when Strava gave us nothing to show.

    Mile splits are always shown. With no target pace, or when the session's structure can't be
    found (no tempo block, no laps for intervals), the card falls back to splits plus a note
    instead of a verdict."""
    full_splits = _paced(splits, MIN_FULL_SPLIT_M)
    lap_entries = _paced(laps, MIN_LAP_M) if laps and len(laps) >= 3 else []
    if not full_splits and not lap_entries:
        return {"state": "no_detail", "message": "Strava didn't return splits for this run, so we can't break it down."}

    # Totals use every split (including a short final one) so distance matches the run.
    totals = _totals(_paced(splits, 1)) if splits else _totals(lap_entries)
    has_target = low is not None and high is not None

    if workout_type == "Intervals":
        # Reps can only be told apart from laps. Mile splits blur short reps and
        # their recoveries together, so without laps we say so rather than guess.
        if not has_target:
            result = _splits_only(full_splits or lap_entries, totals, NO_TARGET_NOTE)
        else:
            result = _intervals(low, high, lap_entries, full_splits, totals)
    elif not full_splits:
        return {"state": "no_detail", "message": "Strava didn't return mile splits for this run, so we can't break it down."}
    elif not has_target:
        result = _splits_only(full_splits, totals, NO_TARGET_NOTE)
    elif workout_type == "Tempo":
        result = _tempo(low, high, full_splits, totals)
    elif workout_type == "Long Run" and mp_low and mp_high:
        result = _long_with_marathon_pace(low, high, mp_low, mp_high, full_splits, totals)
    else:
        result = _easy(low, high, full_splits, totals)

    result.setdefault("note", "")
    result.setdefault("mile_chart", None)
    return result


# ------------------------------------------------------------------ easy / plain long run

def _easy(low, high, splits, totals):
    paces = [s[0] for s in splits]
    avg = _avg_pace(splits)
    kind, off = _compare(avg, low, high, EASY_TOL)
    chip, tone = _verdict(kind, off, "easy")
    n = len(splits)
    outside = sum(1 for p in paces if _compare(p, low, high, EASY_TOL)[0] != "on")

    if kind == "on":
        text = f"Average {fmt(avg)}/mi, inside your {fmt(low)}–{fmt(high)} range."
    elif kind == "fast":
        text = f"Average {fmt(avg)}/mi, {off}s/mi quicker than your {fmt(low)}–{fmt(high)} easy range. Easy days work best when they stay relaxed."
    else:
        text = f"Average {fmt(avg)}/mi, {off}s/mi slower than your easy range, which is fine - an easy day can be easier than planned."
    if kind == "on" and n >= 2:
        text += " Every mile was in range." if outside == 0 else f" {outside} of {n} {_plural(n, 'mile')} fell outside it."
    if n >= 4:
        half = n // 2
        first, second = _avg_pace(splits[:half]), _avg_pace(splits[-half:])
        if first - second >= 5 and kind != "fast":
            text += " You got a little quicker as you went without pushing."
        elif second - first >= 10:
            text += " You slowed a little towards the end."

    bars = [{"pace": p, "cls": _bar_class(p, low, high, EASY_TOL, True)} for p in paces]
    dist_mi, time_s = totals
    stats = [
        _stat("Average pace", fmt(avg), "/mi", f"range {fmt(low)}–{fmt(high)}", "ok" if kind == "on" else ("warn" if kind == "fast" else "")),
        _stat("Distance", f"{dist_mi:.1f}", "mi", ""),
        _stat("Time", _clock(time_s), "", ""),
    ]
    return {"state": "ok", "verdict": chip, "tone": tone, "headline": text, "chart_title": "Mile splits",
            "band_label": f"Easy range {fmt(low)}–{fmt(high)}", "others_label": "", "has_others": False,
            "stats": stats, "callout": "", **_chart(bars, low, high)}


# ------------------------------------------------------------------ tempo

def _tempo(low, high, splits, totals):
    threshold = high + KEY_MARGIN
    key = [s for s in splits if s[0] <= threshold]
    others = [s for s in splits if s[0] > threshold]
    if not key:
        return _splits_only(splits, totals, NO_BLOCK_NOTE)

    n = len(key)
    avg = _avg_pace([k for k in key])
    kind, off = _compare(avg, low, high, ON_TARGET_TOL)
    chip, tone = _verdict(kind, off, "quality")
    miles = _plural(n, "mile")
    paces = [k[0] for k in key]
    spread = round(max(paces) - min(paces))

    if kind == "on":
        text = f"You held {fmt(avg)}/mi through the {_word(n)} tempo {miles}, {_difference_text(kind, off, 'tempo')}"
    else:
        text = f"You averaged {fmt(avg)}/mi through the {_word(n)} tempo {miles}, {_difference_text(kind, off, 'tempo')}"
    if n >= 2:
        if spread <= STEADY_MAX_SPREAD:
            text += f", and the miles were within {spread} {_plural(spread, 'second')} of each other."
        else:
            text += f", but pace varied by {spread} seconds between miles."
    else:
        text += "."
    if others:
        # Judged on the average of the non-tempo miles, so one transition mile
        # (half warm-up, half tempo) doesn't read as a too-quick warm-up.
        if _avg_pace(others) >= avg + EASY_GAP:
            text += " Warm-up and cool-down were properly easy."
        else:
            text += " Your warm-up and cool-down were on the quick side - worth keeping those relaxed."

    bars = []
    for s in splits:
        is_key = s[0] <= threshold
        bars.append({"pace": s[0], "cls": _bar_class(s[0], low, high, ON_TARGET_TOL, is_key)})
    dist_mi, time_s = totals
    callout = _faster_callout(kind, off)
    stats = [
        _stat("Tempo block", fmt(avg), "/mi", f"target {fmt(low)}–{fmt(high)}", "ok" if kind == "on" else ("ahead" if kind == "fast" else "warn")),
        _stat("Spread", str(spread), "s", f"across {n} {miles}", "ok" if spread <= STEADY_MAX_SPREAD else "warn"),
        _stat("Session", f"{dist_mi:.1f}", "mi", f"{_clock(time_s)} total"),
    ]
    return {"state": "ok", "verdict": chip, "tone": tone, "headline": text, "chart_title": "Mile splits",
            "band_label": f"Tempo target {fmt(low)}–{fmt(high)}", "others_label": "Warm-up and cool-down",
            "has_others": bool(others), "stats": stats, "callout": callout, **_chart(bars, low, high)}


# ------------------------------------------------------------------ intervals

def _intervals(low, high, laps, splits, totals):
    threshold = high + KEY_MARGIN
    reps = [e for e in laps if e[0] <= threshold]
    if len(reps) < 2 or len(reps) == len(laps):
        # No usable laps (or no clear reps among them): show the mile splits and say why.
        return _splits_only(splits or laps, totals, NO_REPS_NOTE)

    n = len(reps)
    avg = _avg_pace(reps)
    kind, off = _compare(avg, low, high, ON_TARGET_TOL)
    chip, tone = _verdict(kind, off, "quality")
    fade = round(reps[-1][0] - reps[0][0])

    text = f"Your {n} reps averaged {fmt(avg)}/mi, {_difference_text(kind, off, 'reps')}."
    if abs(fade) <= 3:
        text += " Almost no fade from first to last rep."
    elif fade > 0:
        text += f" You faded {fade} seconds from the first rep to the last."
    else:
        text += f" You were {abs(fade)} seconds quicker by the last rep."

    bars = [{"pace": r[0], "cls": _bar_class(r[0], low, high, ON_TARGET_TOL, True)} for r in reps]
    callout = _faster_callout(kind, off)
    stats = [
        _stat("Rep average", fmt(avg), "/mi", f"target {fmt(low)}–{fmt(high)}" if kind == "on" else _difference_short(kind, off),
              "ok" if kind == "on" else ("ahead" if kind == "fast" else "warn")),
        _stat("Fade", f"{'+' if fade > 0 else ''}{fade}", "s", "first to last rep", "ok" if abs(fade) <= 5 else "warn"),
        _stat("Reps found", str(n), "", "in this run"),
    ]
    return {"state": "ok", "verdict": chip, "tone": tone, "headline": text, "chart_title": "Rep pace",
            "band_label": f"Target {fmt(low)}–{fmt(high)}", "others_label": "", "has_others": False,
            "stats": stats, "callout": callout, "mile_chart": _mile_chart(splits), **_chart(bars, low, high)}


def _difference_short(kind, off):
    return f"{off}s/mi {'faster' if kind == 'fast' else 'slower'}"


# ------------------------------------------------------------------ long run with a marathon-pace section

def _long_with_marathon_pace(easy_low, easy_high, mp_low, mp_high, splits, totals):
    threshold = mp_high + KEY_MARGIN
    key = [s for s in splits if s[0] <= threshold]
    easy = [s for s in splits if s[0] > threshold]
    if not key:
        base = _easy(easy_low, easy_high, splits, totals)
        base["headline"] = "We didn't find a marathon-pace section in the mile splits. Overall: " + base["headline"]
        return base

    n = len(key)
    avg = _avg_pace(key)
    kind, off = _compare(avg, mp_low, mp_high, ON_TARGET_TOL)
    chip, tone = _verdict(kind, off, "quality")
    easy_avg = _avg_pace(easy) if easy else 0

    phrase = _difference_text(kind, off, "").replace("target range", "marathon-pace range")
    if easy:
        text = (f"{_word(len(easy)).capitalize()} easy {_plural(len(easy), 'mile')} at {fmt(easy_avg)}/mi, "
                f"then {_word(n)} {_plural(n, 'mile')} at {fmt(avg)}/mi, {phrase}.")
    else:
        text = f"{_word(n).capitalize()} {_plural(n, 'mile')} at {fmt(avg)}/mi, {phrase}."

    bars = [{"pace": s[0], "cls": _bar_class(s[0], mp_low, mp_high, ON_TARGET_TOL, s[0] <= threshold)} for s in splits]
    dist_mi, time_s = totals
    stats = [
        _stat("Easy section", fmt(easy_avg) if easy else "-", "/mi" if easy else "", f"{len(easy)} {_plural(len(easy), 'mile')}"),
        _stat("Marathon pace", fmt(avg), "/mi", f"{n} {_plural(n, 'mile')} · " + ("in range" if kind == "on" else _difference_short(kind, off)),
              "ok" if kind == "on" else ("ahead" if kind == "fast" else "warn")),
        _stat("Session", f"{dist_mi:.1f}", "mi", f"{_clock(time_s)} total"),
    ]
    return {"state": "ok", "verdict": chip, "tone": tone, "headline": text, "chart_title": "Mile splits",
            "band_label": f"Marathon pace {fmt(mp_low)}–{fmt(mp_high)}", "others_label": "Easy miles",
            "has_others": bool(easy), "stats": stats, "callout": _faster_callout(kind, off), **_chart(bars, mp_low, mp_high)}


def _faster_callout(kind, off):
    if kind == "fast" and off >= FAST_CALLOUT_SEC:
        return "Sessions like this can mean your targets are out of date. Your coach can re-pace the plan if it keeps happening."
    return ""
