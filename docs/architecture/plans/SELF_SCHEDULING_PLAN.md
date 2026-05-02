# Self-Scheduling Migration Plan

## Goal

Replace algorithmic assignment generation with collector self-scheduling in the website while keeping dashboard and map behavior stable.

## Key Decisions

- Weather is advisory only.
- Scheduling remains backpack-specific.
- Slot uniqueness is enforced per `backpack + route + date + tod`.
- Transition will preserve compatibility with existing `schedule_output.json` consumers.

## Non-Goals (V1)

- No algorithmic auto-assignment.
- No weather-based claim blocking.
- No collector-facing Slack bot workflow in phase 1.

## Phases

1. Define and freeze schema + API contracts.
2. Introduce schedule storage module with validation and atomic writes.
3. Add read and claim/unclaim schedule APIs.
4. Add admin edit/delete APIs.
5. Add self-scheduling UI in dashboard.
6. Shift rebuild paths to weather + site rebuild only (no scheduler run).
7. Add notifications (preview, manual send, then daily automation).
8. Deprecate scheduler as default runtime path after stabilization.

## Required Guardrails

- Validation on every write.
- Conflict checks:
  - Unique assignment per `backpack + route + date + tod`.
  - No collector double-booking in same `date + tod`.
- Audit logging for claim/unclaim/edit/delete.
- Keep legacy scheduler runnable as temporary fallback endpoint during cutover.

## Notification Roadmap

- Phase 1: Notification preview endpoint for next-day assignments.
- Phase 2: Manual notification send endpoint.
- Phase 3: Automated day-before send (fixed local time) with idempotency protection.

## Risks and Mitigations

- Risk: Breaking existing dashboards/maps.
  - Mitigation: Keep `schedule_output.json` top-level and assignment field compatibility.
- Risk: Race conditions during simultaneous claims.
  - Mitigation: Single-writer lock + atomic write strategy.
- Risk: Operational confusion during transition.
  - Mitigation: Clear docs, legacy fallback endpoint, and staged PR rollout.
