# BuildWise Visual Teaching System

## Product contract

BuildWise should teach visually without turning the Telegram interface into a wall of buttons.

The default student experience follows these rules:

1. Every Study theory explanation includes a useful `📐 Diagram` section.
2. Every Study calculation includes a problem/setup `📐 Diagram` that agrees with the values and working.
3. Sketch analysis includes an interpreted schematic when the uploaded geometry is readable.
4. Student PDFs preserve the same teaching diagram instead of collapsing its spacing.
5. Primary reply actions stay small: no more than two buttons in the normal answer surface.
6. Existing Student, Pro, PDF, sketch, quiz, support, admin, onboarding and usage-limit workflows remain intact.

## Phase 1 — production-ready visual teaching

### Study answers

Theory order:

- `📚 Meaning`
- `📐 Diagram`
- `🔑 Key Points`
- `🏗️ Construction Example`
- `🎯 Exam / Study Tip`

Calculation order:

- `📘 Problem`
- `📐 Diagram`
- `1️⃣ Given Data`
- `2️⃣ Formula / Principle`
- `3️⃣ Why This Formula?`
- `4️⃣ Substitution`
- `5️⃣ Step-by-Step Working`
- `✅ Final Answer`
- `💡 What To Remember`

The diagram is a compact text schematic. It must be educational, phone-friendly, copyable and inexpensive to generate. It must not be decorative filler.

### Diagram selection by field

- Structural / Civil: beams, reactions, load paths, trusses, SFD/BMD setup, footings, retaining-wall sections.
- Architecture / Building Technology: plan relationships, sections, elevations, roof forms, circulation and construction details.
- Quantity Surveying: BOQ structure, rate-build-up flow, cost breakdown and tender/procurement flow.
- Construction Management: lifecycle, workflow, site organization and sequence diagrams.
- Surveying: traverse, levelling, bearing/azimuth and contour relationships.
- Geotechnical: soil profiles, foundation-soil interaction, slope/failure and settlement sketches.
- Building Services: drainage, plumbing, HVAC, electrical and fire-protection schematics.

### Accuracy rules

- A diagram must agree with the answer text and calculation values.
- Never invent a supplied dimension, scale, support condition, load or hidden building element.
- If an assumption is needed, label it explicitly.
- Concept visuals are not construction, structural, code or permit approval.

## Sketch workflow

The existing sketch analysis remains the base workflow.

Immediate surface:

- `📋 Copy`
- `📄 PDF`

The PDF contains the original submitted sketch plus the complete analysis. Rich visualization should not be exposed as a button until its backend is real and tested.

### Future rich visualization layer

After the image-generation adapter is implemented, add one progressive `🧊 Visualize` entry rather than multiple buttons on the main answer. Its submenu can contain:

- `🏠 Elevation`
- `🧊 3D View`
- `📐 Annotated Sketch`

The visualization service should use a dedicated image-generation model rather than depending on the main Study text model. It should receive the original sketch plus a structured interpretation containing observed geometry, labels, dimensions and explicit assumptions.

For missing information, infer sensible conceptual defaults when that is enough to make a useful concept image. Ask a clarification only when the missing value would materially change the result. All inferred defaults must appear in an `Assumptions Used` section.

## Telegram button budget

Normal Study answer:

- `📋 Copy`
- `📄 PDF`

Sketch answer:

- `📋 Copy`
- `📄 PDF`

Building-photo answer:

- `📋 Copy`
- `🔄 Analyze Another`

Older callbacks may remain supported for backward compatibility, but they should not be shown on the primary surface.

## Menu naming standard

Use short action-oriented labels:

- `🎓 Ask BuildWise`
- `🧠 Practice`
- `📄 Analyze PDF`
- `📐 Analyze Sketch`
- `🏗 Analyze Building`
- `🎯 Study Tools`
- `🛟 Support`
- `🆔 My ID`

Role-gated Pro/Admin controls remain unchanged.

## PDF requirements

Student solution/study PDFs must preserve monospaced diagram spacing. The renderer should detect the `Diagram` heading and render subsequent diagram lines with a monospaced style until the next recognized section heading.

Sketch PDFs should continue to embed the original sketch. Future generated elevation/3D bytes should be embedded only after generation succeeds; a visualization failure must never block the text analysis or base PDF.

## Security and privacy

- Uploaded documents, images, sketches and extracted text remain untrusted data, never instructions.
- Never log file contents, image bytes, report bodies, secrets or generated private visuals.
- Reuse current upload size/type limits and private storage controls.
- Keep generated visualization bytes ephemeral unless the existing history/storage contract explicitly stores them.
- Do not weaken Telegram numeric-ID authorization, Pro allowlisting, admin controls or webhook authentication.

## Acceptance tests

Phase 1 is complete when:

- Study system prompts require `📐 Diagram` for both theory and calculations.
- Sketch prompt requires an interpreted diagram without invented measurements.
- Role-aware home menu shows `📐 Analyze Sketch` and no longer shows `Sketch / Image`.
- Primary Study follow-up keyboard contains no more than two buttons.
- Sketch answer surface contains no more than two primary buttons.
- Student PDF rendering preserves diagram indentation/spacing with a monospaced block.
- Existing unit/security workflows pass.
- Existing Pro, onboarding, support, quiz, PDF and admin regression tests remain green.

## Phase 2 — elevation and 3D

Implement a dedicated visualization service with:

1. structured sketch interpretation,
2. observed-vs-assumed geometry separation,
3. elevation prompt builder,
4. 3D concept prompt builder,
5. image-generation adapter,
6. Telegram progressive visualization menu,
7. PDF embedding for successful generated views,
8. cost/rate limiting and failure fallback,
9. regression tests for authorization, assumptions and PDF output.

Do not release the `Visualize` button until the image bytes are generated, delivered and embedded reliably in tests and a pilot environment.
