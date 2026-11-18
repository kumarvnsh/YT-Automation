# Bot Office setup and operating guide

Open https://vnsh.tail9bdcf7.ts.net/bots?channel=shotsnip or select Histold in the header.

## Set up each bot

1. **Choose the channel.** Notes, assignments and routines are separate for each channel, including Jarvis and Operations.
2. **Select a desk.** Its saved routine assignment and standing notes load into the Control desk. Running, queued, report ready, needs attention and idle states come from real task records.
3. **Choose a job.** Press **Use suggested routine** to fill a focused assignment and recommended cadence. This does not save or activate anything. Edit the question if needed.
4. **Save context.** Enter preferences, constraints and previous experiment outcomes in Standing notes, then press **Save notes**.
5. **Enable the routine.** Select the engine and interval, then press **Save routine**. The first scheduled review runs after that interval. One unfinished scheduled assignment per bot prevents a growing backlog. Changing an interval resets its next review time.
6. **Test immediately if wanted.** Press **Assign task now** to queue a report without changing the saved routine. Select the bot's desk to see its reports, status, errors and activity. Stop or retry individual assignments using their controls.

You can close the browser page. Automatic reviews require the laptop, dashboard service and an available provider to remain running. Tasks run one at a time to leave resources for capture and rendering. A next-review countdown is a scheduling estimate, not a guarantee of an immediate start.

## Suggested jobs

| Bot | Starting cadence | Purpose |
| --- | --- | --- |
| Operations | Every hour | Review current failures, publishing progress and stale data; recommend recovery checks. |
| Shotsnip producer | Every 6 hours | Compare streamer results at similar publication ages; propose a targeting or hook experiment. |
| Histold analyst | Every 6 hours | Find supported topic/format opportunities and propose one measurable content test. |
| Jarvis | Daily | Prioritize three actions, check previous findings and identify decisions for the owner. |

These are suggested defaults, not automatic changes to existing routines. Previously saved intervals are preserved.

## What happens to a report

The completed report stays in assignment history. Standing notes and the last two completed reports from that bot and channel are included in the next assignment. Review a recommendation, implement a selected change through the existing controls or a development task, then save the change and measured result in the bot's notes.

Bots currently produce analysis and recommendations. They do not execute repairs, change targets, edit code, send messages or publish. The hourly clipping/publishing scheduler is independent. Routine setup does not turn on autonomous implementation.

## Office behavior

- Desks show real current/most recent task states, with animation only for running assignments.
- Selecting a desk filters reports to that bot. Use All channel bots for the whole team's history.
- The activity feed and task counts cover the latest 50 channel assignments. Reports available is not a count of today's reports.
- Updates poll every five seconds while the page is visible; background tabs pause polling. Draft assignments and notes survive refreshes.
- On a connection failure the office shows that updates stopped and pauses working animations. It does not infer completion from a countdown.
- Mobile uses compact desk cards with the same status and controls. Reduced-motion preferences disable animation.

## Provider fallback deployment

The provider policy is Codex, then Claude, then local Qwen (`qwen3:8b`). A failed Superbot Codex assignment falls back to the dashboard Claude/local analyst; an offline runtime uses the dashboard's full provider chain. Login, quota and model availability can still prevent a report. Provider details appear in newly generated reports once the updated backend is loaded.

The page reads backend capability information. If it says the fallback update awaits a service restart, the office redesign is live but the running Python process has not loaded the new fallback implementation yet. Existing routines remain in place. Use the managed dashboard launcher in the appropriate Windows task/account to reload the service; do not terminate capture or publishing workers to refresh this page.

## Future action layer

A separate implementation is needed before bots can act on their findings. Start with explicit allowed actions, bounded retries, logged before/after state and a measurable outcome; keep ambiguous changes for owner review. The office should show each action and result separately from an analyst recommendation. No such action layer is implied by this redesign.
