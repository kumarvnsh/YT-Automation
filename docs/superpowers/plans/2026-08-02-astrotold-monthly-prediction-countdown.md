# Astrotold Last-9-Days Monthly Prediction Countdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** During the last 9 days of every month, make the astrotold morning short a next-month numerology prediction advancing one birth number per day (BN1 on the first window day → BN9 on the last day of the month).

**Architecture:** A pure helper `monthly_prediction_direction(cfg, today, slot)` in `src/topics.py` computes whether today's morning slot is inside the last-9-day window and, if so, returns a forced REQUIRED-topic direction for that day's birth number. `_build_prompt` in `src/script_generator.py` calls it before the normal angle branch; when it returns `None` (off-window, evening slot, or flag off) nothing changes. Gated per-channel by a new `channel.monthly_prediction` config flag so histold is untouched.

**Tech Stack:** Python 3, stdlib `calendar`/`datetime`/`os`, `unittest` (repo convention). Run tests with `.venv/bin/python -m unittest ... -v`.

**Spec:** `docs/superpowers/specs/2026-08-02-astrotold-monthly-prediction-countdown-design.md`

---

## File Structure

- `src/topics.py` — add the `_BIRTH_NUMBER_DATES` table and the `monthly_prediction_direction` helper. Pure, no I/O beyond reading `PUBLISH_SLOT` env when `slot` not supplied. Lives next to the existing `pick_angle` angle logic it complements.
- `src/script_generator.py` — one new branch in `_build_prompt` plus one import name.
- `channels/astrotold/config.yaml` — add `monthly_prediction: true` under `channel:`.
- `tests/test_monthly_prediction.py` — new unittest module for the helper.

---

### Task 1: `monthly_prediction_direction` helper

**Files:**
- Modify: `src/topics.py` (add near the other angle helpers, e.g. after `pick_angle`)
- Test: `tests/test_monthly_prediction.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_monthly_prediction.py`:

```python
from __future__ import annotations

import unittest
from datetime import date

from src import topics


class _Cfg:
    """Minimal config stub: only answers the monthly_prediction flag."""

    def __init__(self, flag):
        self._flag = flag

    def get(self, path, default=None):
        if path == "channel.monthly_prediction":
            return self._flag
        return default


class MonthlyPredictionTests(unittest.TestCase):
    def test_first_window_day_is_bn1(self):
        # July has 31 days; window is Jul 23-31, so Jul 23 -> BN1.
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 23), slot="morning")
        self.assertIsNotNone(d)
        self.assertIn("birth number 1", d)
        self.assertIn("August", d)

    def test_last_day_is_bn9(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 31), slot="morning")
        self.assertIn("birth number 9", d)
        self.assertIn("August", d)

    def test_thirty_day_month_last_day_is_bn9(self):
        # June has 30 days; window is Jun 22-30, so Jun 30 -> BN9.
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 6, 30), slot="morning")
        self.assertIn("birth number 9", d)
        self.assertIn("July", d)

    def test_december_rolls_to_january_next_year(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 12, 31), slot="morning")
        self.assertIn("birth number 9", d)
        self.assertIn("January 2027", d)

    def test_mid_month_returns_none(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 15), slot="morning")
        self.assertIsNone(d)

    def test_evening_slot_returns_none(self):
        d = topics.monthly_prediction_direction(_Cfg(True), today=date(2026, 7, 31), slot="evening")
        self.assertIsNone(d)

    def test_flag_off_returns_none(self):
        d = topics.monthly_prediction_direction(_Cfg(False), today=date(2026, 7, 31), slot="morning")
        self.assertIsNone(d)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_monthly_prediction -v`
Expected: FAIL — `AttributeError: module 'src.topics' has no attribute 'monthly_prediction_direction'`

- [ ] **Step 3: Write minimal implementation**

At the top of `src/topics.py`, ensure `calendar` and `os` are imported (add to the existing import block; `json`, `random`, `re`, `date` are already imported):

```python
import calendar
import os
```

Then add, right after the `pick_angle` function:

