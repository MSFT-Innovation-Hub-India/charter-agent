**From:** Kishore P <kishorep@onemtc.net>
**To:** Sandeep Srinivasa <sansri@microsoft.com>
**Cc:** Vishakha Arbat <viarbat@microsoft.com>
**Date:** [Day 6 of response window], 18:42 IST
**Subject:** AHN-RFP-2026-CARE-009 — Case studies & testimonials pack (draft for the §9 annexure)

Hi Sandeep,

As discussed in the kickoff, here's my consolidated pack for the **Case Studies & Testimonials** section of the Aurora response. I've picked four engagements that map cleanly to the AHN RFP's evaluation criterion — *healthcare domain, Epic / FHIR integration* (15% weight) — and added two short customer testimonial quotes at the end that we already have written permission to reproduce in proposals.

Vishakha — flagging two integration call-outs in CS-1 and CS-3 that overlap with what you're writing in the technical proposal (FHIR R4 backend services auth pattern, partner HL7 v2 over MLLP). Worth making sure the language is consistent across both sections before we lock the draft on Day 8.

Assumptions I've made (please correct any that are wrong):

- We can name **Sunhaven Hospitals** and **Trillium Care Group** in the response — both have evergreen reference-customer clauses in their MSAs with us. I have **not** named the third (US-based) customer in CS-4; used "a US Pacific-Northwest integrated delivery network" instead, per their reference policy.
- Aurora is on Epic; all four case studies I've picked are Epic engagements. I left out our two Cerner/Oracle Health references — happy to add one as a fifth if you'd rather show platform breadth than depth.
- Numbers (appointment volumes, p95 latencies, go-live dates) are taken from the closed-out project records on SharePoint. I'll re-verify each one against the signed go-live acceptance memo before we submit on Day 10.
- Format below is the rough shape I'd put into the PDF annexure — one page per case study, logo at top-right, the testimonial quotes as a separate two-page spread at the end. The PDF designer will tidy up; this is the content draft.

Shout if any of this needs reframing. I'll be online tomorrow (Day 7) for the integration walkthrough with Vishakha.

Thanks,
Kishore

---

# Annexure D — Case Studies & Customer Testimonials

**In response to:** AHN-RFP-2026-CARE-009 §8 (Evaluation Criteria — Case studies and credentials)
**Prepared by:** Meridian InfoSystems Pvt. Ltd. — Delivery Practice
**Author:** Kishore P, Director — Healthcare Delivery (kishorep@onemtc.net)
**Document reference:** MIS-AHN-2026-CS-001 Rev 0.9 (Draft for internal review)

---

## CS-1 · Sunhaven Hospitals — Patient Scheduling Modernisation on Epic

| | |
|---|---|
| **Customer** | Sunhaven Hospitals Pvt. Ltd. |
| **Sector / Geography** | Multi-specialty hospital chain; 9 hospitals across Karnataka and Andhra Pradesh |
| **Engagement period** | Mar 2024 – Feb 2025 (11 months build + 3 months hypercare) |
| **Meridian role** | Prime vendor — solution design, build, integration, migration, hypercare |
| **EHR / integration surface** | Epic Hyperspace (Foundation 2023 + IU3); FHIR R4 via Epic Interconnect; SMART-on-FHIR backend services auth |
| **Cloud / stack** | Azure India Central + India South DR; AKS; PostgreSQL Flex; Azure API Management; Azure Front Door |
| **Team size at peak** | 28 (Meridian) + 6 (Sunhaven IT + clinical) |

**Business problem.** Sunhaven's legacy scheduling system, built on a heavily customised .NET Framework 4.5 stack, had become a release bottleneck — single-tenant per hospital, no API surface, and a per-deployment regression cycle of 6 weeks. Front-desk staff were double-booking ~3.4% of consultation slots. Patient no-show rates were 18% with no reminder workflow.

**What we delivered.**
- A multi-tenant scheduling and appointment-management platform on Azure, deployed once and consumed by all nine hospitals.
- Bidirectional integration with Epic via FHIR R4 — `Patient`, `Schedule`, `Slot`, `Appointment` ($book / $cancel), `Encounter`, `Practitioner`, `Location`.
- SMS + WhatsApp reminder workflow integrated with the Client's existing Gupshup tenant; configurable per-specialty cadence.
- Migration of 1.4 million historical appointments and 480,000 patient records from the legacy system with a documented field-by-field reconciliation report.
- Front-desk + patient PWA front-ends; mobile-first; offline-capable for the front-desk view (in case of branch WAN outages).

