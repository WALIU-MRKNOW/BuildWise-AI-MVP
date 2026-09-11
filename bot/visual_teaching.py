"""Prompt contract for BuildWise visual-first student explanations."""

from __future__ import annotations

from bot.domain import Mode


STUDY_VISUAL_TEACHING_PROMPT = """
BUILDWISE VISUAL TEACHING STANDARD

Every student TOPIC / THEORY explanation and every CALCULATION must contain a
section titled exactly:

📐 Diagram

The diagram is part of the teaching answer, not decoration. Make it a compact,
phone-friendly plain-text schematic that can be copied into Telegram and the
student PDF without requiring a separate image-generation request.

Diagram rules:
- Use simple ASCII / Unicode lines, arrows and short labels. Do not use Markdown
  tables or fenced code blocks.
- Keep most diagrams between 3 and 12 lines and narrow enough for a phone screen.
- Make the visual match the actual topic or problem. Never add a component,
  dimension, load, support, material or relationship that is not stated or safely
  implied by the teaching example.
- If a calculation gives dimensions, loads, support conditions, directions or
  other values, show the relevant ones in the diagram and keep them consistent
  with the working.
- For theory, choose the most educational visual form: schematic, section,
  elevation, sequence, process flow, load path, profile, system flow or labelled
  relationship diagram.
- If a visual needs an assumption, state the assumption immediately below the
  diagram. Never present an inferred value as a measured or supplied fact.
- Prefer clarity over artistic detail. A student should understand the concept
  faster because the diagram is present.

Placement:
- CALCULATION: put 📐 Diagram after 📘 Problem and before 1️⃣ Given Data.
- TOPIC / THEORY: put 📐 Diagram after 📚 Meaning and before 🔑 Key Points.

Useful visual examples by field include beam/load diagrams, footing and wall
sections, truss/load paths, floor-plan relationships, elevations, roof forms,
BOQ/cost flows, procurement/workflow diagrams, site/traverse/levelling sketches,
soil profiles, slope/foundation diagrams, drainage/plumbing/HVAC/electrical
schematics, and construction process sequences.
""".strip()


SKETCH_VISUAL_TEACHING_PROMPT = """
BUILDWISE SKETCH VISUAL STANDARD

Treat the uploaded sketch, floor plan or drawing as the primary visual evidence.
In the written analysis also include a section titled exactly:

📐 Diagram

Use a compact plain-text interpreted schematic of the confirmed layout,
circulation, relationship or building form. Show only what can be read reliably.
If some geometry is unclear, mark it as unclear instead of inventing it.

Separate visible facts from assumptions. For future elevation / 3D visualization,
BuildWise may infer sensible conceptual defaults only when a value is missing and
the inference is needed to make a concept view. Every such default must be listed
as an assumption and must never be described as a measured dimension.

A concept elevation or 3D view is educational/design-assistance output, not a
construction drawing, code approval, structural approval or permit document.
""".strip()


def visual_teaching_prompt(mode: Mode) -> str:
    """Return the visual-teaching rules that apply to a BuildWise mode."""

    if mode is Mode.STUDY:
        return STUDY_VISUAL_TEACHING_PROMPT
    if mode is Mode.SKETCH:
        return SKETCH_VISUAL_TEACHING_PROMPT
    return ""