```python
# Numerology birth-number -> the birth dates that reduce to it. Same grouping
# as the astrotold config angles; kept here so the monthly countdown and the
# angle bank share one source of truth.
_BIRTH_NUMBER_DATES = {
    1: "1, 10, 19, and 28",
    2: "2, 11, 20, and 29",
    3: "3, 12, 21, and 30",
    4: "4, 13, 22, and 31",
    5: "5, 14, and 23",
    6: "6, 15, and 24",
    7: "7, 16, and 25",
    8: "8, 17, and 26",
    9: "9, 18, and 27",
}


def monthly_prediction_direction(cfg, today: date | None = None, slot: str | None = None) -> str | None:
    """Forced next-month prediction topic for the last-9-days morning countdown.

    Returns a REQUIRED-topic direction string when ALL hold, else None:
      - channel opted in via `channel.monthly_prediction`
      - this run is the morning slot (PUBLISH_SLOT, or the passed `slot`)
      - today is within the last 9 calendar days of the month

    Birth number advances one per day: the first window day is BN1 and the last
    day of the month is BN9. December rolls the prediction to January next year.
    `today`/`slot` are injectable for tests.
    """
    if cfg is None or not cfg.get("channel.monthly_prediction", False):
        return None
    if slot is None:
        slot = os.getenv("PUBLISH_SLOT")
    if slot != "morning":
        return None
    today = today or date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day < days_in_month - 8:
        return None
    birth_number = today.day - days_in_month + 9  # 1..9
    dates = _BIRTH_NUMBER_DATES[birth_number]
    if today.month == 12:
        next_year, next_month = today.year + 1, 1
    else:
        next_year, next_month = today.year, today.month + 1
    month_name = calendar.month_name[next_month]
    return (
        f"Today's REQUIRED topic (do not deviate): A playful {month_name} {next_year} "
        f"numerology prediction ONLY for birth number {birth_number} (people born on "
        f"{dates}). The title must name {month_name} and birth number {birth_number} "
        f"so it is distinct from other days."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_monthly_prediction -v`
Expected: PASS — 7 tests OK

- [ ] **Step 5: Commit**

```bash
git add src/topics.py tests/test_monthly_prediction.py
git commit -m "feat: monthly_prediction_direction helper for last-9-days countdown"
```

---

### Task 2: Hook the countdown into `_build_prompt`

**Files:**
- Modify: `src/script_generator.py:21` (import), `src/script_generator.py:151-161` (branch)

- [ ] **Step 1: Add the import**

Change line 21 from:

```python
from .topics import recent_titles, pick_angle, performance_examples, series_turn
```

to:

```python
from .topics import (
    recent_titles,
    pick_angle,
    performance_examples,
    series_turn,
    monthly_prediction_direction,
)
```

- [ ] **Step 2: Add the countdown branch**

In `_build_prompt`, replace this block (currently lines 151-161):

```python
    series = None if topic_override else series_turn(cfg)
    if topic_override:
        direction = f"Today's REQUIRED topic (do not deviate): {topic_override}"
    elif series:
        direction = _series_direction(cfg, series)
    else:
        source = cfg.get("channel.topic_source", "angles")
        if source in ("trends", "on_this_day", "blend"):
            direction = _trend_direction(cfg, source)
        else:
            direction = f"Today's creative angle to explore: {pick_angle(cfg)}"
```

with:

```python
    # Last-9-days monthly prediction countdown (astrotold morning slot). Returns
    # None off-window / evening / when the channel has not opted in.
    countdown = None if topic_override else monthly_prediction_direction(cfg)
    series = None if (topic_override or countdown) else series_turn(cfg)
    if topic_override:
        direction = f"Today's REQUIRED topic (do not deviate): {topic_override}"
    elif countdown:
        direction = countdown
    elif series:
        direction = _series_direction(cfg, series)
    else:
        source = cfg.get("channel.topic_source", "angles")
        if source in ("trends", "on_this_day", "blend"):
            direction = _trend_direction(cfg, source)
        else:
            direction = f"Today's creative angle to explore: {pick_angle(cfg)}"
```

- [ ] **Step 3: Verify nothing broke**

