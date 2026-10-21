# Facebook four-week growth measurement

The `facebook-world-history-4w` report compares Facebook Reels by `editorial_region`, `editorial_series`, and `duration_variant` at 24, 72 and 168 hours. These are descriptive observations, not causal winners: region, series, posting time and duration are confounded and samples may be small. YouTube and Instagram are excluded.

The existing Meta exporter does not collect per-Reel shares, followers, watch duration, three-second retention or completion. They remain null. No new Meta Insights API fields are asserted or added by this workflow. Manual input is the supported fallback when the Facebook professional dashboard actually exposes a measure for that Reel. Do not substitute Page-wide follows, reach, impressions or another view definition for per-Reel views.

## Manual observation CSV

Normalize dashboard data into this exact UTF-8 CSV template. Optional measure columns may be blank; blanks are unknown, while `0` means a reported zero. All measures on a row must use the same Reel and period. Counts must be nonnegative integers, watch duration is seconds, retention and completion are fractions between 0 and 1 (e.g. dashboard `70%` becomes `0.70`). Do not derive completion from average watch duration. Only enter three-second retention or completion when the dashboard explicitly reports that definition.

```csv
media_id,observed_at,period_start,period_end,views,shares,followers_gained,average_watch_time_seconds,three_second_retention,completion_rate,view_definition
123456789,2026-10-23T10:00:00+05:30,2026-10-22T10:00:00+05:30,2026-10-23T10:00:00+05:30,1000,12,3,22.5,0.70,0.30,Dashboard Reel views including replays
```

Replace the example ID and timestamps with actual data. Every timestamp must include its timezone. `observed_at` means when these counts were observed, not when the file was imported. `period_start` and `period_end` mean the reporting window selected in the dashboard. Use the exact publication timestamp from `data/published_index.json` as the start **only when the dashboard really reports cumulative lifetime counts**; the end must equal the observation time to qualify for an age snapshot. Period-only dashboard totals are saved in the private ledger but excluded from equal-age comparisons. Rounded dashboard dates are insufficient to assert an exact lifetime observation.

```sh
python scripts/import_facebook_insights.py /absolute/path/observations.csv --root .
python scripts/report_facebook_growth.py --root .
```

These commands process local files; they make no API calls and publish nothing.

The importer saves its full observations in ignored `data/private/facebook-insights.json`. Qualified snapshots and the growth report go to `data/experiments/facebook-world-history-4w-snapshots.json` and `facebook-world-history-4w-report.json`; these files are intended for version control and contain performance metrics. Do not import confidential measures into public tracked reports. The original CSV hash is retained in the private ledger. Each Reel/observation is immutable; conflicting reimports fail. Snapshot keys are immutable within their source and view definition. A manual dashboard observation can coexist with an existing API observation at the same age; it does not overwrite the API record. The original observation timestamp still determines its age and lateness, even when imported later.

The optional `view_definition` column must describe the exact dashboard view measure, including replay/threshold semantics where stated. Reuse the same text only when definitions match. API and manual observations, and different view definitions, always form separate comparison groups. When the definition is absent, the observation remains visible per Reel and derived share/follow rates stay null; it is not pooled with other Reels. Manual `followers_gained` must be gross follows attributed to that Reel. Native signed `Net follows` is never mapped into it.

Only the latest due age is captured. Older missed windows are marked missed, never reconstructed using later counts. A snapshot more than the manifest tolerance (one hour) late stays visible but is excluded from comparisons. Each rate uses only Reels with both its numerator and positive compatible views: total shares or follows / those Reels' views × 1000. Separate sample sizes accompany each rate. Unknown metadata stays null. Inspect observation watch duration and retention directly; the report does not invent aggregate retention denominators.

The hourly analytics workflow builds the Facebook report after the existing exports and commits the experiment directory. A failed Meta export retains its previous observation timestamp; this does not create fresh observations from stale counts. Existing API view collection is unchanged and should be checked against the account's available Insights definitions before comparing it to a dashboard view definition.

## Earnings stay in their own period and currency

Import actual dashboard earnings with the existing importer; no RPM or payout is fabricated:

```sh
python scripts/import_facebook_monetization.py /absolute/path/earnings.csv --root .
python scripts/report_facebook_growth.py --root . --earnings data/private/facebook-monetization.json --output data/private/facebook-growth-with-earnings.json
```

The earnings CSV uses `media_id,period_start,period_end,currency,qualified_views,stars_count,content_earnings,stars_earnings` (dates are `YYYY-MM-DD`). Rows retain matching Reel, reporting period and currency. Content earnings per 1000 qualified views uses only that row's same-period qualified views, never age-snapshot views. Currencies, Stars and Content Monetization amounts are not pooled. The normal committed growth report never reads private earnings. Imported amounts may be estimated or revised, and are not payout statements.

## Native Meta content publish-time summary

For an unmodified native Meta export with `Post ID`, `Post type`, `Date`, `Publish time` and `Views` headers:

```sh
python scripts/import_facebook_insights.py /absolute/path/native-export.csv --native-export --root .
```

This branch supports `Date=Lifetime` rows and writes only ignored `data/private/facebook-baseline/{source.csv,records.json,report.json,report.md}`. It does not create experiment snapshots or alter published metrics. The filename's selected publication dates do not establish a metrics reporting window; the native `Date` field governs metric scope. Unzoned publish timestamps stay local with unknown timezone. Capture time stays unknown unless supplied explicitly with `--observed-at` using an actual known observation timestamp. Source CSV, full captions in the raw file, performance figures and financial amounts remain private. Running another native import replaces this baseline; preserve prior private directories yourself if needed.

`Post ID` and the ID in the Reel permalink are stored separately; neither is silently assumed to match a publishing API ID. Native `Net follows` remains signed net follows, never gross `followers_gained`. Per-Reel `Average Seconds viewed` and `Seconds viewed` are preserved as reported, without deriving retention/completion or a view-weighted watch average. Approximate earnings, Content Monetisation and Stars are separate estimated fields; no period RPM is calculated from Lifetime views. Non-Lifetime native shapes are rejected for explicit normalization rather than guessing their period.
