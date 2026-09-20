"""
Generates FICTIONAL sample contracts (PDF) for tests and demos. Nothing here is real.

    python -m sample_data.generate          # writes PDFs next to this file

long_contract_text() is deliberately > 12,000 characters, with the effective date on page 1
but the term, renewal, termination and reporting facts in the LAST sections, so any pipeline
that only reads the start of the document will miss them.
"""

import sys
import textwrap
from pathlib import Path

import pymupdf

PARTY_PROVIDER = "Northwind Analytics Inc."
PARTY_CUSTOMER = "Contoso Retail LLC"


def write_pdf(path, text: str, fontsize: float = 10, width: int = 92, lines_per_page: int = 52) -> None:
    lines = []
    for para in text.split("\n"):
        lines.extend(textwrap.wrap(para, width=width) or [""])
    doc = pymupdf.open()
    for i in range(0, len(lines), lines_per_page):
        page = doc.new_page()
        y = 60
        for ln in lines[i:i + lines_per_page]:
            if ln:
                page.insert_text((56, y), ln, fontsize=fontsize, fontname="helv")
            y += 13.5
    doc.save(str(path))
    doc.close()


def write_blank_pdf(path) -> None:
    """A valid PDF with no text layer (stands in for a scanned document)."""
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()


_FILLER = {
    "1. Definitions": (
        'In this Agreement, "Services" means the analytics, reporting and support services described in each Statement of Work; '
        '"Confidential Information" means non-public information disclosed by one party to the other that is marked confidential '
        'or would reasonably be understood to be confidential; and "Deliverables" means the reports and dashboards that Provider '
        "prepares for Customer under a Statement of Work. Capitalised terms not defined here have the meaning given in the relevant Statement of Work."
    ),
    "2. Scope of Services": (
        "Provider will perform the Services in a professional manner using suitably qualified personnel. Each Statement of Work will describe "
        "the scope, the Deliverables, the acceptance criteria and the fees. Changes to a Statement of Work are effective only when signed by both parties. "
        "Provider will use commercially reasonable efforts to meet any target dates stated in a Statement of Work but such dates are estimates unless expressly stated to be fixed."
    ),
    "3. Customer Responsibilities": (
        "Customer will give Provider timely access to the data, systems and personnel that Provider reasonably needs to perform the Services. "
        "Customer is responsible for the accuracy of the data it provides and for obtaining any consents required to allow Provider to process that data. "
        "If Customer delays providing access or information, Provider's target dates will be extended by a corresponding period."
    ),
    "4. Fees and Payment": (
        "Customer shall pay Provider a monthly fee of $12,500, invoiced on the first day of each calendar month and payable within thirty (30) days of the invoice date. "
        "Undisputed amounts that are not paid when due accrue interest at the rate of 1.5% per month or the maximum rate permitted by law, whichever is lower. "
        "All fees are exclusive of applicable taxes, which Customer will pay except for taxes on Provider's income. Customer may dispute an invoice in good faith by notifying Provider within fifteen (15) days of receipt."
    ),
    "5. Expenses": (
        "Customer will reimburse Provider for reasonable, pre-approved travel and out-of-pocket expenses incurred in performing the Services, "
        "provided that Provider submits receipts with the relevant invoice. Expenses that have not been approved in writing in advance are not reimbursable."
    ),
    "6. Intellectual Property": (
        "Each party retains ownership of the intellectual property it owned before this Agreement. Provider grants Customer a non-exclusive, non-transferable licence "
        "to use the Deliverables for Customer's internal business purposes. Provider retains ownership of its pre-existing tools, methods and know-how, "
        "including any improvements to them that are not specific to Customer's data."
    ),
    "7. Confidentiality": (
        "Each party will protect the other's Confidential Information with at least the same care it uses for its own confidential information, and no less than reasonable care. "
        "A party may disclose Confidential Information only to its employees and advisers who need to know it and who are bound by equivalent confidentiality duties. "
        "These obligations do not apply to information that is public through no fault of the receiving party, was already known to it, or is independently developed."
    ),
    "8. Data Protection": (
        "To the extent Provider processes personal data on behalf of Customer, Provider will process it only on Customer's documented instructions, "
        "will implement appropriate technical and organisational security measures, and will notify Customer without undue delay after becoming aware of a personal data breach. "
        "Provider will not engage a sub-processor without giving Customer prior notice and an opportunity to object."
    ),
    "9. Warranties": (
        "Each party warrants that it has the authority to enter into this Agreement. Provider warrants that the Services will be performed with reasonable skill and care. "
        "Except as expressly stated in this Agreement, all other warranties, whether express or implied, are excluded to the fullest extent permitted by law."
    ),
    "10. Limitation of Liability": (
        "Neither party is liable for indirect, incidental or consequential loss, or for loss of profits or revenue, arising out of this Agreement. "
        "Each party's total aggregate liability under this Agreement is limited to the fees paid or payable by Customer in the twelve (12) months before the event giving rise to the claim. "
        "Nothing in this Agreement limits liability that cannot be limited by law."
    ),
    "11. Indemnification": (
        "Provider will defend Customer against any third-party claim alleging that the Deliverables, as supplied by Provider, infringe that third party's intellectual property rights, "
        "and will pay the damages finally awarded or agreed in settlement, provided that Customer promptly notifies Provider of the claim and allows Provider to control its defence."
    ),
    "12. Force Majeure": (
        "Neither party is in breach of this Agreement for a delay or failure caused by events beyond its reasonable control, including natural disaster, war, government action, "
        "or widespread failure of public utilities or telecommunications networks, provided that it notifies the other party promptly and uses reasonable efforts to resume performance."
    ),
    "13. Insurance": (
        "Provider will maintain commercial general liability insurance and professional indemnity insurance with reputable insurers throughout the term of this Agreement "
        "and will give Customer a certificate of insurance on request."
    ),
    "14. Subcontracting and Assignment": (
        "Provider may use subcontractors to perform the Services but remains responsible for their acts and omissions. Neither party may assign this Agreement without the other party's "
        "prior written consent, except to a successor in a merger or a sale of all or substantially all of its business."
    ),
    "15. Notices": (
        "Notices under this Agreement must be in writing and delivered by hand, by recognised courier or by email to the address stated in the relevant Statement of Work. "
        "A notice is treated as received on delivery by hand or courier, or on the next business day after it is sent by email."
    ),
    "16. Governing Law": (
        "This Agreement is governed by the laws of the State of Delaware, without regard to its conflict of laws rules. The parties submit to the exclusive jurisdiction "
        "of the state and federal courts located in Delaware for any dispute arising out of this Agreement."
    ),
    "17. Dispute Resolution": (
        "The parties will first try to resolve any dispute through good-faith discussions between senior executives for a period of at least twenty (20) days before starting proceedings, "
        "except that either party may seek urgent injunctive relief at any time."
    ),
}

