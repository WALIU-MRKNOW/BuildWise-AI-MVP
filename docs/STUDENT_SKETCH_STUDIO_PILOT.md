# BuildWise Student Sketch Studio — isolated pilot

This is a **new text-to-original-sketch** feature. It does not change the existing
`/sketch` upload-analysis or elevation workflow, and it does not activate
BuildWise Pro, billing, a general Premium plan, or the proposed 50-request limit.

## Pilot controls (off by default)

Set these only for a **small, explicitly approved Telegram pilot**:

- `STUDENT_SKETCH_ENABLED=true` (defaults to `false`)
- `STUDENT_SKETCH_ALLOWED_IDS=<comma-separated Telegram user IDs>` (mandatory; empty = nobody)
- `STUDENT_SKETCH_DAILY_LIMIT=2` (default 2, maximum 5)

Approved students can use
`/draw A labelled educational section through a reinforced concrete raft foundation`.
The command sends a newly generated PNG as a Telegram document and labels it as
an educational concept, not a dimensionally verified construction drawing or
certified survey plan. Requests also use one existing daily AI request.

**Cost and quota caution:** The extra per-user drawing allowance is an in-memory,
UTC-day pilot guard; process restarts reset its counters. It is **not a durable
billing or subscription quota**. Keep the pilot off unless the ID allowlist is
small and actual image-generation costs are being monitored. Before any wider
launch, implement a persistent atomic drawing allowance and clear pricing.

The generator reuses the configured `OPENAI_ELEVATION_MODEL`,
`OPENAI_ELEVATION_SIZE`, and `OPENAI_ELEVATION_QUALITY` values currently used
for existing elevation images; confirm the configured model supports text-to-image
generation before enabling. Do not enable without first checking supported model
parameters and cost.

## Scope and accuracy

The first iteration supports educational conceptual sketches described in text:
building elements, architectural arrangements, structural details, services
schematics, and illustrative surveying diagrams. It does **not** provide an
accurate dimensioned CAD engine. Do not infer missing coordinates, bearings,
survey beacons, structural capacities, official signatures or approvals. Survey
plans and construction-ready drawings will need separate geometry/validation
tools and professional review.

The command is omitted from general student menus until the pilot is proven.