**Outcomes.**
- **Slot lookup p95 latency**: 2.1 s → 270 ms.
- **Double-bookings**: 3.4% → 0.08% (residual edge cases on concurrent walk-ins).
- **No-show rate**: 18% → 11.6% after six months of reminder workflow.
- **Release cadence**: 6 weeks → bi-weekly.
- **Zero P1 incidents** in the first 90 days of hypercare; one P2 (Epic side outage misclassified as platform failure).

**Reference contact.** Available on written request through Meridian's Delivery Office; reference call protocol per Sunhaven MSA §14.

---

## CS-2 · Trillium Care Group — Lab Order & Result Exchange Hub

| | |
|---|---|
| **Customer** | Trillium Care Group |
| **Sector / Geography** | Diagnostic + multi-specialty group; 4 hospitals + 31 collection centres across Tamil Nadu |
| **Engagement period** | Aug 2024 – May 2025 (build + go-live) |
| **Meridian role** | Prime vendor — integration platform build, HL7 v2 + FHIR R4 gateway, partner onboarding |
| **EHR / integration surface** | Epic (Beaker LIS); HL7 v2.5.1 (ORM^O01, ORR^O02, ORU^R01) over MLLP; FHIR R4 (`ServiceRequest`, `DiagnosticReport`, `Observation`) for newer partners |
| **Cloud / stack** | Azure India Central; AKS; Mirth Connect (hardened build) for HL7 v2 routing; HAPI FHIR for FHIR R4 partners |

**Business problem.** Trillium's lab orders and results were exchanged with seven external diagnostic chains over a patchwork of CSV-over-SFTP, hand-rolled HL7 v2 over TCP, and one partner-specific REST endpoint. Mismatched result-to-order linking was running at ~1.7% — each mismatch was a clinical safety incident under their internal definition.

**What we delivered.**
- A unified partner integration hub — HL7 v2 (MLLP) and FHIR R4 endpoints, with a normalised internal canonical message model.
- Per-partner adapter modules; we onboarded all seven existing partners and three new ones during the engagement.
- End-to-end **order-result linkage tracker** with a configurable SLA breach alerting workflow.
- **Standing rule we enforced from day one:** no PHI in application logs. All log fields that could contain PHI go through a redaction middleware before write; a Semgrep rule in CI catches accidental log statements that touch a PHI field.

**Outcomes.**
- **Result-to-order mismatch rate**: 1.7% → 0.04% (residual cases are partner-side identifier errors, flagged for manual review).
- **Mean order → result turnaround time**: 41 min → 23 min (measured across all routine pathology orders).
- **Partner onboarding time**: 8–14 weeks (legacy bespoke effort) → 3–5 weeks (templated adapter pattern).
- Audited and cleared by Trillium's external InfoSec auditors against ISO 27001 Annex A controls; no major non-conformities.

---

## CS-3 · Banyan Multispeciality — Patient Portal + ABDM Linkage

| | |
|---|---|
| **Customer** | Banyan Multispeciality Hospitals |
| **Sector / Geography** | 6 hospitals; Maharashtra and Gujarat |
| **Engagement period** | Jan 2025 – Oct 2025 |
| **Meridian role** | Prime vendor — patient-facing PWA + mobile, Epic FHIR integration, ABDM (ABHA) linkage |
| **EHR / integration surface** | Epic MyChart-equivalent functions via FHIR R4; ABDM Health Information Provider (HIP) + Health Information User (HIU) flows |
| **Cloud / stack** | Azure India Central; Azure Entra External ID (CIAM); AKS; Azure Front Door |

**Business problem.** Banyan wanted a single patient-facing surface — appointments, reports, prescriptions, discharge summaries — that worked equally well on a low-end Android device in tier-2 towns and on a desktop browser. They also wanted to onboard to ABDM in the same engagement so that patient records could be linked to a patient's ABHA number and shared (with consent) across the national health ecosystem.

**What we delivered.**
- Patient PWA + companion Android/iOS shell apps; mobile-number + OTP login via Azure Entra External ID; optional ABHA linkage flow.
- FHIR R4 integration with Epic for appointment booking, report retrieval, discharge document download.
- ABDM HIP and HIU flows certified in sandbox before go-live; production cutover within 4 weeks of ABDM sandbox sign-off.
- Patient-side consent ledger — every share of a record under ABDM consent is recorded with consent artefact ID and patient signature timestamp.