_LATE = {
    "18. Term and Renewal": (
        "The initial term of this Agreement begins on the Effective Date and expires on December 31, 2028 (the \"Expiration Date\"). "
        "This Agreement renews automatically for successive one (1) year terms unless either party gives written notice of non-renewal "
        "at least sixty (60) days before the end of the then-current term."
    ),
    "19. Termination": (
        "Either party may terminate this Agreement for convenience by giving ninety (90) days written notice to the other party. "
        "Either party may terminate this Agreement immediately by written notice if the other party materially breaches it and fails to cure the breach within thirty (30) days "
        "after receiving notice of the breach."
    ),
    "20. Reporting Obligations": (
        "Customer shall deliver an annual security audit report to Provider by March 31, 2027. "
        "Provider shall deliver a quarterly service performance report to Customer within fifteen (15) days after the end of each calendar quarter."
    ),
    "21. Entire Agreement": (
        "This Agreement, together with each Statement of Work, is the entire agreement between the parties on its subject matter and replaces all earlier discussions and agreements. "
        "Any amendment must be in writing and signed by both parties."
    ),
}


_EXTRA = {
    "Service Levels": (
        "Provider will make the hosted reporting platform available at least 99.5% of the time in each calendar month, measured excluding scheduled maintenance of up to eight (8) hours per month "
        "notified at least five (5) business days in advance. If availability falls below that level in a month, Customer's sole remedy is a service credit calculated as set out in the "
        "applicable Statement of Work. Provider will respond to priority-one support requests within four (4) business hours and to all other requests within two (2) business days."
    ),
    "Acceptance of Deliverables": (
        "Customer will review each Deliverable within ten (10) business days after delivery and either accept it or give Provider written notice of the respects in which it does not conform to the "
        "acceptance criteria. Provider will correct any non-conformity within a reasonable period and resubmit the Deliverable. A Deliverable that Customer does not reject within the review period is treated as accepted."
    ),
    "Change Control": (
        "Either party may propose a change to the scope, timetable or fees of a Statement of Work by giving the other a written change request describing the change and its expected effect. "
        "Provider will assess the request and respond with an estimate of the impact on fees and delivery dates. No change takes effect until both parties have signed a written change order."
    ),
    "Personnel and Key Contacts": (
        "Each party will nominate a relationship manager who is responsible for day-to-day communication and for escalating issues. Provider will not remove a named key person from the Services "
        "without reasonable prior notice unless the person leaves Provider's employment, is unavailable through illness or is removed for misconduct, in which case Provider will provide a suitably qualified replacement."
    ),
    "Security Measures": (
        "Provider will maintain a written information security programme that includes access controls, encryption of Customer data in transit and at rest, logging and monitoring, "
        "vulnerability management and staff security training. Provider will make available, on reasonable request and no more than once in any twelve (12) month period, "
        "a summary of the results of an independent assessment of its security controls."
    ),
    "Audit Rights": (
        "On at least thirty (30) days' notice and no more than once per year, Customer may audit Provider's records that relate directly to the fees charged under this Agreement. "
        "Audits will take place during normal business hours, will be conducted so as to minimise disruption, and will be at Customer's cost unless they reveal an overcharge of more than five percent (5%)."
    ),
    "Publicity": (
        "Neither party will issue a press release or make a public statement about this Agreement without the prior written consent of the other, "
        "except as required by law or by the rules of a securities exchange."
    ),
    "Non-Solicitation": (
        "During the term of this Agreement and for six (6) months afterwards, neither party will directly solicit for employment any employee of the other who was materially involved in the Services, "
        "other than through general advertisements that are not targeted at that employee."
    ),
    "Business Continuity": (
        "Provider will maintain and periodically test a business continuity and disaster recovery plan designed to restore the Services within a reasonable time after a disruptive event. "
        "Provider will give Customer a summary of the plan on request and will notify Customer promptly if the plan is invoked in a way that affects the Services."
    ),
    "Compliance with Laws": (
        "Each party will comply with the laws that apply to its performance of this Agreement, including anti-bribery, export control and sanctions laws. "
        "Neither party will offer or accept any improper payment or advantage in connection with this Agreement."
    ),
    "Survival": (
        "Provisions that by their nature are intended to continue after termination or expiry, including those concerning confidentiality, intellectual property, limitation of liability, "
        "indemnification, payment of accrued fees and governing law, will survive the end of this Agreement."
    ),
    "Training and Knowledge Transfer": (
        "Provider will provide up to sixteen (16) hours of remote training for Customer's users during the first ninety (90) days after the Effective Date, at no additional charge. "
        "Further training, on-site sessions and bespoke documentation will be provided at Provider's then-current rates and will be documented in a Statement of Work or change order."
    ),
    "Records Retention": (
        "Provider will keep the working papers and supporting data for each Deliverable for at least twenty-four (24) months after the Deliverable is accepted, "
        "and will make them available to Customer on reasonable request, subject to the confidentiality obligations in this Agreement."
    ),
    "Third-Party Software": (
        "Where the Services depend on software or data sources supplied by a third party, Provider will identify them in the relevant Statement of Work. Customer will comply with the licence terms "
        "of any third-party software that Provider makes available to it, and Provider is not liable for defects in third-party software except to the extent that the third party is liable to Provider."
    ),
    "Benchmarking and Aggregated Data": (
        "Provider may use data derived from the Services in aggregated and anonymised form to improve its products and to prepare industry benchmarks, provided that the data does not identify Customer, "
        "its customers or any individual, and provided that Provider does not disclose Customer's Confidential Information in doing so."
    ),
    "Return of Customer Data": (
        "Within thirty (30) days after the end of this Agreement and on Customer's written request, Provider will return or securely delete Customer's data held by Provider, "
        "except to the extent that Provider is required by law to retain it. Provider will confirm the deletion in writing on request."
    ),
    "Cooperation on Regulatory Enquiries": (
        "Each party will give the other reasonable assistance, at the requesting party's cost, in responding to a request or enquiry from a regulator that relates to the Services, "
        "including providing relevant records and making appropriate personnel available at mutually convenient times."
    ),
    "Independent Contractors": (
        "The parties are independent contractors. Nothing in this Agreement creates a partnership, joint venture, agency or employment relationship, and neither party has authority to bind the other."
    ),
    "Waiver and Severability": (
        "A failure or delay in exercising a right under this Agreement is not a waiver of that right. If any provision of this Agreement is held to be invalid or unenforceable, "
        "the remaining provisions will continue in full force and the invalid provision will be modified to the minimum extent necessary to make it enforceable."
    ),
}


