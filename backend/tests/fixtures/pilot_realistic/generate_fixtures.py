"""Deterministic generator for the realistic pilot fixtures (item 2b).

Produces, into this directory:
- ``contract.pdf`` — a FIDIC-style conditions-of-contract, 40-80 pages, with
  numbered clauses and contract data (time for completion, LD rate + cap,
  DNP, Engineer, bonds, milestones).
- ``boq.xlsx`` — a priced bill of quantities, 300-800 lines, with sections,
  rates, quantities and a summary.

Every value is synthetic; there are no real company names and no owner data.
Regeneration is deterministic for ``SEED``, so a diff of the fixtures is
reviewable. Run: ``python generate_fixtures.py`` from this directory.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import openpyxl
from openpyxl.styles import Font

SEED = 20261109
OUT_DIR = Path(__file__).resolve().parent

# --- synthetic project identity ---------------------------------------------
PROJECT_NAME = "Greenharbour Terminal \u2014 Berth 4 Extension Works"
PROJECT_NO = "GHT-B4-2026"
EMPLOYER = "the Employer"
CONTRACTOR = "the Contractor"
ENGINEER = "the Engineer"
SITE = "Greenharbour, Plot B4"

# Contract data (all synthetic).
TIME_FOR_COMPLETION_DAYS = 730
LD_RATE_PCT_PER_DAY = 0.05
LD_CAP_PCT = 10.0
DNP_DAYS = 365
PERFORMANCE_SECURITY_PCT = 10.0
ADVANCE_PAYMENT_PCT = 10.0
RETENTION_PCT = 5.0
RETENTION_LIMIT_PCT = 2.5
CONTRACT_SUM = 48_750_000.00

MILESTONES = [
    ("M1", "Site mobilisation and setting out", 0),
    ("M2", "Substructure \u2014 piling and foundations complete", 180),
    ("M3", "Superstructure frame complete", 400),
    ("M4", "Mechanical and electrical first fix complete", 550),
    ("M5", "Testing and commissioning complete", 650),
    ("M6", "Taking Over of the Works", TIME_FOR_COMPLETION_DAYS),
]

# FIDIC-style clause titles; each maps to a list of (sub-clause, title) pairs.
CLAUSES = [
    ("1", "General Provisions", [
        ("1.1", "Definitions"),
        ("1.2", "Interpretation"),
        ("1.3", "Communications"),
        ("1.4", "Law and Language"),
        ("1.5", "Priority of Documents"),
    ]),
    ("2", "The Employer", [
        ("2.1", "Right of Access to the Site"),
        ("2.2", "Permits, Licences and Approvals"),
        ("2.3", "Employer\u2019s Personnel"),
        ("2.4", "Employer\u2019s Financial Arrangements"),
        ("2.5", "Employer\u2019s Claims"),
    ]),
    ("3", "The Engineer", [
        ("3.1", "Engineer\u2019s Duties and Authority"),
        ("3.2", "Delegation by the Engineer"),
        ("3.3", "Instructions of the Engineer"),
        ("3.4", "Replacement of the Engineer"),
        ("3.5", "Determinations"),
    ]),
    ("4", "The Contractor", [
        ("4.1", "Contractor\u2019s General Obligations"),
        ("4.2", "Performance Security"),
        ("4.3", "Contractor\u2019s Representative"),
        ("4.4", "Subcontractors"),
        ("4.5", "Assignment of Benefit of Subcontract"),
        ("4.6", "Co-operation"),
        ("4.7", "Setting Out"),
        ("4.8", "Safety Procedures"),
        ("4.9", "Quality Assurance"),
        ("4.10", "Site Data"),
    ]),
    ("5", "Subcontracting", [
        ("5.1", "Consent to Subcontracting"),
        ("5.2", "Subcontractors"),
        ("5.3", "Nominated Subcontractors"),
        ("5.4", "Evidence of Payments"),
    ]),
    ("6", "Staff and Labour", [
        ("6.1", "Engagement of Staff and Labour"),
        ("6.2", "Rates of Wages and Conditions of Labour"),
        ("6.3", "Persons in the Service of the Employer"),
        ("6.4", "Labour Laws"),
        ("6.5", "Working Hours"),
        ("6.6", "Facilities for Staff and Labour"),
        ("6.7", "Health and Safety"),
    ]),
    ("7", "Plant, Materials and Workmanship", [
        ("7.1", "Manner of Execution"),
        ("7.2", "Samples"),
        ("7.3", "Inspection"),
        ("7.4", "Testing"),
        ("7.5", "Rejection"),
        ("7.6", "Remedial Work"),
    ]),
    ("8", "Commencement, Delays and Suspension", [
        ("8.1", "Commencement of Works"),
        ("8.2", "Time for Completion"),
        ("8.3", "Programme"),
        ("8.4", "Extension of Time for Completion"),
        ("8.5", "Delays Caused by Authorities"),
        ("8.6", "Rate of Progress"),
        ("8.7", "Delay Damages"),
        ("8.8", "Suspension of Work"),
        ("8.9", "Consequences of Suspension"),
        ("8.10", "Payment for Plant and Materials in Event of Suspension"),
    ]),
    ("9", "Tests on Completion", [
        ("9.1", "Contractor\u2019s Obligations"),
        ("9.2", "Delayed Tests"),
        ("9.3", "Retesting"),
        ("9.4", "Failure to Pass Tests on Completion"),
    ]),
    ("10", "Employer\u2019s Taking Over", [
        ("10.1", "Taking Over of the Works and Sections"),
        ("10.2", "Taking Over of Parts of the Works"),
        ("10.3", "Interference with Tests on Completion"),
    ]),
    ("11", "Defects Liability", [
        ("11.1", "Completion of Outstanding Work and Remedying Defects"),
        ("11.2", "Cost of Remedying Defects"),
        ("11.3", "Extension of Defects Notification Period"),
        ("11.4", "Failure to Remedy Defects"),
        ("11.5", "Removal of Defective Work"),
        ("11.6", "Further Tests"),
        ("11.7", "Right of Access"),
    ]),
    ("12", "Measurement and Evaluation", [
        ("12.1", "Works to be Measured"),
        ("12.2", "Method of Measurement"),
        ("12.3", "Evaluation"),
        ("12.4", "Omissions"),
    ]),
    ("13", "Variations and Adjustments", [
        ("13.1", "Right to Vary"),
        ("13.2", "Value Engineering"),
        ("13.3", "Variation Procedure"),
        ("13.4", "Payment in Applicable Currencies"),
        ("13.5", "Provisional Sums"),
        ("13.6", "Daywork"),
        ("13.7", "Adjustments for Changes in Legislation"),
    ]),
    ("14", "Contract Price and Payment", [
        ("14.1", "The Contract Price"),
        ("14.2", "Advance Payment"),
        ("14.3", "Application for Interim Payment Certificates"),
        ("14.4", "Schedule of Payments"),
        ("14.5", "Plant and Materials intended for the Works"),
        ("14.6", "Issue of Interim Payment Certificates"),
        ("14.7", "Payment"),
        ("14.8", "Delayed Payment"),
        ("14.9", "Payment of Retention Money"),
        ("14.10", "Statement at Completion"),
        ("14.11", "Application for Final Payment Certificate"),
        ("14.12", "Discharge"),
        ("14.13", "Issue of Final Payment Certificate"),
        ("14.14", "Cessation of Employer\u2019s Liability"),
    ]),
    ("15", "Termination by Employer", [
        ("15.1", "Notice to Correct"),
        ("15.2", "Termination by Employer"),
        ("15.3", "Valuation at Date of Termination"),
        ("15.4", "Payment after Termination"),
    ]),
    ("16", "Suspension and Termination by Contractor", [
        ("16.1", "Contractor\u2019s Entitlement to Suspend Work"),
        ("16.2", "Termination by Contractor"),
        ("16.3", "Cessation of Work and Removal of Contractor\u2019s Equipment"),
        ("16.4", "Payment on Termination"),
    ]),
    ("17", "Risk and Responsibility", [
        ("17.1", "Indemnities"),
        ("17.2", "Contractor\u2019s Care of the Works"),
        ("17.3", "Employer\u2019s Risks"),
        ("17.4", "Consequences of Employer\u2019s Risks"),
        ("17.5", "Intellectual and Industrial Property Rights"),
    ]),
    ("18", "Insurance", [
        ("18.1", "General Requirements for Insurances"),
        ("18.2", "Insurance for Works and Contractor\u2019s Equipment"),
        ("18.3", "Insurance against Injury to Persons and Damage to Property"),
        ("18.4", "Insurance for Contractor\u2019s Personnel"),
    ]),
    ("19", "Force Majeure", [
        ("19.1", "Definition of Force Majeure"),
        ("19.2", "Notice of Force Majeure"),
        ("19.3", "Duty to Minimise Delay"),
        ("19.4", "Consequences of Force Majeure"),
        ("19.5", "Optional Termination, Payment and Release"),
    ]),
    ("20", "Claims, Disputes and Arbitration", [
        ("20.1", "Contractor\u2019s Claims"),
        ("20.2", "Appointment of the Dispute Adjudication Board"),
        ("20.3", "Failure to Agree Dispute Adjudication Board"),
        ("20.4", "Obtaining Dispute Adjudication Board\u2019s Decision"),
        ("20.5", "Amicable Settlement"),
        ("20.6", "Arbitration"),
        ("20.7", "Failure to Comply with Dispute Adjudication Board\u2019s Decision"),
    ]),
]

_BODY_SENTENCES = [
    "The parties shall perform their respective obligations under the Contract "
    "in good faith and with the skill, care and diligence expected of competent "
    "practitioners experienced in works of a similar nature, size and complexity.",
    "Unless otherwise stated, nothing in this Clause relieves either party of a "
    "liability, duty or obligation that the Contract imposes on it, and no "
    "waiver of any provision is effective unless it is in writing and signed.",
    "Any notice, consent, approval, certificate or determination to which this "
    "Clause refers shall be in writing, shall not be unreasonably withheld or "
    "delayed, and shall be copied to the parties identified in the Contract Data.",
    "The Contractor shall comply with the requirements of this Clause and with "
    "all applicable Laws, and shall keep the Employer indemnified against any "
    "claim arising from a failure to do so, to the extent such claim is caused "
    "by the Contractor\u2019s breach.",
    "Where the Contract requires a matter to be agreed, determined or certified, "
    "the Engineer shall consult each party before proceeding, and each "
    "determination shall be reasoned and copied to both parties.",
    "Time shall be of the essence for the obligations to which this Clause "
    "applies; any period expressed in days shall be calculated as calendar days, "
    "and any period expiring on a non-working day shall be deemed to expire on "
    "the next working day.",
    "The Contractor shall maintain at the Site such records, registers and "
    "contemporary evidence as are necessary to substantiate any matter arising "
    "under this Clause, and shall make them available to the Engineer on request.",
    "Any sum that becomes payable under this Clause shall be included in the "
    "next Payment Certificate and shall bear interest at the rate stated in the "
    "Contract Data from the date it becomes due until the date of payment.",
]


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _subclause_paragraphs(rng: random.Random, n: int) -> list[str]:
    """``n`` deterministic paragraphs drawn from the body-sentence pool."""
    paragraphs = []
    for _ in range(n):
        pool = list(_BODY_SENTENCES)
        rng.shuffle(pool)
        paragraphs.append(" ".join(pool[: rng.randint(3, 4)]))
    return paragraphs


def build_contract_story(seed: int = SEED):
    rng = _rng(seed)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=14, spaceAfter=10)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11, spaceBefore=8, spaceAfter=4)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=9.5, spaceBefore=6, spaceAfter=3)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9, leading=12.5, spaceAfter=6)

    story = []

    # Cover.
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph(PROJECT_NAME, styles["Title"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(f"Project No. {PROJECT_NO}", styles["Heading2"]))
    story.append(Paragraph("Conditions of Contract", styles["Heading2"]))
    story.append(Spacer(1, 20 * mm))
    story.append(Paragraph(f"Between {EMPLOYER} and {CONTRACTOR}", body))
    story.append(Paragraph(f"Site: {SITE}", body))
    story.append(Paragraph("Synthetic fixture \u2014 for pilot evaluation only.", body))
    story.append(PageBreak())

    # Table of contents.
    story.append(Paragraph("Table of Contents", h1))
    for num, title, _subs in CLAUSES:
        story.append(Paragraph(f"Clause {num} \u2014 {title}", body))
    story.append(Paragraph("Contract Data", body))
    story.append(Paragraph("Schedule 1 \u2014 Milestone Schedule", body))
    story.append(Paragraph("Schedule 2 \u2014 Bill of Quantities (reference)", body))
    story.append(PageBreak())

    # Contract data.
    story.append(Paragraph("Contract Data", h1))
    cd_rows = [
        ("Time for Completion", f"{TIME_FOR_COMPLETION_DAYS} days from the Commencement Date"),
        ("Delay Damages (LD) rate", f"{LD_RATE_PCT_PER_DAY:.2f}% of the Final Contract Price per day"),
        ("Delay Damages cap", f"{LD_CAP_PCT:.1f}% of the Final Contract Price"),
        ("Defects Notification Period (DNP)", f"{DNP_DAYS} days"),
        ("Engineer", ENGINEER),
        ("Performance Security", f"{PERFORMANCE_SECURITY_PCT:.1f}% of the Accepted Contract Amount"),
        ("Advance Payment", f"{ADVANCE_PAYMENT_PCT:.1f}% of the Accepted Contract Amount"),
        ("Retention", f"{RETENTION_PCT:.1f}% (limit {RETENTION_LIMIT_PCT:.1f}%)"),
        ("Contract Sum", f"{CONTRACT_SUM:,.2f}"),
    ]
    table = Table([[k, v] for k, v in cd_rows], colWidths=[70 * mm, 90 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    story.append(PageBreak())

    # General Conditions.
    story.append(Paragraph("General Conditions", h1))
    for num, title, subs in CLAUSES:
        story.append(Paragraph(f"Clause {num} \u2014 {title}", h2))
        for sub_num, sub_title in subs:
            story.append(Paragraph(f"{sub_num} {sub_title}", h3))
            for para in _subclause_paragraphs(rng, rng.randint(2, 3)):
                story.append(Paragraph(para, body))

    # Milestones.
    story.append(PageBreak())
    story.append(Paragraph("Schedule 1 \u2014 Milestone Schedule", h1))
    m_rows = [["Milestone", "Description", "Due (days from Commencement)"]]
    for code, desc, day in MILESTONES:
        m_rows.append([code, desc, str(day)])
    m_table = Table(m_rows, colWidths=[25 * mm, 105 * mm, 40 * mm])
    m_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(m_table)

    return story


def _deterministic_canvas(filename, **kwargs):
    """A canvas with a fixed CreationDate so regeneration is byte-stable."""
    kwargs["invariant"] = 1
    return canvas.Canvas(filename, **kwargs)


_FIXED_ID_HEX = b"0" * 32


def _determinize_pdf_id(data: bytes) -> bytes:
    """Replace the random trailer /ID with a fixed one (length-preserving)."""
    return re.sub(
        rb"<[0-9a-fA-F]{32}><[0-9a-fA-F]{32}>",
        b"<" + _FIXED_ID_HEX + b"><" + _FIXED_ID_HEX + b">",
        data,
        count=1,
    )


def generate_contract(path: Path, seed: int = SEED) -> Path:
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=PROJECT_NAME,
        canvasmaker=_deterministic_canvas,
    )
    doc.build(build_contract_story(seed))
    path.write_bytes(_determinize_pdf_id(path.read_bytes()))
    return path


# --- bill of quantities -----------------------------------------------------
BOQ_SECTIONS = [
    ("A", "Preliminaries", ["Site establishment and temporary works", "Temporary fencing and hoarding", "Site offices and welfare facilities", "Traffic management and signage", "Survey and setting out"]),
    ("B", "Site Preparation and Earthworks", ["Site clearance and grubbing", "Bulk excavation", "Cart away of excavated material", "Imported granular fill", "Compaction and testing"]),
    ("C", "Piling and Foundations", ["Driven precast concrete piles", "Bored cast-in-place piles", "Pile caps", "Ground beams", "Blinding concrete"]),
    ("D", "Concrete Works", ["Reinforced concrete in substructure", "Reinforced concrete in superstructure", "Formwork", "Reinforcement", "Waterproofing and tanking"]),
    ("E", "Structural Steelwork", ["Fabrication and supply of structural steel", "Erection of structural steel", "Fire protection to steelwork", "Bolts, welds and connections", "Metal decking"]),
    ("F", "Building Envelope and Finishes", ["External cladding", "Roofing and insulation", "Internal partitions", "Floor finishes", "Ceilings and paintwork"]),
    ("G", "Mechanical Services", ["HVAC plant and ductwork", "Plumbing and drainage", "Fire suppression systems", "Lifts and vertical transport", "BMS controls"]),
    ("H", "Electrical Services", ["Main switchgear and distribution", "Lighting and small power", "Fire detection and alarm", "Security and access control", "Earthing and lightning protection"]),
    ("I", "External Works", ["Roads and paving", "Drainage and manholes", "Landscaping and planting", "Boundary walls and gates", "External lighting"]),
    ("J", "Provisional Sums", ["Contingency for utility diversions", "Dayworks allowance", "Testing and commissioning allowance"]),
]

BOQ_UNITS = ["m\u00b3", "m\u00b2", "m", "t", "nr", "wk", "day", "lump"]


def _boq_items(rng: random.Random) -> list[tuple[str, str, str, float, float]]:
    """Deterministic BOQ lines: (ref, description, unit, quantity, rate)."""
    items = []
    for sec_code, _sec_name, descs in BOQ_SECTIONS:
        # Deterministic per-section line count in [25, 45].
        count = 25 + (sum(ord(c) for c in sec_code) % 20)
        for i in range(count):
            desc = descs[i % len(descs)]
            variant = rng.randint(1, 4)
            desc = f"{desc} \u2014 type {variant}"
            unit = BOQ_UNITS[rng.randrange(len(BOQ_UNITS))]
            quantity = round(rng.uniform(1.0, 1000.0), 2)
            rate = round(rng.uniform(5.0, 2500.0), 2)
            ref = f"{sec_code}{i + 1}"
            items.append((ref, desc, unit, quantity, rate))
    return items


def generate_boq(path: Path, seed: int = SEED) -> Path:
    rng = _rng(seed)
    wb = openpyxl.Workbook()
    # Fixed timestamps so a regeneration is byte-stable (openpyxl stamps
    # docProps/core.xml with the current time otherwise).
    _fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    wb.properties.created = _fixed
    wb.properties.modified = _fixed
    ws = wb.active
    ws.title = "Bill of Quantities"
    ws.append(["Ref", "Description", "Unit", "Quantity", "Rate", "Amount"])
    ws["A1"].font = Font(bold=True)
    ws["B1"].font = Font(bold=True)
    ws["C1"].font = Font(bold=True)
    ws["D1"].font = Font(bold=True)
    ws["E1"].font = Font(bold=True)
    ws["F1"].font = Font(bold=True)

    section_totals = {}
    current_section = None
    for ref, desc, unit, quantity, rate in _boq_items(rng):
        sec = ref[:1]
        if sec != current_section:
            section_name = next(n for c, n, _ in BOQ_SECTIONS if c == sec)
            ws.append([f"Section {sec}", section_name, "", "", "", ""])
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
            current_section = sec
            section_totals[sec] = 0.0
        amount = round(quantity * rate, 2)
        section_totals[sec] += amount
        ws.append([ref, desc, unit, quantity, rate, amount])

    # Summary sheet.
    summary = wb.create_sheet("Summary")
    summary.append(["Section", "Description", "Total"])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    grand = 0.0
    for sec, name, _ in BOQ_SECTIONS:
        total = section_totals.get(sec, 0.0)
        grand += total
        summary.append([f"Section {sec}", name, round(total, 2)])
    summary.append(["", "Total (excluding Provisional Sums markup)", round(grand, 2)])
    wb.save(path)
    return path


def main() -> None:
    contract = generate_contract(OUT_DIR / "contract.pdf")
    boq = generate_boq(OUT_DIR / "boq.xlsx")
    print(f"wrote {contract.name} ({contract.stat().st_size} bytes)")
    print(f"wrote {boq.name} ({boq.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
