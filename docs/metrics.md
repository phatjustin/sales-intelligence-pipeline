# Metric contracts (USD, UTC)

All monetary values are decimal **USD dollars**, stored as `numeric(14,2)` per opportunity and
`numeric(18,2)` for aggregate value. They are not cents. API money is serialized as a decimal string.
All opportunity IDs and account IDs are UUIDs; timestamps are timezone-aware and normalized to UTC.

| Table / metric | Definition | Limitations |
| --- | --- | --- |
| `reporting.open_pipeline` | One row for each open stage: `prospecting`, `qualification`, `proposal`, `negotiation`. Count of current opportunities and sum of current `value`; empty stages are zero. | No probability weighting, currency conversion, expected-close cohort, or historical inference. Won/lost excluded. |
| `reporting.closed_performance.win_rate` | Current won count / (current won + lost count). NULL if no closed deals. | All-time current closed population, not a booking-period or cohort metric. Reopened deals leave this population. |
| `average_sales_cycle_days` | Mean `(closed_at - created_at)` / 86,400 seconds across current won AND lost deals. NULL if none. | Calendar elapsed days, not business days. Requires an explicit valid `closed_at`; never substitutes `updated_at`. Reopen/reclose episodes are not separate deals. |
| `reporting.account_activity.activity_count` | Count of ingested activities linked through opportunities to an account, across all kinds. Zero for accounts without activity. | Counts source events, not unique people or engagement quality. |
| `contact_count`, `last_contact_at` | Count and maximum `occurred_at` for email and meeting activities only. | `contact_count` counts interactions, not CRM contact entities. CRM audit events do not establish contact. NULL last-contact means unknown/no qualifying events. |
| `last_activity_at` | Maximum activity `occurred_at` including CRM events. | An old event delivered now changes ingestion state but does not imply contact now. |
| `reporting.stage_history` | Exactly one row per supplied explicit stage event. Entered timestamp is `changed_at`; exited timestamp is the next explicit event timestamp for that opportunity. | No events are invented from current state or creation time. First known event need not be first lifetime stage. Same-time distinct events are quarantined as ambiguous. |
| `stage_history.duration_days` | For a consistent adjacent transition, elapsed days to the next event. For the latest known open stage matching current state, elapsed days to `observed_through` (publication time). | NULL for broken adjacent chains, future intervals, unknown latest-state endings, or final terminal stages. `chain_valid` checks supplied adjacent `from_stage` links; it does not prove complete source history. `is_current` means latest known event matches a current open stage. |
| `reporting.weekly_snapshots` | The first successful non-backfill observation in each Monday-based UTC week. Four rows capture open-stage counts and values, with actual `observed_at` and originating run ID. | Not a Monday-midnight balance. No prior snapshots are fabricated, no missing weeks are filled, and reruns/corrections do not restate an existing observation. Snapshots begin when this pipeline actually runs. |
| `reporting.pipeline_health` | Latest run status/counts/duration/checkpoint; last complete publication timestamp, latest accepted source-update timestamp, unresolved rejects, last successful run. | Publication freshness is pipeline activity; source-event freshness is source data age. A recent successful backfill does not prove the arrival backlog is consumed; check its mode and checkpoint. |

Reporting consumers should read `reporting.publication` along with reports to identify the
originating complete run. Backfills refresh the global current reporting state from the latest
accepted staging versions; they are repair runs, not historical as-of reporting. During a partial
or failed attempt, reporting still points to the last complete run. A successful run after explicit
quarantine acknowledgement means complete processing with those documented exclusions.

## Known synthetic fixture

Four opportunities start at values $1,000, $2,000, $3,000, $4,000. The first two are open;
the last two are won/lost after 10/20 elapsed days. Expected results: $3,000 open pipeline,
one win and one loss, win rate 0.5, average closed cycle 15 days. Explicit prospecting intervals
for the closed deals are 10 and 20 days. Account 1 has an email and CRM event; only the email
defines its last-contact date. Revision 1 changes the first opportunity to negotiation at $1,500,
redelivers that version, delivers an older meeting, and supplies a transition event. Current open
pipeline becomes $3,500; the original weekly observation remains $3,000. These are fixture
expectations validated locally, not observed customer outcomes.
