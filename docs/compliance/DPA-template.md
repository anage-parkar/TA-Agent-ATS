# Data Processing Addendum (DPA) — Template

> Template only — not legal advice. Have counsel review and adapt before use.
> Capitalised terms follow GDPR Art. 4 / UK GDPR.

This DPA forms part of the Agreement between the **Customer** ("Controller") and
**TA Agent** ("Processor") and governs Processing of Personal Data on the
Controller's behalf.

## 1. Subject matter & roles
The Processor provides an AI-assisted applicant-tracking service. The Customer is
the Controller of candidate Personal Data; the Processor acts only on documented
instructions (the Agreement, this DPA, and product configuration).

## 2. Nature & purpose of Processing
Ingesting applications; AI-assisted parsing, ranking, and reply classification
(on redacted profiles); human-in-the-loop review; outreach and scheduling.

## 3. Categories of data subjects & data
Job applicants. Identity/contact details, CV/profile content, application
outcomes, communications, and — where voluntarily provided — diversity (EEO)
data, which is segregated and used only for aggregate adverse-impact reporting.

## 4. Processor obligations
- Process only on the Controller's documented instructions.
- Ensure personnel are bound by confidentiality.
- Implement the technical & organizational measures in Annex II (see
  `soc2-readiness.md`): RLS tenant isolation, RBAC, audit logging, encryption in
  transit/at rest, data minimization (redacted scoring), least privilege.
- Assist the Controller with data-subject requests (access, portability,
  erasure, contest) via the product's export/erasure/transparency endpoints.
- Notify the Controller without undue delay of a Personal Data Breach.
- Make available information to demonstrate compliance and allow audits.

## 5. Sub-processors
The Controller authorizes the sub-processors listed in `subprocessors.md`
(served at `/api/legal/subprocessors`). The Processor notifies the Controller of
intended changes and remains liable for sub-processor compliance.

## 6. International transfers
Where data leaves its region, transfers rely on an appropriate safeguard (e.g.
SCCs). Data residency is configurable per tenant (`organizations.region`).

## 7. Automated decision-making (GDPR Art. 22 / EU AI Act)
The service does **not** make solely-automated rejection decisions: AI ranks and
surfaces with evidence; a human reviews outcomes, and candidates may request
human review / contest an assessment. AI use is disclosed to candidates.

## 8. Retention & deletion
Personal Data is retained per the Controller's configured retention period and
deleted automatically thereafter; on termination, the Processor deletes or
returns Personal Data at the Controller's choice.

## Annex I — Details of Processing
(Parties, duration, nature/purpose, data categories — per §2–3.)

## Annex II — Technical & Organizational Measures
See `docs/compliance/soc2-readiness.md`.
