# Plan — encode the four-beat looping structure into the script pipeline

**Goal:** make every generated short structurally match the three videos that
exceeded 100% average view percentage, and make it impossible for a script
that breaks the structure to reach upload.

**Evidence:** channel retention data, 2026-06-28 → 2026-07-19.

| Video | Length | Avg watched | Avg view % |
|---|---|---|---|
| The Ship That Crossed the Atlantic | 50s | 74s | 148.6% |
| They Invented Writing—Then Disappeared | 49s | 73s | 151.0% |
| The Titanic Had a Bakery, a Pool | 44s | 49s | 112.3% |
| She Walked Into a Parliament | 26s | 10s | 40.3% |

Every video above 100% is 44s+. No video under 30s loops.

---

## 0. Root cause — fix first, independent of everything else

`config.yaml:36`

```yaml
shorts_target_seconds: 20      # was 45 until commit 886d4a5 (2026-07-10)
```

Commit `886d4a5` dropped this 45 → 20. Uploads from 2026-07-12 onward run
21–29s and average 10–14s watched, against 44–74s for the 45s-era uploads —
roughly a 6x loss of watch time per view, the primary Shorts ranking input.

**Change:** restore `shorts_target_seconds: 45`.

At 150 wpm that puts the word budget at 112 words, with the quality gate's
0.75–1.35 ratio giving a 84–151 word window. The three loopers ran 120–145
words. This alone should recover most of the loss; everything below is about
making the structure reliable rather than accidental.

---

## 1. Beat 1 — hook with a gap

### Current state

`src/script_generator.py:125-129`

```
- The FIRST segment is the hook and MUST be 8-15 words. It must NOT open with a
  dry date/setup ("In 1915, Alice Ball was a chemist who..."). Instead lead with
  the consequence, twist, or stakes, e.g. "She cured leprosy. Then a man stole
  her credit." or "This law could send you to prison for reading it."
```

Two problems.

**The exemplar teaches the failure mode.** "She cured leprosy. Then a man stole
her credit." is a complete, resolved story in nine words. Nothing is left open.
That is structurally identical to the 40%-retention video's opening, whose
second sentence closed the loop the first sentence opened — and 70% of viewers
left by the one-third mark.

**8–15 words is too short to hold a gap.** The three loopers' hooks run 15–22
words. "What if the alphabet you're reading right now was invented by a
civilization most people have completely forgotten?" is 18 words and resolves
nothing.

### Change

Raise to 15–22 words. Replace the exemplars with the three patterns that
actually worked on this channel, and state the resolution rule explicitly:

```
- Segment 1 is the HOOK: 15-22 words. It must state a claim containing an
  unresolved gap — the viewer must finish it wanting the answer.
  Three patterns that work on this channel:
    * Implicate the viewer — "What if the alphabet you're reading right now
      was invented by a civilization most people have completely forgotten?"
    * Unbeaten superlative — "A ship crossed the Atlantic so fast, no
      passenger liner has beaten it since."
    * Number + contradiction — "The Titanic received six iceberg warnings the
      day it sank and ignored every single one."
  HARD RULE: segment 2 must NOT fully resolve the hook. If the hook can be
  answered in one sentence, it is too small — widen it.
  Never open with a dry date/setup ("In 1915, Alice Ball was a chemist who...").
```

---

## 2. Beat 2 — setup

### Current state

No named beat. The prompt only says to bury the date "in segment 2, never
segment 1" — a negative constraint with no positive shape.

### Change

Name it and require one hard number:

```
- Segment 2 is the SETUP: who, when, where, ~25 words. Land exactly one hard
  number here (a date, duration, price, count, distance). Partially answer the
  hook. Never fully.
```

---

## 3. Beat 3 — pivot + fact stack (largest gap)

### Current state

**Absent entirely.** Nothing in the prompt, the config persona, or the quality
gate references a mid-script turn. All three loopers have an explicit pivot
phrase at roughly the one-third mark — exactly where the weak videos lose 70%
of viewers:

- "But here's the twist. The ship was secretly designed as a military vessel."
- "What most people don't know, the Titanic had a heated swimming pool..."
- "But because they recorded history on perishable papyrus, not stone..."

Also `src/script_generator.py:72`:

```python
seg_hint = "3 to 5 short segments" if secs <= 30 else "5 to 7 short segments"
```

A bare count gives the model no structure to fill. At 45s this yields 5–7
segments, which is the right budget — but the segments need names, not a
number.

### Change

Replace `seg_hint` with an explicit beat scaffold, and add the pivot rule:

```
- Segment 3 is the PIVOT: open it with an explicit turn phrase — "But here's
  the twist.", "What most people don't know...", "But because..." — then
  deliver the reframe. This beat is what holds viewers through the middle.
- Segments 4-6 are the FACT STACK: 3 to 5 discrete facts, each one able to
  stand alone and each individually surprising. Do NOT build one continuous
  argument; build a chain of small payoffs, because each fact buys the next
  few seconds of attention.
- Put the strangest, most specific fact LAST in the stack. ("The only wood
  aboard was the butcher's chopping block and the grand piano.")
```

---

## 4. Beat 4 — callback closer

### Current state

`config.yaml:13` (persona):

```
...and end with a memorable takeaway.
```

This instruction produces the platitude that killed the 40% video: "One
election, one seat, a permanently changed map of who gets to govern." It calls
back to nothing and closes no loop.

The loop is mechanical, not tonal: the final line must reach back to the
opening line and change what it meant, which is what physically sends a viewer
back to the top. That rescan is the 2.24x opening watch ratio.

### Change

**config.yaml:13** — replace "end with a memorable takeaway" with:

