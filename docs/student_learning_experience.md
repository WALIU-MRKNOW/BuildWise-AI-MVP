# BuildWise student learning experience

Study & Solve now distinguishes calculation-style requests from topic/theory requests without an extra AI classification call.

Calculation flow:

- Telegram processing state: `🧮 Solving calculations...`
- Problem restatement
- Given data with units
- Formula / principle
- Plain-language reason for the formula
- Substitution
- Step-by-step working
- Final answers with units
- Short learning takeaway
- Optional worked-solution PDF

Topic flow:

- Telegram processing state: `📚 Explaining topic...`
- Meaning
- Key points
- Built-environment example where useful
- Exam / study tip

Telegram output sanitizes common raw LaTeX commands so students see readable phone-friendly mathematics.

The PDF action renders the latest student Study & Solve answer in memory and sends it through Telegram. It does not persist a new PDF or add a database migration.
