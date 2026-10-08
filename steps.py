"""
Structured workout steps: the warm-up / reps / recoveries / cool-down breakdown a watch can follow.

The coach-facing source of truth is a short piece of text, one step per line:

    1.5mi WU
    8x (600m @ 6:03-6:11, 400m jog)
    1mi CD

A step is `<amount> [words] [@ <pace>]`. Amounts: mi, km, m (metres), min, sec. Words such as
WU, CD, jog, easy, tempo, MP pick the kind of step. A pace is `6:03-6:11` (per mile), or one of
`target` (this session's pace range), `easy` (the plan's easy range) or `mp` (marathon pace).
With no `@ pace`, warm-ups, cool-downs, jogs and easy steps use the easy range and everything
else uses the session's own target range. A repeat is `N x ( step, step )`.

Pure functions - no Flask, no database - so the parser can be tested on its own.
"""
import re

from pacing import parse_pace, fmt_pace_range

MILE_M = 1609.34
MAX_STEPS = 40
MAX_REPEAT = 50

UNIT_ALIASES = {
    "miles": "mi", "mile": "mi", "mi": "mi", "km": "km", "k": "km", "m": "m",
    "minutes": "min", "minute": "min", "mins": "min", "min": "min",
    "secs": "sec", "sec": "sec", "s": "sec",
}
_AMOUNT = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(miles|mile|mi|km|minutes|minute|mins|min|secs|sec|s|m|k)(?![A-Za-z])(.*)$", re.IGNORECASE)
_REPEAT = re.compile(r"^\s*(\d+)\s*[x×]\s*\((.*)\)\s*$", re.IGNORECASE)
_PACE = re.compile(r"^~?\s*(\d{1,2}:\d{2})(?:\s*[-–]\s*(\d{1,2}:\d{2}))?\s*(?:/\s*mi)?$", re.IGNORECASE)

KIND_WORDS = {
    "wu": "warmup", "warmup": "warmup", "warm-up": "warmup", "warm": "warmup",
    "cd": "cooldown", "cooldown": "cooldown", "cool-down": "cooldown", "cool": "cooldown",
    "jog": "recover", "rec": "recover", "recovery": "recover", "recover": "recover", "rest": "recover", "float": "recover",
}
INTENSITY_WORDS = {"easy": "easy", "tempo": "key", "steady": "key", "mp": "mp", "marathon": "mp"}


class StepError(ValueError):
    pass


def _split_top_level(text):
    """Split on newlines and semicolons that aren't inside parentheses."""
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if (ch in "\n;") and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_pace_spec(spec):
    """('key'|'easy'|'mp', None) for a keyword, ('range', (low, high)) for numbers; raises StepError."""
    s = spec.strip().lower()
    if s in ("target", "key", "tempo"):
        return "key", None
    if s == "easy":
        return "easy", None
    if s in ("mp", "marathon", "marathon pace"):
        return "mp", None
    m = _PACE.match(s)
    if not m:
        raise StepError(f"I couldn't read the pace '{spec.strip()}' - use something like 6:03-6:11.")
    low = parse_pace(m.group(1))
    high = parse_pace(m.group(2)) if m.group(2) else low
    if low is None or high is None:
        raise StepError(f"'{spec.strip()}' isn't a sensible pace.")
    if low > high:
        low, high = high, low
    return "range", (low, high)


