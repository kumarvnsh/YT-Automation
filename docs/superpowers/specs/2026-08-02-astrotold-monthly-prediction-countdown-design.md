# Astrotold — Last-9-Days Monthly Prediction Countdown

**Date:** 2026-08-02
**Channel:** astrotold (numerology)
**Status:** Approved design, ready for implementation plan

## Goal

During the last 9 days of every month, the **morning** short becomes a
next-month numerology prediction for one birth number, advancing one birth
number per day (BN1 on the first window day → BN9 on the last day of the
month). Evening shorts and all other days keep the existing random-angle flow.

## Behaviour

### Window + birth-number mapping

- `days_in_month` comes from the calendar for today's month.
- Window = the last 9 calendar days: `day >= days_in_month - 8`.
- Birth number for the day: `BN = day - days_in_month + 9`.
  - First window day (`day == days_in_month - 8`) → BN1.
  - Last day of month (`day == days_in_month`) → BN9.
- Next-month name/year derived from today. December rolls to January of the
  next year.

Worked examples:

| Month (days) | Window        | First day → BN1 | Last day → BN9 |
|--------------|---------------|-----------------|----------------|
| July (31)    | Jul 23–31     | Jul 23          | Jul 31         |
| June (30)    | Jun 22–30     | Jun 22          | Jun 30         |
| Feb (28)     | Feb 20–28     | Feb 20          | Feb 28         |
| Feb (29)     | Feb 21–29     | Feb 21          | Feb 29         |
| Dec (31)     | Dec 23–31     | Dec 23          | Dec 31 (→ Jan next year) |

### Trigger gate (ALL must hold, else fall through to normal angles)

1. Config flag `channel.monthly_prediction: true` — astrotold only; histold and
   any channel without the flag is unaffected.
2. `PUBLISH_SLOT == "morning"` — evening slot and manual runs (no/other slot)
   fall through.
3. Today is inside the last-9-day window.

When the gate does not fire, topic selection is byte-for-byte the existing
`pick_angle` / trend flow — no behavioural change off-window or on the evening
slot.

### Birth-number → dates table

Reused from the config angles (single source in code):

| BN | Dates            |
|----|------------------|
| 1  | 1, 10, 19, 28    |
| 2  | 2, 11, 20, 29    |
| 3  | 3, 12, 21, 30    |
| 4  | 4, 13, 22, 31    |
| 5  | 5, 14, 23        |
| 6  | 6, 15, 24        |
| 7  | 7, 16, 25        |
| 8  | 8, 17, 26        |
| 9  | 9, 18, 27        |

### Forced direction string

For birth number `k` and next month `M YYYY`, `_build_prompt` receives a
REQUIRED-topic direction (same channel as the existing `topic_override` path,
so downstream prompt handling is unchanged):

> `Today's REQUIRED topic (do not deviate): A playful {M} {YYYY} numerology
> prediction ONLY for birth number {k} (people born on {dates for k}). The title
> must name {M} and birth number {k} so it is distinct from other days.`

## Implementation

**Approach A (chosen):** a helper in `src/topics.py`, hooked into
`_build_prompt` in `src/script_generator.py`.

- `src/topics.py`: add `monthly_prediction_direction(cfg, today=None,
  slot=None) -> str | None`. Pure function of `(config flag, slot, date)` —
  `today`/`slot` injectable for tests, defaulting to `date.today()` and
  `os.getenv("PUBLISH_SLOT")`. Returns the forced direction string or `None`.
  Holds the BN→dates table.
- `src/script_generator.py`, `_build_prompt`: before the `topic_override` /
  `series` / angle branch, if `topic_override` is not set, call
  `monthly_prediction_direction(cfg)`. If it returns a string, use it as the
  REQUIRED-topic `direction`; otherwise proceed exactly as today.

Rejected alternatives: (B) external cron passing `--topic` — cron-job.org can't
do date/BN math, needs a wrapper, duplicates calendar logic outside the app;
(C) new `topic_source: "monthly_countdown"` mode — still needs the window gate,
and switching `topic_source` would hijack every slot/day unless gated anyway,
so more rewiring than A for no gain.

### Config

Add to `channels/astrotold/config.yaml` under `channel:`:

```yaml
  monthly_prediction: true
```

## Interactions

- Coexists with the 9 random birth-number angles (added 2026-08-02). Those
  continue to drive the evening slot and every off-window day.
- Titles name month + birth number, so `reserve_topic`'s 0.82 fingerprint
  dedup does not reject the 9 distinct daily predictions.
- No change to histold: gated behind the per-channel `monthly_prediction` flag.

## Test

One `tests/test_monthly_prediction.py` asserting the pure helper:

- July 23 (31-day month), morning, flag on → BN1 direction, text contains
  "August" and "birth number 1".
- July 31, morning → BN9, contains "birth number 9".
- June 30 (30-day month), morning → BN9.
- Dec 31, morning → text contains "January" (next year rollover).
- July 15 (mid-month), morning → `None`.
- July 31, evening → `None`.
- July 31, morning, flag off/absent → `None`.