Run: `.venv/bin/python -m unittest tests.test_monthly_prediction tests.test_topics -v`
Expected: PASS — all tests OK (the helper import resolves and existing topic tests still pass)

- [ ] **Step 4: Smoke-check the wiring end to end**

Run:

```bash
PUBLISH_SLOT=morning .venv/bin/python -c "from datetime import date; from src import topics; from src.config import load_config; cfg=load_config('channels/astrotold/config.yaml'); print(topics.monthly_prediction_direction(cfg, today=date(2026,7,31), slot='morning'))"
```

Expected: prints the BN9 August direction string (confirms the astrotold config flag from Task 3 is read; run this step after Task 3, or expect `None` until the flag is added).

- [ ] **Step 5: Commit**

```bash
git add src/script_generator.py
git commit -m "feat: wire monthly prediction countdown into script prompt"
```

---

### Task 3: Enable the flag for astrotold

**Files:**
- Modify: `channels/astrotold/config.yaml` (under `channel:`)

- [ ] **Step 1: Add the config flag**

In `channels/astrotold/config.yaml`, under the `channel:` block (e.g. right after the `trend_region: "india"` line), add:

```yaml
  monthly_prediction: true
```

- [ ] **Step 2: Verify config parses and the flag is read**

Run:

```bash
.venv/bin/python -c "from src.config import load_config; cfg=load_config('channels/astrotold/config.yaml'); print('flag=', cfg.get('channel.monthly_prediction'))"
```

Expected: `flag= True`

- [ ] **Step 3: Verify the full wiring now fires**

Run:

```bash
PUBLISH_SLOT=morning .venv/bin/python -c "from datetime import date; from src import topics; from src.config import load_config; cfg=load_config('channels/astrotold/config.yaml'); print(topics.monthly_prediction_direction(cfg, today=date(2026,7,31), slot='morning'))"
```

Expected: prints the "... August 2026 numerology prediction ONLY for birth number 9 ..." direction.

- [ ] **Step 4: Confirm off-window / evening is unaffected**

Run:

```bash
.venv/bin/python -c "from datetime import date; from src import topics; from src.config import load_config; cfg=load_config('channels/astrotold/config.yaml'); print('mid:', topics.monthly_prediction_direction(cfg, today=date(2026,7,15), slot='morning')); print('eve:', topics.monthly_prediction_direction(cfg, today=date(2026,7,31), slot='evening'))"
```

Expected: `mid: None` and `eve: None`

- [ ] **Step 5: Commit**

```bash
git add channels/astrotold/config.yaml
git commit -m "feat: enable monthly prediction countdown for astrotold"
```

---

## Self-Review

**Spec coverage:**
- Window + BN mapping (`day >= days_in_month - 8`, `BN = day - days_in_month + 9`) → Task 1 impl + `test_first_window_day_is_bn1` / `test_last_day_is_bn9` / `test_thirty_day_month_last_day_is_bn9`.
- December → January next-year rollover → Task 1 + `test_december_rolls_to_january_next_year`.
- Trigger gate (flag + morning slot + window) → Task 1 impl + `test_evening_slot_returns_none` / `test_flag_off_returns_none` / `test_mid_month_returns_none`.
- Forced direction string with month + BN + dates → Task 1 return value.
- BN→dates table reused as single source → `_BIRTH_NUMBER_DATES` in Task 1.
- Hook before normal angle flow, off-window unchanged → Task 2.
- Config flag, astrotold only, histold untouched → Task 3 (histold has no flag → helper returns None).
- Coexists with 9 random angles / dedup by distinct title → titles name month+BN (direction instructs it); no code needed beyond Task 1's string.
- Test module → Task 1 `tests/test_monthly_prediction.py`.

No gaps.

**Placeholder scan:** No TBD/TODO/"handle edge cases" — all steps contain concrete code and exact commands.

**Type consistency:** Helper name `monthly_prediction_direction` and constant `_BIRTH_NUMBER_DATES` identical across Tasks 1–2. `cfg.get("channel.monthly_prediction", False)` matches the flag key added in Task 3. Import list in Task 2 matches the definition in Task 1.