def _parse_step(text):
    m = _AMOUNT.match(text)
    if not m:
        raise StepError(f"'{text.strip()}' needs an amount first, like 1.5mi, 600m or 10min.")
    amount, unit, rest = float(m.group(1)), UNIT_ALIASES[m.group(2).lower()], m.group(3)
    if amount <= 0:
        raise StepError("A step has to be longer than zero.")

    before, _, after = rest.partition("@")
    words = re.findall(r"[A-Za-z][A-Za-z-]*", before.lower())
    kind, intensity, label = "run", None, ""
    for w in words:
        if w in KIND_WORDS and kind == "run":
            kind = KIND_WORDS[w]
        if w in INTENSITY_WORDS and intensity is None:
            intensity = INTENSITY_WORDS[w]
            label = {"easy": "Easy", "key": w.capitalize() if w in ("tempo", "steady") else "", "mp": "Marathon pace"}[intensity] or label

    pace = None
    if after.strip():
        mode, value = _parse_pace_spec(after)
        if mode == "range":
            pace = {"mode": "range", "low": value[0], "high": value[1]}
        else:
            pace = {"mode": mode}
    if kind != "run" and pace is None:
        pace = {"mode": "easy"}                     # warm-up, cool-down and recoveries default to easy
    elif pace is None:
        pace = {"mode": {"easy": "easy", "mp": "mp"}.get(intensity, "key")}
    return {"kind": kind, "amount": amount, "unit": unit, "pace": pace, "label": label}


def parse(text):
    """-> (steps, errors). `steps` is a list of step dicts / repeat dicts; errors are human-readable."""
    steps, errors = [], []
    for number, line in enumerate(_split_top_level(text or ""), start=1):
        try:
            rep = _REPEAT.match(line)
            if rep:
                count = int(rep.group(1))
                if not 1 <= count <= MAX_REPEAT:
                    raise StepError(f"Repeats should be between 1 and {MAX_REPEAT}.")
                inner = [p.strip() for p in rep.group(2).split(",") if p.strip()]
                if not inner:
                    raise StepError("A repeat needs at least one step inside the brackets.")
                steps.append({"kind": "repeat", "count": count, "steps": [_parse_step(p) for p in inner]})
            else:
                steps.append(_parse_step(line))
        except StepError as exc:
            errors.append(f"Step {number}: {exc}")
    if len(steps) > MAX_STEPS:
        errors.append(f"That's {len(steps)} steps - the limit is {MAX_STEPS}.")
    return steps, errors


def distance_m(step):
    unit = step["unit"]
    if unit == "mi":
        return step["amount"] * MILE_M
    if unit == "km":
        return step["amount"] * 1000
    if unit == "m":
        return step["amount"]
    return 0


def total_distance_mi(steps):
    """Distance covered by the distance-based steps (time-based steps can't be counted)."""
    total = 0.0
    for s in steps:
        if s["kind"] == "repeat":
            total += s["count"] * sum(distance_m(i) for i in s["steps"])
        else:
            total += distance_m(s)
    return total / MILE_M


def _amount_text(step):
    amount = step["amount"]
    shown = f"{amount:g}"
    return f"{shown} {step['unit']}"


_TITLES = {"warmup": "Warm-up", "cooldown": "Cool-down", "recover": "Recovery"}


def _resolve_pace(pace, key_range, easy_range, mp_range):
    if pace["mode"] == "range":
        return pace["low"], pace["high"]
    return {"key": key_range, "easy": easy_range, "mp": mp_range}.get(pace["mode"])


def _row(step, key_range, easy_range, mp_range, session_type, in_repeat):
    rng = _resolve_pace(step["pace"], key_range, easy_range, mp_range)
    mode = step["pace"]["mode"]
    if step["kind"] in _TITLES:
        title = _TITLES[step["kind"]]
    elif step["label"]:
        title = step["label"]
    elif mode == "easy":
        title = "Easy"
    elif mode == "mp":
        title = "Marathon pace"
    elif in_repeat:
        title = "Rep"
    elif session_type == "Tempo":
        title = "Tempo"
    else:
        title = "Run"
    return {"type": "step", "kind": step["kind"], "title": title, "amount": _amount_text(step),
            "pace": fmt_pace_range(*rng) if rng else None}


def describe(steps, key_range=None, easy_range=None, mp_range=None, session_type=None):
    """Display rows for the athlete's workout page, with each step's pace resolved."""
    rows = []
    for s in steps:
        if s["kind"] == "repeat":
            rows.append({"type": "repeat", "count": s["count"],
                         "steps": [_row(i, key_range, easy_range, mp_range, session_type, True) for i in s["steps"]]})
        else:
            rows.append(_row(s, key_range, easy_range, mp_range, session_type, False))
    return rows


