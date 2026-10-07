"""Original topic taxonomy. Reference links are reading aids, not copied courses."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    objectives: str
    source: str


TOPICS = (
    Topic('measurement', 'Measurement and units', 'Convert units; distinguish length, area and volume; check dimensional consistency.', 'https://www.nist.gov/pml/special-publication-811'),
    Topic('materials', 'Construction materials', 'Compare material properties, selection criteria, storage and quality checks.', 'https://www.nist.gov/buildings-and-construction'),
    Topic('technology', 'Construction technology', 'Explain construction sequence, interfaces and inspection hold points.', 'https://www.osha.gov/publications/bytopic/construction'),
    Topic('services', 'Building services', 'Trace water, drainage, ventilation and electrical service routes conceptually.', 'https://www.nist.gov/buildings-and-construction'),
    Topic('structures', 'Structural principles', 'Trace loads and supports; distinguish force, stress and deformation; state assumptions.', 'https://www.nist.gov/buildings-and-construction'),
    Topic('estimating', 'Estimating fundamentals', 'Calculate quantities from supplied dimensions; state units, waste assumptions and exclusions.', 'https://www.nist.gov/pml/special-publication-811'),
    Topic('maintenance', 'Building maintenance', 'Separate observations from possible causes; plan inspection and maintenance records.', 'https://www.nist.gov/buildings-and-construction'),
    Topic('site', 'Site practice', 'Identify hazards, document observations and escalate uncertain work to a supervisor.', 'https://www.osha.gov/publications/bytopic/construction'),
    Topic('drawing', 'Drawing interpretation', 'Read supplied labels, dimensions and scales; flag missing or unreadable information.', 'https://www.nist.gov/pml/special-publication-811'),
)
TOPIC_MAP = {topic.key: topic for topic in TOPICS}

# Newly authored arithmetic checks; no copied past papers or licensed textbooks.
FOUNDATION_CHECKS = (
    ('measurement', 'Convert 2500 mm to metres.', '2.5 m'),
    ('estimating', 'Find the volume of a 4 m by 3 m slab, 0.15 m thick, excluding waste.', '1.8 m³'),
    ('drawing', 'A dimension label is illegible. Can its exact value be certified from appearance?', 'No. Request a legible dimension or verified scale.'),
)