def _section(number: int, title: str, body: str) -> str:
    import re
    return f"{number}. {re.sub(r'^\d+\.\s*', '', title)}\n{body}\n"


def long_contract_text() -> str:
    parts = [
        "MASTER SERVICES AGREEMENT\n",
        (
            'This Master Services Agreement (the "Agreement") is entered into as of January 15, 2026 (the "Effective Date") by and between '
            f'{PARTY_PROVIDER}, a Delaware corporation ("Provider"), and {PARTY_CUSTOMER}, a Texas limited liability company ("Customer").\n'
        ),
    ]
    # Numbered in order; the term/renewal/termination/reporting facts land in the last sections.
    for n, (title, body) in enumerate([*_FILLER.items(), *_EXTRA.items(), *_LATE.items()], start=1):
        parts.append(_section(n, title, body))
    parts.append("IN WITNESS WHEREOF, the parties have executed this Agreement as of the Effective Date.\n")
    return "\n".join(parts)


# ─── the version-comparison demo pair ─────────────────────────────────────────
# v2 differs from v1 in exactly three deliberate ways:
#   1. termination notice   thirty (30) days   ->  sixty (60) days
#   2. renewal              none (must be agreed) -> renews automatically
#   3. monthly fee          $10,000            ->  $12,000
# Everything else is word-for-word identical.