```
...and close by calling back to your opening line so it means something new.
```

**Prompt** — add:

```
- The FINAL segment is the CALLBACK: ~20 words. It must reuse a distinctive
  noun or phrase from segment 1 and change what that phrase means.
    "the alphabet you're reading right now" -> "every time you write a single
    letter, you're carrying forward a gift from a civilization history tried
    its best to forget."
  A generic uplifting summary is a FAILURE. If the closer would work on any
  other video in the niche, rewrite it.
```

---

## 5. Make the structure machine-checkable

Prose instructions drift. Tag the beats in the JSON so they can be validated.

### 5a. Schema — `src/script_generator.py:140-149`

Add a `beat` field per segment:

```json
{"beat": "hook|setup|pivot|fact|callback", "narration": "...", "keywords": ["..."]}
```

### 5b. `Segment` dataclass — `src/script_generator.py:24-27`

```python
@dataclass
class Segment:
    narration: str
    keywords: list[str] = field(default_factory=list)
    beat: str = ""
```

Defaulting to `""` keeps old persisted stages loadable.

### 5c. Parse validation — `_parse_script_response`, line 330

Reject unknown beat values alongside the existing narration/keyword checks.

### 5d. New `_validate_structure(segments) -> str | None`

Returns an error string or `None`. Checks:

1. exactly one `hook`, and it is first
2. exactly one `callback`, and it is last
3. exactly one `pivot`
4. pivot index falls in the middle third of the segment list
5. between 3 and 5 `fact` segments
6. hook is 15–22 words
7. callback shares at least one content word (≥5 chars, non-stopword) with the
   hook — the cheap mechanical proxy for the loop test

Check 7 is deliberately loose. It catches the "generic uplift closer" failure
without trying to judge meaning.

### 5e. Wire into the existing retry loop — `generate_script`, lines 409-434

The loop already retries on word count. Add structure to the same loop so a
structural failure costs one LLM call rather than a full render:

```python
structure_error = _validate_structure(segments)
if fmt == "short" and structure_error:
    continue   # feed structure_error back into attempt_prompt, as word_count is
```

Mirror the existing word-count feedback pattern — append the specific failure
to the retry prompt so the model corrects rather than rerolls blind.

### 5f. Quality gate — `src/quality_gate.py:17`

Add `"structure"` to `CHECK_NAMES` and validate beat tags on `st["script"]`.
The gate is the last line of defence before upload; a structurally broken
script should never render-then-fail, but should never upload either.

Note `CHECK_NAMES` drives the score denominator, so adding an entry shifts
historical scores by one slot. Acceptable — the gate is pass/fail, score is
informational (`quality_gate.py:180`).

---

## 6. Fix the feedback loop — it is reading the wrong column

`src/topics.py:142-166`, `performance_examples()`:

```python
"""Past titles split by like-rate (likes/views): (winners, losers).

Like-rate is our retention proxy — Studio retention data isn't in the API
export. Videos below min_views are ignored: 1 like on 12 views is noise.
"""
```

**That comment is out of date.** `scripts/export_analytics.py:190` already
requests `averageViewPercentage` and writes it to `analytics.json` as
`avg_view_pct` (line ~203). The real retention number is sitting in the file;
the selector is using a proxy for data it already has.

This matters because the two metrics disagree. `They Invented Writing` is the
channel's best video by retention at 151% — it is not the top video by like
rate, so the current selector may not be feeding it back as a winner at all.

### Change

Rank on `avg_view_pct` when present, fall back to like-rate when missing (old
rows, or videos too new for the analytics lag):

```python
def performance_examples(min_views=50, k=4):
    # winners: avg_view_pct >= 100 (looping)
    # losers:  avg_view_pct <= 55  (structure broke)
    # fallback to likes/views only when avg_view_pct is absent
```

Suggested thresholds from the current distribution: winners ≥100%, losers ≤55%.
That currently yields 3 winners and a clear loser set, which is the right size
for the `k=4` prompt block.

Also update the docstring — it is now actively misleading about what data is
available.

---

## 7. Cadence (config, not code)

81 uploads in 21 days spread the algorithm's test audience thin and gave it no
stable channel profile. One upload/day. Check `scripts/run_daily.sh` and
`scripts/com.histold.daily.plist` for how many slots fire per day and reduce to
one. This is independent of the beat work but compounds with it.

---

## Rollout order

1. **`shorts_target_seconds: 45`** — one line, recovers most of the loss, ship
   alone and verify against the next 3–5 uploads before touching prompts.
2. Prompt rewrite (beats 1–4) + persona line in `config.yaml`.
3. Beat tagging + `_validate_structure` + retry wiring.
4. Quality gate `structure` check.
5. `performance_examples()` retention fix.
6. Cadence reduction.

Steps 1 and 2 are the ones that move retention. Steps 3–4 stop it regressing
silently — which is exactly what happened on 2026-07-10, when a config change
in a commit about frontend design cut watch time per view by 6x with nothing
in the pipeline to catch it.

## Tests to add

- `tests/test_script_length.py` — extend for the 45s budget.
- New `tests/test_script_structure.py` — `_validate_structure` unit cases:
  missing pivot, pivot at wrong index, 2-fact stack, 6-fact stack, hook out of
  word range, callback sharing no word with hook, and one fully valid script.
- `tests/test_quality_gate.py` — add a structurally-invalid script fixture and
  assert `passed is False`.

## Verification

After 5 uploads on the new structure, pull `averageViewPercentage` and
`averageViewDuration` per video. Targets: avg view % above 100, avg duration
above 45s. If avg view % lands 60–90%, the pivot is landing but the callback
is not closing the loop — check beat 4 before touching anything else.
