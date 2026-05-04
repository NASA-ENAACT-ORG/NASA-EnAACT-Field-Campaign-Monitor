# Context History

This file preserves the reasoning trail behind the current repo direction.

It is not the best starting point for day-to-day work. Start with:

- `docs/operations/context/CURRENT_STATE.md`

## Why This File Exists

The repo accumulated several temporary chat-resumption and planning files during
cleanup and self-scheduling work. Those notes were useful, but they overlapped
heavily and eventually drifted from one another.

This file keeps the important historical transitions in one place.

## Timeline Summary

### Phase 1: Scheduler-centered understanding

The repo was initially framed as a scheduling + dashboard system for a NASA NYC
field campaign. The smallest perceived production path included:

- `serve.py`
- `build_weather.py`
- `walk_scheduler.py`
- `build_dashboard.py`
- `build_collector_map.py`

During this phase, the biggest complexity hotspots were identified as:

- `pipelines/dashboard/build_dashboard.py`
- `pipelines/scheduling/walk_scheduler.py`

### Phase 2: Cleanup audit and redundancy review

Two architecture docs captured the first serious cleanup pass:

- `docs/operations/history/architecture/CLEANUP_AUDIT.md`
- `docs/operations/history/architecture/REDUNDANCY_VERDICTS.md`

Key findings from that pass:

- forecast monitoring looked duplicated between Apps Script and Python
- Claude schedule parsing looked like a dormant fallback
- availability heatmap logic was awkwardly shared across two builders
- collector/route metadata was duplicated across several scripts
- `Coordinate Availability.xlsx` appeared unused

### Phase 3: Strategic pivot away from algorithmic scheduling

The major correction was that the project was no longer centered on improving
the scheduling algorithm.

Instead, the direction became:

- simplify operations
- reduce moving parts
- support direct calendar-integrated slot workflows
- bias toward a single source of truth for shared metadata

This was the turning point captured in the now-superseded rectified chat notes.

### Phase 4: Self-scheduling rollout

The self-scheduling migration plan formalized the shift to:

- slot claim/unclaim APIs
- validated schedule storage
- scheduler-free rebuild paths
- compatibility with existing `schedule_output.json` consumers

Important decisions recorded during that phase:

- weather is advisory only
- uniqueness is per `backpack + date + tod`
- no collector double-booking within the same `date + tod`

### Phase 5: Registry cleanup and retirement pass

The next major cleanup step was completed by:

- creating `shared/registry.py`
- moving active scripts to shared metadata imports
- moving scheduler/map/transit scripts under `pipelines/_retired/`
- adding `scripts/ops/self_schedule_smoke.py`

This marks the current architecture boundary between active runtime code and
historical scheduler-era code.

### Phase 6: Backpack status coordination polish

After self-scheduling landed on `main`, the dashboard gained backpack
holder/location controls in the calendar nav:

- one status dropdown per backpack
- saved manual status in `schedule_output.json` under `backpack_status`
- default holder inferred from the latest completed walk when no manual status
  exists
- professor/staff accounts kept at the bottom of relevant dropdowns
- a more prominent "Current holder/location" control-group treatment so the
  status box is easier to find during calendar use

## Superseded Temporary Source Files

These files have now been condensed into the current context docs plus this
history file:

- `docs/operations/terra_temp_text/CHAT_CONTEXT.md`
- `docs/operations/terra_temp_text/CHAT_CONTEXT_RECTIFIED.md`
- `docs/operations/terra_temp_text/NEXT_CHAT_CONTEXT.md`
- `docs/operations/terra_temp_text/SELF_SCHEDULE_TMP_NOTES.md`

## Notes On Older Detailed Docs

The following files are still worth keeping as detailed reference material, but
they should be read as historical analysis rather than the shortest source of
current truth:

- `docs/operations/history/architecture/CLEANUP_AUDIT.md`
- `docs/operations/history/architecture/REDUNDANCY_VERDICTS.md`
- `docs/architecture/plans/SELF_SCHEDULING_PLAN.md`
- `docs/operations/history/architecture/Repo_Reorg_plan_codex.md`