# ------------------------------------------------------------------ drafting from the notes text

_NUM = r"(\d+(?:\.\d+)?)"
_WU = re.compile(_NUM + r"\s*mi\s*(?:easy\s*)?(?:WU|warm[- ]?up)", re.IGNORECASE)
_CD = re.compile(_NUM + r"\s*mi\s*(?:easy\s*)?(?:CD|cool[- ]?down)", re.IGNORECASE)
_TEMPO_BLOCK = re.compile(_NUM + r"\s*mi\s*(?:@|at)\s*tempo", re.IGNORECASE)
_REPS = re.compile(r"(\d+)\s*[x×]\s*" + _NUM + r"\s*(mi|m|km)\b", re.IGNORECASE)
_RECOVERY_DIST = re.compile(_NUM + r"\s*(mi|m|km)\s*(?:easy\s*)?(?:jog|recovery|rec)\b", re.IGNORECASE)
_RECOVERY_TIME = re.compile(_NUM + r"\s*(sec|secs|s|min|mins)\s*(?:easy\s*)?(?:jog|recovery|rec|rest)\b", re.IGNORECASE)
_EASY_THEN_MP = re.compile(_NUM + r"\s*mi\s*easy,?\s*(?:then\s*)?(?:the\s*)?last\s*" + _NUM + r"\s*mi", re.IGNORECASE)


def _n(value):
    return f"{float(value):g}"


def draft_from_notes(workout_type, distance_mi, notes):
    """Best-effort draft of the steps text from a workout's distance and notes.

    Returns (text, warnings) or (None, []) when nothing sensible can be drafted. It is only ever a
    draft - the coach confirms it before it's saved."""
    notes = notes or ""
    warnings = []

    if workout_type == "Easy Run":
        return (f"{_n(distance_mi)}mi easy", []) if distance_mi else (None, [])

    if workout_type == "Long Run":
        m = _EASY_THEN_MP.search(notes)
        if m:
            return f"{_n(m.group(1))}mi easy\n{_n(m.group(2))}mi @ mp", []
        if re.search(r"marathon pace|\bMP\b", notes, re.IGNORECASE):
            return None, []                      # mentions marathon pace but not in a shape we can read
        return (f"{_n(distance_mi)}mi easy", []) if distance_mi else (None, [])

    wu, cd = _WU.search(notes), _CD.search(notes)
    lines = []
    if wu:
        lines.append(f"{_n(wu.group(1))}mi WU")

    if workout_type == "Tempo":
        block = _TEMPO_BLOCK.search(notes)
        if not block:
            return None, []
        lines.append(f"{_n(block.group(1))}mi @ target")
    elif workout_type == "Intervals":
        reps = _REPS.search(notes)
        if not reps:
            return None, []
        rec_d, rec_t = _RECOVERY_DIST.search(notes), _RECOVERY_TIME.search(notes)
        rep_step = f"{_n(reps.group(2))}{reps.group(3).lower()} @ target"
        if rec_d:
            rec_step = f"{_n(rec_d.group(1))}{rec_d.group(2).lower()} jog"
        elif rec_t:
            unit = "sec" if rec_t.group(2).lower().startswith("s") else "min"
            rec_step = f"{_n(rec_t.group(1))}{unit} jog"
        else:
            rec_step = None
            warnings.append("No recovery between the reps was found in the notes - add one if there is one.")
        inner = rep_step + (f", {rec_step}" if rec_step else "")
        lines.append(f"{int(reps.group(1))}x ({inner})")
    else:
        return None, []

    if cd:
        lines.append(f"{_n(cd.group(1))}mi CD")
    if not wu and not cd:
        warnings.append("No warm-up or cool-down was found in the notes.")
    return "\n".join(lines), warnings