_DEMO_COMMON = {
    "Services": (
        "Provider will deliver the analytics and reporting services described in each Statement of Work, using suitably qualified "
        "personnel and reasonable skill and care. Customer will give Provider timely access to the data and people Provider needs."
    ),
    "Confidentiality": (
        "Each party will protect the other's Confidential Information with at least the care it uses for its own, and will use it only "
        "to perform this Agreement. These duties survive for three (3) years after the Agreement ends."
    ),
    "Data Protection": (
        "Provider will process personal data only on Customer's documented instructions and will keep it secure. Provider will tell "
        "Customer without undue delay after becoming aware of a personal data breach."
    ),
    "Limitation of Liability": (
        "Each party's total aggregate liability under this Agreement will not exceed the fees paid by Customer in the twelve (12) months "
        "before the event giving rise to the claim. Neither party is liable for indirect or consequential loss."
    ),
    "Governing Law": (
        "This Agreement is governed by the laws of the State of Delaware, and the courts of Delaware have exclusive jurisdiction "
        "over any dispute arising out of it."
    ),
}


def _demo_text(fee: str, notice: str, renewal_sentence: str, label: str) -> str:
    head = (
        f"MASTER SERVICES AGREEMENT ({label})\n\n"
        'This Master Services Agreement (the "Agreement") is entered into as of January 15, 2026 (the "Effective Date") by and between '
        f'{PARTY_PROVIDER}, a Delaware corporation ("Provider"), and {PARTY_CUSTOMER}, a Texas limited liability company ("Customer").\n'
    )
    sections = [
        ("Services", _DEMO_COMMON["Services"]),
        ("Fees and Payment",
         f"Customer shall pay Provider a monthly fee of {fee}, invoiced on the first day of each calendar month and payable within "
         "thirty (30) days of the invoice date. Late amounts accrue interest at 1.5% per month."),
        ("Term",
         f"The initial term begins on the Effective Date and expires on December 31, 2027 (the \"Expiration Date\"). {renewal_sentence}"),
        ("Termination",
         f"Either party may terminate this Agreement for convenience by giving {notice} written notice to the other party. "
         "Either party may terminate immediately if the other materially breaches it and fails to cure within thirty (30) days."),
        ("Confidentiality", _DEMO_COMMON["Confidentiality"]),
        ("Data Protection", _DEMO_COMMON["Data Protection"]),
        ("Limitation of Liability", _DEMO_COMMON["Limitation of Liability"]),
        ("Governing Law", _DEMO_COMMON["Governing Law"]),
    ]
    return head + "\n" + "\n".join(_section(i, t, b) for i, (t, b) in enumerate(sections, start=1))


def demo_v1_text() -> str:
    return _demo_text("$10,000", "thirty (30) days",
                      "This Agreement does not renew automatically; any renewal must be agreed in writing.", "Version 1")


def demo_v2_text() -> str:
    return _demo_text("$12,000", "sixty (60) days",
                      "This Agreement renews automatically for successive one (1) year terms.", "Version 2")


def main() -> None:
    out = Path(__file__).parent
    write_pdf(out / "long_contract.pdf", long_contract_text())
    print("wrote", out / "long_contract.pdf", f"({len(long_contract_text())} characters)")
    write_pdf(out / "northwind_msa_v1.pdf", demo_v1_text())
    write_pdf(out / "northwind_msa_v2.pdf", demo_v2_text())
    print("wrote", out / "northwind_msa_v1.pdf", "and", out / "northwind_msa_v2.pdf", "(3 deliberate differences)")


if __name__ == "__main__":
    sys.exit(main())
