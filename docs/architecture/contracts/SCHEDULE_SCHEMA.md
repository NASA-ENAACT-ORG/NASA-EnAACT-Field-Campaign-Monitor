# Schedule Schema (Self-Scheduling Compatible)

This document defines the canonical JSON contract for `data/outputs/site/schedule_output.json`.

## Compatibility Rule

Current dashboard and map scripts must continue to read this file without changes.

## Top-Level Object

```json
{
  "generated": "2026-05-01",
  "generated_at": "2026-05-01T17:30:00-04:00",
  "week_start": "2026-05-01",
  "week_end": "2026-05-07",
  "weather_history_start": "2026-04-24",
  "weather_week_start": "2026-05-01",
  "weather_week_end": "2026-05-07",
  "weather": {
    "2026-05-01_AM": true,
    "2026-05-01_MD": false,
    "2026-05-01_PM": true
  },
  "bad_weather_slots": ["2026-05-01_MD"],
  "assignments": [],
  "unassigned": []
}
```

## Assignment Object

Required fields (existing compatibility set):

- `route` (string)
- `label` (string)
- `boro` (string)
- `neigh` (string)
- `tod` (`AM|MD|PM`)
- `backpack` (`A|B`)
- `collector` (string)
- `date` (`YYYY-MM-DD`)

Optional fields (new, non-breaking):

- `id` (string, stable identifier)
- `status` (`claimed|confirmed|cancelled`)
- `claimed_at` (ISO timestamp)
- `claimed_by` (string)
- `updated_at` (ISO timestamp)
- `preserved` (boolean; legacy-compatible passthrough)
- `route_group` (string; legacy-compatible passthrough)
- `weather_advisory` (boolean)

Example:

```json
{
  "id": "A_MN_WB_2026-05-01_AM",
  "route": "MN_WB",
  "label": "West Village",
  "boro": "MN",
  "neigh": "WB",
  "tod": "AM",
  "backpack": "A",
  "collector": "AYA",
  "date": "2026-05-01",
  "status": "claimed",
  "claimed_at": "2026-05-01T17:30:00-04:00",
  "claimed_by": "AYA",
  "updated_at": "2026-05-01T17:30:00-04:00",
  "weather_advisory": false
}
```

## Unassigned Object

Fields:

- `route` (string)
- `label` (string)
- `tod` (`AM|MD|PM`)
- `backpack` (`A|B`)
- `reason` (string)

## Validation Rules

1. Assignment uniqueness key: `backpack + date + tod` (max one walk per backpack per slot).
2. Collector cannot be assigned to multiple backpacks in the same `date + tod`.
3. `tod` must be one of `AM`, `MD`, `PM`.
4. `date` must match `YYYY-MM-DD`.
5. Weather is advisory only: bad weather may coexist with claimed assignments.

## API Implications

- Claim endpoint must reject multiple assignments for the same `backpack + date + tod` slot.
- Claim endpoint must reject collector double-booking on `date + tod`.
- Weather advisory should be returned for clients, but never block claims.