**Outcomes.**
- **30,000+ patients linked their ABHA number** in the first 90 days post go-live.
- **Patient app store rating**: 4.6 (Android) / 4.5 (iOS) — measured at 6 months.
- **Average appointment booking completion time** (open app → confirmation screen): 47 seconds.
- **DPDP-compliance review** (independent counsel) — passed with no remediation actions.

---

## CS-4 · Pacific-Northwest IDN — Care Coordination Platform Re-platforming (anonymised)

| | |
|---|---|
| **Customer** | A US Pacific-Northwest integrated delivery network (IDN); named under NDA |
| **Sector / Geography** | 11 hospitals + 60+ ambulatory clinics; Washington and Oregon states |
| **Engagement period** | Sep 2023 – Dec 2024 |
| **Meridian role** | Build + integration partner (Sub to a US prime SI; Meridian owned the integration tier end-to-end) |
| **EHR / integration surface** | Epic; FHIR R4 + HL7 v2; bulk FHIR (`$export`) for analytics warehouse hydration |
| **Cloud / stack** | Azure US West 2 + US West 3 DR; AKS; Confluent Kafka; PostgreSQL Flex |

**Business problem.** The IDN's care-coordination platform was a 12-year-old monolith with a 9-month release cycle and no API surface; clinical-pathway changes were blocked behind the release queue, and the operations team could not easily report on patient throughput by service line.

**What we delivered.**
- Re-platformed the care-coordination tier as a set of domain microservices on Azure, fronted by a thin web app and an internal API surface for downstream analytics.
- Migrated 4.2 million active patient records and 18 months of historical encounter data with a documented reconciliation memo signed off by the IDN's Chief Medical Information Officer.
- Bulk FHIR `$export` pipeline into the IDN's existing Snowflake warehouse; daily reconciled.
- Phased cutover by service line (oncology → cardiology → general medicine → others) over 14 weeks; no big-bang switch.

**Outcomes.**
- **Release cadence**: 9 months → 3 weeks.
- **Patient-throughput reporting latency**: T+3 days → T+15 minutes (warehouse-side).
- **Cutover defects in production**: 4 P2, zero P1; all P2s resolved within hypercare window.

---

# Customer Testimonials

The following testimonial quotes are reproduced with the customer's prior written consent. Source letters are on file with Meridian's Delivery Office and can be made available to Aurora's evaluation committee on request, subject to a mutual NDA.

---

> "Meridian's team delivered a scheduling platform that finally feels like it was built *for* a hospital chain rather than *for* a single hospital that grew. Their grasp of Epic's FHIR surface, and their discipline on the patient-safety edge cases — duplicate bookings, no-show reminders, identity matching — was the strongest we've evaluated. Eleven months from kick-off to all nine hospitals live, with zero P1 incidents in hypercare, is a benchmark we will hold future vendors to."
>
> — **Dr. Anjali Rao**, Chief Medical Information Officer, Sunhaven Hospitals
> March 2025

---

> "We engaged Meridian to fix an integration patchwork that had become a clinical safety risk. They replaced it with a single, observable, properly versioned integration hub — and they stayed disciplined on the boring things (no PHI in logs, CI gates, reconciliation reports) that other vendors talk about and don't actually do. Our result-to-order mismatch rate is now indistinguishable from zero."
>
> — **Vikram Subramanian**, Chief Information Officer, Trillium Care Group
> June 2025

---

> "Meridian shipped our patient portal and our ABDM linkage in the same engagement. The team understood that ABDM is not just a technical integration — it's a consent and a trust conversation with our patients — and the consent ledger they built reflects that. Thirty thousand patients linked their ABHA number in the first ninety days, and we have had zero consent-related complaints to date."
>
> — **Rohan Deshpande**, Chief Digital Officer, Banyan Multispeciality Hospitals
> November 2025

---

**Document control**
Prepared by: Kishore P, Director — Healthcare Delivery, Meridian InfoSystems Pvt. Ltd.
Reviewed by: [pending — Vishakha Arbat for technical claims; Sandeep Srinivasa for inclusion + commercial framing]
Status: Draft 0.9 — for internal review ahead of Day 8 consolidated draft.
