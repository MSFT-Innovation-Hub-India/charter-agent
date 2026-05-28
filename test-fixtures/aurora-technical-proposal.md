# Technical Proposal — Patient Scheduling & Care Coordination Platform
### Companion to SOW MIS-AHN-2026-SOW-001 · Response to RFP AHN-RFP-2026-CARE-009

---

| | |
|---|---|
| **Submitted by** | Meridian InfoSystems Pvt. Ltd. |
| **Registered address** | 14th Floor, Prestige Shantiniketan, Whitefield, Bengaluru — 560048 |
| **Contact** | Vishakha Arbat, Solution Architect — vishakha.arbat@meridianinfosystems.in · +91 98201 34567 |
| **Submitted to** | Ramesh Iyer, Head of Strategic Sourcing, Aurora Healthcare Network |
| **Submission date** | 2026-06-06 |
| **RFP reference** | AHN-RFP-2026-CARE-009 |
| **Document reference** | MIS-AHN-2026-TECH-001 Rev 1.0 |
| **Companion documents** | Commercial Response MIS-AHN-2026-COMM-001 (sealed) · SOW MIS-AHN-2026-SOW-001 |

> This Technical Proposal is the engineering companion to our SOW. The SOW defines **what** we will deliver and on **what commercial terms**; this document defines **how** we will build it. Where the two documents reference the same item, the SOW is authoritative for scope and the Technical Proposal is authoritative for engineering detail.

---

## Table of Contents

1. Engineering Principles
2. Logical Architecture
3. Physical Deployment on Azure (India)
4. Service Decomposition & Domain Model
5. Epic EHR Integration — Detailed Design
6. Partner HL7 / FHIR Gateway Integration — Detailed Design
7. Patient Self-Service Portal & Mobile Web — Technical Design
8. Data Model & Persistence Strategy
9. Data Migration — Technical Approach
10. Security Architecture
11. Identity, Authentication & Authorisation
12. Non-Functional Engineering (Performance, Scalability, HA/DR)
13. Observability & Operations
14. DevOps, CI/CD & Environment Strategy
15. Testing Strategy
16. Coding Standards & Quality Gates
17. Open-Source & Third-Party Components
18. Technical Risks & Mitigations
19. Assumptions
20. Annexure T1 — Component Sizing Worksheet
21. Annexure T2 — Sequence Diagrams (illustrative)

---

## 1. Engineering Principles

The platform is designed against six engineering principles. Every design decision in this document can be traced back to one of these.

| # | Principle | Implication |
|---|---|---|
| P1 | **Standard APIs, never custom plumbing into Epic.** | No Epic Chronicles scripting. No direct Caché database access. Epic integration via FHIR R4 (USCDI v3) APIs and HL7 v2 only, brokered through Epic's Interconnect / EPCS endpoints. |
| P2 | **Loosely coupled services behind a single gateway.** | Independently deployable domain services; API Management is the only ingress. Internal services do not call each other directly across domain boundaries — they go via the gateway or async events. |
| P3 | **Async by default, sync only when the clinician or patient is waiting.** | Appointment slot lookup, check-in, and patient-app interactions are synchronous and snappy. Everything else (lab orders, results ingest, notification fan-out, document generation) is queued via Azure Service Bus with dead-letter recovery. |
| P4 | **Infrastructure is code, never clicks.** | Every environment — dev, QA, UAT, staging, prod, DR — is rebuilt from Terraform. Manual portal edits to production are explicitly forbidden by policy. |
| P5 | **Observability is a build-time concern, not a launch-time bolt-on.** | Distributed tracing, structured logging, and SLO-aligned metrics are wired in Sprint 1 — not retrofitted before UAT. |
| P6 | **Aurora owns the platform at handover.** | No proprietary Meridian frameworks. Stock Spring Boot, stock React, stock Azure services, stock HAPI FHIR. Anything Aurora's IT team cannot Google must be justified in writing. |

---

## 2. Logical Architecture

The platform is composed of six logical layers, each with a single responsibility and a stable contract to its neighbours.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Presentation Layer                                                     │
│  ├── Clinician & Front-Desk Web App (React 18 + TS)                     │
│  └── Patient Self-Service Web + PWA (React 18 + TS)                     │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ HTTPS / OIDC bearer
┌────────────────────────────────▼────────────────────────────────────────┐
│  Edge Layer                                                             │
│  ├── Azure Front Door (WAF, TLS 1.3 termination, geo-routing)           │
│  └── Azure API Management (JWT validation, rate limit, quota, logging)  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ Internal-only mTLS
┌────────────────────────────────▼────────────────────────────────────────┐
│  Application Services Layer (Azure Container Apps, Java 21 + Spring)    │
│  ├── scheduling-service     ├── patient-service                         │
│  ├── orders-service         ├── results-service                         │
│  ├── notification-service   ├── document-service                        │
│  └── admin-service                                                      │
└────────┬────────────────────────────────────────────┬───────────────────┘
         │                                            │
┌────────▼────────────────────┐         ┌─────────────▼──────────────────┐
│  Integration Layer          │         │  Persistence Layer             │
│  ├── epic-fhir-adapter      │         │  ├── Azure PostgreSQL Flex     │
│  ├── partner-hl7-adapter    │         │  ├── Azure Cache for Redis     │
│  └── messaging (Service Bus)│         │  └── Azure Blob (documents)    │
└─────────────────────────────┘         └────────────────────────────────┘
                                                       │
┌──────────────────────────────────────────────────────▼─────────────────┐
│  Observability Layer                                                   │
│  Azure Monitor · Application Insights · Log Analytics · Defender       │
└────────────────────────────────────────────────────────────────────────┘
```

Domain services never reach across to each other's databases. Cross-service reads go through the originating service's API; cross-service writes go through Service Bus events. This is the single most important constraint that keeps the system maintainable past Year 1.

---

## 3. Physical Deployment on Azure (India)

### 3.1 Region Topology

| Tier | Primary | Secondary (DR) |
|---|---|---|
| Compute, data, integration | **Azure India Central** (Pune) | **Azure India South** (Chennai) |
| Static frontends + patient PWA | Azure Front Door (global anycast, India PoPs) | — |
| Backups | Geo-redundant storage between India Central → India South | — |

No patient data leaves Indian Azure regions. This is enforced by Azure Policy assignments at the subscription level (denying resource creation in non-India regions) committed alongside the Terraform. This is a hard DPDP Act 2023 requirement, not a preference.

### 3.2 Subscription & Resource Group Layout

| Subscription | Purpose |
|---|---|
| `aurora-care-nonprod` | dev, QA, UAT — shared lower environments, separated by resource group |
| `aurora-care-prod` | Production + DR + production observability |
| `aurora-care-shared` | Shared services (Key Vault, ACR, DNS zones, Front Door) |

Resource groups follow `rg-ahn-care-{env}-{layer}` (e.g. `rg-ahn-care-prod-data`). Tags: `costCenter`, `environment`, `owner`, `dataClassification` (`PHI` / `non-PHI`), `compliance`.

### 3.3 Azure SKUs (production sizing baseline)

| Service | SKU | Rationale |
|---|---|---|
| Azure Container Apps environment | Workload Profiles (D8s v3, min 2 / max 12 nodes) | Predictable CPU; serverless autoscale on top |
| Azure PostgreSQL Flexible Server | GP_Standard_D8s_v5, 1 TB storage, zone-redundant HA | 1 500 concurrent clinical users + 5 000 concurrent patient sessions per §12.1 |
| Azure Cache for Redis | Premium P2 (13 GB), zone-redundant | Session store + slot-lookup hot-path |
| Azure Service Bus | Premium (2 MU), zone-redundant | Predictable latency, VNet integration, geo-DR pairing |
| API Management | Premium v2, 1 unit + auto-scale, zone-redundant | VNet integration, multi-region capable for DR |
| Azure Front Door | Standard | WAF, caching for static assets |
| Azure Blob Storage | RA-GZRS, immutable tier | Discharge summaries, lab PDFs, imaging reports — 7-year statutory retention |
| Key Vault | Premium (HSM-backed for PHI signing keys) | Customer-managed keys for PostgreSQL TDE; FHIR token signing |

All sizing is **baseline**. Final sizing is locked at architecture sign-off (G2 gate) after load-test results in Week 16. Annexure T1 carries the full sizing worksheet.

---

## 4. Service Decomposition & Domain Model

### 4.1 Bounded Contexts

| Service | Bounded context | Owns (writes) | Reads (via API) |
|---|---|---|---|
| `patient-service` | Patient demographics + consent | `patients`, `patient_consents` | none |
| `scheduling-service` | Appointment lifecycle | `appointments`, `slots`, `provider_calendars` | patient (via patient-service) |
| `orders-service` | Lab / imaging orders | `orders`, `order_routing` | patients, appointments |
| `results-service` | Lab / imaging results | `results`, `result_attachments` | orders |
| `document-service` | Discharge summaries, reports | `documents`, `document_metadata` | blob storage |
| `notification-service` | SMS / email / WhatsApp | `notification_log` | none — pure consumer of events |
| `admin-service` | Internal RBAC, facility config | `users`, `roles`, `facilities`, `audit_log` | identity provider |

Each service has its own PostgreSQL schema in a shared cluster (cost-optimised baseline; can be split into per-service clusters later without code change).

### 4.2 Canonical Domain Events

Published to Service Bus topics; consumed by any service that needs them.

| Event | Publisher | Typical consumers |
|---|---|---|
| `patient.registered` | patient-service | scheduling-service (cache warm), notification-service |
| `appointment.booked` | scheduling-service | notification-service, patient-app (push) |
| `appointment.checked_in` | scheduling-service | orders-service (pre-orders unlock) |
| `order.placed` | orders-service | partner-hl7-adapter, notification-service |
| `result.received` | results-service | notification-service, document-service (attach to encounter) |
| `document.attached` | document-service | patient-app, notification-service |

Schema: CloudEvents 1.0 JSON envelope. Schema registry: a versioned `events/` folder in the platform mono-repo, with consumer-driven contract tests gating breaking changes.

---

## 5. Epic EHR Integration — Detailed Design

### 5.1 Integration Topology

```
Platform Service  ──HTTPS──▶  API Management  ──HTTPS──▶  epic-fhir-adapter  ──FHIR R4──▶  Epic Interconnect  ──▶  Epic Hyperspace / Chronicles
       ▲                                                          │
       │                                                          │
       └──── async callback via Service Bus (Subscriptions) ◀─────┘
```

We do **not** call Epic APIs directly from application services. Every Epic interaction goes through `epic-fhir-adapter` (Spring Boot + HAPI FHIR client), which:

- Handles Epic SMART-on-FHIR backend services authentication (JWT-bearer client assertion).
- Performs payload transformation (canonical JSON ↔ FHIR R4 resources).
- Applies retry policy (3 attempts, exponential back-off 5 / 30 / 120 sec for sync; 5 / 30 / 120 min for async batch).
- Writes failures to a Service Bus dead-letter queue with structured error context (Epic OperationOutcome preserved verbatim).

### 5.2 Per-Flow Specification

| Flow | FHIR resource / API | Direction | Trigger | Latency budget | Failure mode |
|---|---|---|---|---|---|
| Patient demographics sync | `Patient` (read + search) | Epic → Platform | Patient registration in Epic; nightly delta | < 60s end-to-end | DLQ + ops alert; Epic remains source of truth for identifying data |
| Provider schedule sync | `Schedule`, `Slot` | Epic → Platform | Subscription notification; 15-min poll fallback | < 30s | Stale slots flagged; booking falls back to Epic-direct |
| Appointment booking | `Appointment` ($book operation) | Platform → Epic | User confirms slot | < 2s p95 | Slot released; UI shows retry; no double-book risk (idempotency key) |
| Appointment cancel / reschedule | `Appointment` (PUT / $cancel) | Platform → Epic | User action | < 2s p95 | Retried; banner shown until reconciled |
| Encounter / visit creation | `Encounter` | Platform → Epic | Check-in event | < 5s p95 | Queued; clinician sees "syncing" badge |
| Lab / imaging order placement | `ServiceRequest` | Platform → Epic | Provider signs order | < 10s p95 | Retried via DLQ; order marked `pending_ehr_sync` |
| Result delivery | `DiagnosticReport`, `Observation` | Epic → Platform | Subscription notification | < 60s | DLQ + ops alert; patient app shows last-known result |
| Clinical documents | `DocumentReference` + Binary | Bidirectional | Encounter close / signed note | < 30s | Retried; document remains in platform blob |

### 5.3 Idempotency

Every Epic-bound write carries a platform-generated `X-Idempotency-Key` (UUID v7, time-ordered) and a FHIR `Request.ifNoneExist` clause where applicable. The adapter caches keys for 7 days; duplicate POSTs return the original response. This protects against double-booking and duplicate orders under retry storms — the single most common Epic integration bug in our experience.

### 5.4 Consent Propagation

DPDP-aligned patient consent (collected at the patient portal or by the front desk) is materialised as a FHIR `Consent` resource and pushed to Epic so that downstream Epic users see the same consent posture the patient saw on the platform. The `Consent` resource is the legal record; the platform's `patient_consents` table is the operational view.

### 5.5 Epic Environment Strategy

| Environment | Epic system | Use |
|---|---|---|
| Platform dev | Epic POC / sandbox | Adapter development, smoke tests |
| Platform QA | Epic non-prod (NPI) | Integration regression, performance baselining |
| Platform UAT | Epic non-prod (frozen during UAT) | UAT execution |
| Platform prod | Epic production | Live |

We do **not** require a dedicated Epic environment per platform environment. Epic non-prod is shared between Platform QA and UAT but is frozen during UAT (Aurora's Epic team to coordinate) to prevent UAT defect contamination.

---

## 6. Partner HL7 / FHIR Gateway Integration — Detailed Design

### 6.1 Logical Flow

```
orders-service ─event─▶ Service Bus ─trigger─▶ partner-hl7-adapter ──▶ Partner Gateway ──▶ External Lab / Imaging Chain
                                                                              ▲
                                                                              │
                                              partner-hl7-adapter ◀───────────┘  (results)
                                                     │
                                                     ▼
                                              results-service (DiagnosticReport ingest)
```

### 6.2 Standards & Message Mapping

| Business event | HL7 v2 | FHIR equivalent | Transport |
|---|---|---|---|
| Lab / imaging order placement | ORM^O01 (v2.5.1) | `ServiceRequest` (R4) | MLLP over VPN (v2); HTTPS+JWT (FHIR) |
| Order acknowledgement | ORR^O02 / ACK | OperationOutcome | Same channel as above |
| Result delivery (lab) | ORU^R01 | `DiagnosticReport` + `Observation` | Same |
| Result delivery (imaging report) | ORU^R01 with embedded PDF | `DiagnosticReport` + `DocumentReference` (Binary) | Same |
| Demographic update | ADT^A08 | `Patient` PUT | Same |

The adapter implements **both** v2 (for partners still on legacy interfaces) and FHIR R4 (for partners that have modernised). Choice per partner is configuration-only.

### 6.3 Per-Partner Channel Configuration

Each partner channel is provisioned in the integration account with:
- Mutual TLS, AES-256 transport, JWT-bearer auth for FHIR partners.
- Certificate / key rotation calendar (12-month default, reminders at 90 / 30 / 7 days).
- Inbound ACK required; ackTimeout 10 minutes; missing ACK raises an exception.
- Inbound message archival to immutable Blob (7-year retention for clinical audit).

### 6.4 Partner Onboarding Runbook

Per partner (~2 weeks elapsed, ~3 person-days of effort):

| Day | Activity |
|---|---|
| 1–2 | Exchange endpoint URLs and certificates; partner agreement set up in lower environment |
| 3 | Smoke test: ORM → ORR / ServiceRequest → ACK round trip |
| 4–6 | Partner-specific quirks discovery (segment qualifiers, non-standard OBX coding systems, LOINC variants — every partner has them) |
| 7–9 | Full message-set regression (orders, results, reports, demographics) |
| 10 | UAT in lower env with Aurora clinical witness |
| 11–14 | Promotion to QA → UAT → production windows |

### 6.5 Failure Handling

Partner HL7 is the most failure-prone integration in any healthcare platform. Specific guards:

- **Dead-letter queue per partner** — failures are not commingled, so a single misbehaving partner does not poison the queue.
- **Partner-level circuit breaker** — 5 consecutive failures within 5 minutes opens the breaker; orders to that partner queue for manual retry; ops alert raised; clinician sees "partner unavailable" banner and can route to a fallback partner.
- **Result reconciliation job** — daily 02:00 IST job reconciles platform-known results against a fresh inbound from each partner; gaps trigger an exception report routed to the lab liaison.

---

## 7. Patient Self-Service Portal & Mobile Web — Technical Design

### 7.1 Architecture

The patient surface is a **separate React SPA + PWA** deployed to Azure Static Web Apps with its own domain (`my.aurorahealth.in`) and its own API ingress (`patient-api.aurorahealth.in` → API Management → `patient-service` / `scheduling-service` / `results-service`).

Clinician/front-desk app and patient surface share a component library (`@aurora/ui-kit`, internal NPM package) but are otherwise separately deployed and separately scaled. A misbehaving patient app load spike (think: monsoon flu season) cannot impact clinical operations.

### 7.2 Identity (See §11 for full detail)

Azure Entra External ID (CIAM), self-sign-up with **mobile-number-as-username + OTP** (the dominant Indian healthcare pattern), optional email. ABHA (Ayushman Bharat Health Account) linkage is implemented as a configurable identity provider — enabled at go-live, can be turned off per facility.

### 7.3 Capabilities — Technical Notes

| Capability | Implementation note |
|---|---|
| Appointment booking | Real-time slot query against scheduling-service (Redis-cached, TTL 30s); booking is a 2-phase commit (hold slot 90s, confirm) |
| Real-time appointment status | Status pulled via long-polling (5s interval) — keeps p95 below 200ms without requiring web sockets in v1 |
| Report download | Signed Blob SAS URLs, 5-min TTL, scope-locked to the requesting patient |
| Reschedule / cancel | Within cancellation policy window; outside window, returns soft error with explanation |
| Notification preferences | Per-patient, per-event-type (appointment reminder, result available, prescription refill); stored in `patient_consents` (notification is a consent category under DPDP) |
| Family-member access | Linked dependents (children under 18, declared parents) accessible via a single login; explicit toggle in UI |
| ABHA linkage | Optional; uses ABDM Sandbox APIs for v1 (production-ready by GA per ABDM roadmap) |

### 7.4 Explicit Non-Goals (v1)

Tele-consultation video, prescription refill workflows beyond simple request, payment of bills (handled by Aurora's existing payment gateway via deep link), and in-app chat with clinicians are out of scope per SOW §3.5. The patient app's component library and routing are structured to allow these as additive future work without core refactor.

---

## 8. Data Model & Persistence Strategy

### 8.1 Database Topology

Single Azure PostgreSQL Flexible Server, one logical database (`care_platform`), one schema per service:
- `patients`, `scheduling`, `orders`, `results`, `documents`, `notifications`, `admin`.

Cross-schema foreign keys are **forbidden**. Cross-service references are stored as opaque IDs and resolved via API. This is enforced by a CI check on migrations (Flyway).

### 8.2 Core Entity Shapes (illustrative)

```
patients.patient
  id (UUID v7, PK)
  external_ref (text, Epic MRN, unique)
  abha_id (text, nullable, unique)
  mobile_e164 (text, indexed)
  preferred_language (enum: en, hi, mr, kn, ta, te)
  created_at, updated_at (timestamptz, both indexed)
  payload (jsonb, canonical demographics — encrypted column for PII subset)

scheduling.appointment
  id (UUID v7, PK)
  patient_id (UUID, FK within scheduling schema mirror table)
  facility_id (UUID)
  provider_id (UUID)
  slot_start (timestamptz, indexed)
  status (enum: booked, checked_in, in_progress, completed, no_show, cancelled)
  epic_appointment_id (text)
  epic_sync_status (enum: pending, synced, failed)
  ...

orders.order
  id (UUID v7, PK)
  encounter_id (UUID)
  ordering_provider_id (UUID)
  partner_id (UUID, nullable until routing decision)
  order_type (enum: lab, imaging, pathology)
  loinc_codes (text[])
  status (enum: placed, in_progress, resulted, cancelled)
  ...
```

### 8.3 Migrations

Flyway, versioned forward-only migrations. Schema changes go through the CI/CD pipeline like any other code; production migrations run in the deploy step with a guarded rollback window.

### 8.4 Audit Trail

`admin.audit_log` table captures every PHI access and every create/update/delete with: `user_id`, `service`, `entity_type`, `entity_id`, `action`, `before_jsonb`, `after_jsonb`, `at`, `purpose_of_use`. Retention 7 years (Postgres hot for 2 years, archived to immutable Blob for 5 more — meets ICMR EMR record-retention guidance).

### 8.5 Encryption of PHI Columns

PII subset (name, mobile, address, ABHA ID) is encrypted at the column level using `pgcrypto` with keys from Key Vault. This is in addition to PostgreSQL TDE (which protects against disk theft) — column encryption protects against accidental log emission and privileged-user snooping at the DB layer.

---

## 9. Data Migration — Technical Approach

### 9.1 Pipeline

```
Legacy SQL Server  ──Azure Data Factory──▶  Staging Blob (Parquet)  ──ADF Mapping Data Flow──▶  PostgreSQL staging schema  ──validation──▶  PostgreSQL production schemas
```

ADF is selected over custom Spring Batch jobs because (a) it gives Aurora IT a portal-driven view of pipeline runs they can operate themselves post-handover, and (b) its Mapping Data Flows make field-level mapping reviewable by clinical and ops SMEs without a code change.

### 9.2 Reconciliation Methodology

For each entity:
1. **Row count parity** — source count vs target count must match exactly. Mismatch fails the run.
2. **Clinical-critical field parity** — every appointment in the next 90 days, every active order, every unread result; sum of patient counts per facility.
3. **Random sample spot check** — 100 randomly selected records per entity, full field-by-field comparison; Aurora clinical-ops + IT review.
4. **Edge-case checklist** — null handling, multi-byte characters (patient names in Hindi/Marathi/Tamil/Telugu/Kannada), historical date overflows, deprecated facility codes, deceased-patient handling, soft-deleted records.

All reconciliation output is a versioned PDF report committed to the project SharePoint and signed off by Aurora's CMIO and IT.

### 9.3 Cutover Window

| Window | Activity |
|---|---|
| T-7 days | Final dry-run reconciliation; sign-off |
| T-1 day | Freeze legacy writes (read-only mode); patient app shows "scheduled maintenance" banner |
| T-0 (Saturday night) | Final delta migration (~6 hours); reconciliation; smoke tests |
| T+0 (Sunday) | Aurora go/no-go meeting at 09:00; production switch by 12:00 |
| T+1 (Monday) | Hypercare day 1 — full Meridian team on standby; on-site presence at top 3 hospitals |

---

## 10. Security Architecture

### 10.1 Defence-in-Depth Layers

| Layer | Control |
|---|---|
| Edge | Azure Front Door WAF (OWASP Core Rule Set 3.2), DDoS Standard, geo-fence (India primary, plus negotiated allow-list for ABHA SDK and Epic Hosted Services) |
| Ingress | API Management with JWT validation, rate limit (per-subscriber + per-IP + per-patient for portal), payload size cap (1 MB default, 25 MB for document upload), strict Content-Type allow-list |
| Service | mTLS between API Management and Container Apps; Spring Security at service layer; method-level `@PreAuthorize`; purpose-of-use claim required on every PHI access |
| Data in transit | TLS 1.3 minimum on every hop; HL7 partner channels mTLS + AES-256 |
| Data at rest | PostgreSQL TDE with customer-managed keys (Key Vault HSM); column-level encryption for PII subset; Blob SSE with CMK; Redis encryption at rest |
| Secrets | All secrets in Key Vault; services pull via Managed Identity; no secrets in env vars or config files |
| Compute | Container images scanned (Defender for Containers + Trivy in CI); only signed images from Aurora ACR pulled in production |
| Network | All compute in private subnets; egress through Azure Firewall with FQDN allow-list (only Epic, ABDM, partner gateways, Entra endpoints) |

### 10.2 OWASP Top 10 (2021) — How Each Is Addressed

| Item | Mitigation |
|---|---|
| A01 Broken Access Control | Method-level auth; patient multi-tenancy enforced both at API Management (policy) and service (defence in depth); explicit "this patient owns this resource" check on every PHI fetch |
| A02 Cryptographic Failures | TLS 1.3 only; HSM-backed keys; no homegrown crypto; column-level PII encryption |
| A03 Injection | Parameterised SQL (JPA + named parameters); no string-built queries; input validation at edge + service |
| A04 Insecure Design | Threat model per service, reviewed at design gate; abuse cases in test plan |
| A05 Security Misconfiguration | IaC-only deployments; Defender for Cloud baseline; Azure Policy denies public IP, public Blob, etc. |
| A06 Vulnerable Components | Dependabot + Trivy in CI; weekly base-image rebuild |
| A07 Identification & Authentication Failures | Entra ID enforced for staff; MFA enforced for all internal users; patient app OTP + optional FIDO2 in v1.1 |
| A08 Software & Data Integrity Failures | Signed container images; SBOM published per release; ADO pipelines require code review + 2 approvals on prod branches |
| A09 Logging & Monitoring Failures | Centralised Log Analytics; security events forwarded to Aurora's SIEM (Sentinel) at go-live |
| A10 SSRF | Egress allow-list at Azure Firewall; no user-controlled URLs in server-side fetches |

### 10.3 Penetration Test

Third-party penetration test (Meridian-retained, CERT-In-empanelled tester) in Week 23, after UAT defect closure and before cutover. Findings categorised Critical/High/Medium/Low. Critical and High must be remediated before go/no-go. Medium and Low ride into Year 1 backlog with agreed deadlines.

### 10.4 Compliance Posture

- **DPDP Act 2023 (India)**: Personal and health data of Indian data principals stored only in India regions. Consent-first architecture; consent withdrawal triggers a documented data-handling runbook. Right-to-erasure handled via documented runbook against `patients` + audit-log redaction (clinical record retained per ICMR but de-identified).
- **ICMR EMR Standards 2016**: Record-retention windows honoured (7 years for adults, until-age-21 for minors).
- **ABDM (Ayushman Bharat Digital Mission)**: Sandbox-certified at go-live for patient identity linkage; full ABDM Health Information Provider (HIP) certification pursued in Year 1 as an option.
- **ISO 27001:2022**: Meridian's Bengaluru delivery centre is certified. Aurora inherits the controls applied to this engagement.
- **HIPAA**: Not pursued in v1 (no US-based data subjects); architecture is HIPAA-compatible if Aurora expands.

---

## 11. Identity, Authentication & Authorisation

### 11.1 Clinical & Front-Desk Staff

- IdP: Aurora's existing Azure Entra tenant.
- Protocol: OIDC (auth code + PKCE).
- MFA: enforced via Aurora's Conditional Access policy.
- Group-to-role mapping: configured by Aurora IT, consumed by `admin-service` via Microsoft Graph at login time. Roles: physician, nurse, front-desk, lab-tech, radiologist, MRD, facility-admin, system-admin.
- Smartcard / FIDO2 supported for clinicians.
- Session: 4-hour sliding, hard 8-hour expiry (shorter than Northwind-equivalent because PHI exposure).

### 11.2 Patients

- IdP: Azure Entra External ID (CIAM, separate tenant).
- Sign-up: self-service with mobile + OTP.
- MFA: OTP mandatory on every login (no remember-this-device on first release; behaviour reviewed in v1.1).
- Account binding: each patient user is bound to one MRN; linked dependents added explicitly with consent.
- ABHA linkage: optional, additive identity provider.
- Session: 1-hour sliding, hard 4-hour expiry.

### 11.3 Service-to-Service

- Managed Identity for every Container App.
- API Management validates inbound JWT from Entra; subscription key required for system-to-system callers.
- Service-to-service calls cross API Management (no direct mesh in v1; service mesh deferred unless complexity warrants).

### 11.4 Authorisation Model

Hybrid: coarse-grained role check at API Management policy (fast reject for unauthorised paths) + fine-grained, resource-level check in service layer (e.g. "this clinician is on this patient's care team and has a valid purpose-of-use for this encounter"). Both checks are required; neither is sufficient alone. Break-glass access is supported, logged, and reviewed weekly by Aurora's compliance team.

---

## 12. Non-Functional Engineering

### 12.1 Performance Targets & Headroom

| Metric | Target | Engineering headroom plan |
|---|---|---|
| API response p95 | < 800ms | Internal target 400ms; alert at 600ms |
| Slot lookup p95 | < 300ms | Redis-cached; alert at 200ms |
| Patient app page load | < 2s @ 10 Mbps; < 4s @ 3G | Internal target 1.2s / 2.5s; CDN-cached shell, lazy-loaded routes |
| Concurrent clinical users | 1 500 | Sized for 3 000, autoscale ceiling 6 000 |
| Concurrent patient sessions | 5 000 | Sized for 10 000, autoscale ceiling 20 000 |
| Appointment-booking peak | 200 bookings/sec (morning rush) | Sized for 500/sec |

### 12.2 Scalability Strategy

- **Stateless services** behind Azure Container Apps autoscale (KEDA rules: CPU > 70% for 60s, or HTTP queue length > 50, or Service Bus depth > threshold).
- **Database**: vertical scale (D8s → D16s → D32s) is the v1 strategy; read replicas added if patient-app read load demands it (no application change required).
- **Redis**: cluster-mode-capable SKU chosen for v1 to enable horizontal scale without redeployment.
- **Service Bus**: Premium messaging units scale 2 → 8 with no code change.

### 12.3 High Availability

| Component | HA strategy |
|---|---|
| Container Apps | Multi-zone, min 2 replicas per service, anti-affinity across zones |
| PostgreSQL | Zone-redundant HA (synchronous standby in second AZ; automatic failover) |
| Redis | Zone-redundant Premium |
| Service Bus | Premium, zone-redundant, geo-DR pair configured |
| Storage | RA-GZRS (read access geo-redundant) |
| API Management | Premium, zone-redundant; multi-region capable for DR (passive in India South) |

### 12.4 Disaster Recovery

- **RTO 2 hours, RPO 15 minutes** (tighter than a typical logistics platform — patient care cannot wait).
- **Strategy**: warm-standby in India South. Infrastructure deployed via the same Terraform; database replication via PostgreSQL geo-restore with 15-minute backup frequency.
- **DR drill**: scheduled in Week 26 (hypercare); annual thereafter, planned as part of Year 1 support. Aurora's CMIO must co-sign the DR test plan.

### 12.5 Maintenance Windows

- Scheduled: every second Sunday, 02:00–05:00 IST (lowest-traffic window per Aurora's historical data). Five business days' notice via the platform status page, email, SMS to patients with appointments in the window.
- Emergency: zero-downtime patching for everything except database major-version upgrades.

---

## 13. Observability & Operations

### 13.1 The Three Pillars

| Pillar | Tooling | What's instrumented |
|---|---|---|
| **Logs** | Log Analytics workspace, 90-day hot retention, 7-year archive (regulatory) | Structured JSON, correlation ID per request, PHI fields redacted at source |
| **Metrics** | Azure Monitor + Application Insights | RED metrics (Rate, Errors, Duration) per endpoint; USE metrics (Utilisation, Saturation, Errors) per resource |
| **Traces** | Application Insights distributed tracing | W3C Trace Context propagated through API Management → services → Epic adapter → partner adapters |

### 13.2 SLOs

| SLO | Target | Error budget |
|---|---|---|
| Clinician app availability | 99.9% monthly | ~43 min/month |
| Patient app availability | 99.5% monthly | ~3.6 hr/month |
| Slot lookup p95 < 300ms | 99% of 1-min windows | — |
| Appointment booking end-to-end | 99% < 5s | 7.2 hr/month |
| Result delivery (partner → patient app) | 99% < 5 min | 7.2 hr/month |

Error budget burn-rate alerts (fast-burn 2% / 1h, slow-burn 5% / 6h) routed to the on-call rota.

### 13.3 Dashboards

Three production dashboards delivered at go-live:
- **Platform health** — RED metrics, SLO burn, dependency health (Epic, ABDM, partners, IdP).
- **Clinical ops** — appointments today by facility, check-in queue depth, orders pending result, exception count.
- **Patient experience** — patient app active sessions, slot-lookup latency, OTP success rate, support ticket correlation view.

Dashboards committed as JSON in the repo. No portal-clicked dashboards.

### 13.4 Alerting

| Severity | Routing | Examples |
|---|---|---|
| Sev 1 | PagerDuty → primary on-call → escalate at 15 min | Service down, DB primary unhealthy, partner breaker open for > 5 min, Epic adapter sustained errors |
| Sev 2 | PagerDuty → primary on-call, no escalation | SLO fast-burn, integration DLQ depth > 50, latency p95 over budget for 10 min |
| Sev 3 | Email + dashboard | Slow-burn, certificate expiring < 30 days, low disk |
| Sev 4 | Dashboard only | Informational |

### 13.5 Runbooks

Every alert links to a runbook in the platform wiki. Runbooks are written as part of the build, not retrofitted. The runbook index is a deliverable at go-live.

---

## 14. DevOps, CI/CD & Environment Strategy

### 14.1 Environments

| Env | Subscription | Refresh policy | Data |
|---|---|---|---|
| dev | nonprod | Continuous deploy from `main` | Synthetic |
| QA | nonprod | Promoted from dev after CI green | Synthetic + de-identified legacy sample |
| UAT | nonprod | Promoted from QA at sprint boundary | Full de-identified legacy migration dry-run dataset |
| staging | prod | Promoted from UAT after sign-off; production-parity infra | Production-restore snapshot, de-identified |
| prod | prod | Promoted from staging with manual approval | Live |
| DR | prod (India South) | Continuously replicated | Live (replica) |

### 14.2 Pipeline

Azure DevOps Pipelines, YAML-defined, ring-gated:

```
PR build  →  unit + integration tests + SAST + dependency scan + container scan
   ↓ merge to main
dev deploy  (auto)
   ↓ CI green
QA deploy  (auto)
   ↓ sprint demo sign-off
UAT deploy  (manual approval — Aurora PO + CMIO delegate)
   ↓ UAT acceptance
staging deploy  (manual approval — Meridian Delivery Lead)
   ↓ smoke + pen-test results
prod deploy  (manual approval — Aurora PM + Meridian Delivery Lead)
```

Deploy strategy: blue-green for application services; rolling for stateless support services; in-place with migration guard for the database.

### 14.3 Infrastructure as Code

- **Terraform 1.7+**, modules-per-service.
- State in Azure Storage with per-environment containers and state-locking.
- `terraform plan` posted as a PR comment on every IaC change; merge requires explicit Aurora IT approval for prod-affecting changes.

### 14.4 Branching

Trunk-based development with short-lived feature branches (max 5 days). Feature flags for incomplete work in `main`.

### 14.5 Artefact Management

- Container images → Aurora ACR (private, geo-replicated India Central + South).
- Maven artefacts → ADO Artifacts feed.
- NPM artefacts → ADO Artifacts feed.
- Images signed with Notation; only signed images deployable to production (enforced by Azure Policy).

---

## 15. Testing Strategy

### 15.1 Test Pyramid

| Layer | Coverage target | Owner |
|---|---|---|
| Unit | ≥ 80% line coverage per service | Devs |
| Integration | All inter-service contracts + all Epic flows + all partner HL7 round-trips | Devs + QA |
| Contract (CDC) | Every event producer ↔ consumer pair | Devs |
| End-to-end (UI) | Top 40 user journeys (clinical + patient) automated with Playwright | QA |
| Performance | Sustained 1× target load + 5-min 2× spike (morning rush simulation), before UAT | QA + DevOps |
| Security | SAST per PR; DAST in QA; third-party pen test pre-go-live | QA + external |
| Clinical safety | Scripted scenarios reviewed by Aurora CMIO: wrong-patient prevention, allergy override audit, duplicate-order guard | QA + Aurora CMIO |
| Chaos (light) | Quarterly in Year 1: AZ failover, Epic outage simulation, partner outage, DB failover | DevOps |

### 15.2 Test Data Strategy

- Synthetic data generator (custom Spring CLI, committed to repo) for dev/QA — produces realistic Indian-name distributions, valid ABHA-format IDs, valid mobile numbers (test ranges).
- De-identified production-shape dataset for UAT and staging, refreshed quarterly.
- All direct identifiers irreversibly hashed; quasi-identifiers generalised; clinical fields preserved for realism.

### 15.3 UAT Support

- Pre-written test scripts (one per FSD section); clinical journeys reviewed by Aurora CMIO before kick-off.
- Defect-triage call daily during UAT (09:30 IST, 30 min).
- Defect SLA during UAT: P1 fix in 1 business day, P2 in 3, P3 in 5.

---

## 16. Coding Standards & Quality Gates

### 16.1 Languages & Style

- Java 21, Google Java Style, enforced by Spotless in CI.
- TypeScript 5, ESLint + Prettier (Airbnb-derived ruleset), strict mode on.
- SQL: snake_case, lowercase keywords, formatted by sqlfluff.

### 16.2 Mandatory CI Gates (PR cannot merge unless green)

| Gate | Tool |
|---|---|
| Format | Spotless / Prettier / sqlfluff |
| Static analysis | SpotBugs + SonarQube (Java); ESLint (TS); Semgrep (cross-cutting) |
| Unit + integration tests | Maven Surefire / Vitest |
| Coverage threshold | JaCoCo (80%); coverage CANNOT decrease vs main |
| Dependency vulnerabilities | OWASP Dependency-Check + Dependabot |
| Container scan | Trivy (high+critical block) + Defender for Containers |
| IaC scan | Checkov (Terraform) |
| Secrets scan | Gitleaks |
| PHI-in-logs scan | Custom Semgrep ruleset against known PHI patterns (mobile, ABHA, MRN) |
| Code review | 2 approvers; at least one with backend or frontend specialisation per touched layer |

### 16.3 Documentation as Code

- ADRs (Architecture Decision Records) committed to repo, numbered, immutable.
- Service READMEs follow a template (purpose, owns, depends on, env vars, runbooks).
- OpenAPI spec generated from code; published to API Management developer portal.
- FHIR conformance profiles published as CapabilityStatement resources.

---

## 17. Open-Source & Third-Party Components

### 17.1 Significant OSS Dependencies

| Component | Licence | Use |
|---|---|---|
| Spring Boot 3.x | Apache 2.0 | Backend framework |
| Spring Security | Apache 2.0 | AuthN/Z |
| HAPI FHIR | Apache 2.0 | FHIR R4 client and validation |
| HL7 v2 parser (HAPI v2) | Apache 2.0 | Partner HL7 v2 messages |
| React 18 | MIT | Frontend |
| Tailwind CSS | MIT | Styling |
| TanStack Query | MIT | Frontend data fetching |
| Flyway Community | Apache 2.0 | DB migrations |
| Testcontainers | MIT | Integration tests |
| Playwright | Apache 2.0 | E2E tests |

Full SBOM published per release in CycloneDX format. No copyleft licences (GPL/LGPL/AGPL) in shipped code; this is enforced in CI by a licence scanner.

### 17.2 Third-Party Commercial Components

None in the proposed v1 build. The platform is delivered on stock Azure + OSS so that Aurora has no surprise per-seat or per-transaction licences to absorb at handover. ABDM SDK use is free under ABDM terms; Epic API use is governed by Aurora's existing Epic agreement.

---

## 18. Technical Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| TR1 | Epic FHIR API version drift during build | Medium | Medium | Pin Epic FHIR version; subscribe to Epic UserWeb release notes; contract tests against a HAPI FHIR mock break the build on shape change. |
| TR2 | Partner returns non-standard HL7 variant (e.g. local OBX coding) | High | Medium | Per-partner transformation maps in the adapter; "unknown segment" fallback writes to inspection queue rather than dropping the message. |
| TR3 | PostgreSQL row-level lock contention on appointment table during morning rush | Medium | High | Slot reservation via Redis advisory lock (90s TTL), then async DB write; lock-monitoring dashboard in place from Sprint 1. |
| TR4 | Redis cache stampede during patient app traffic spikes | Medium | Medium | Cache-aside with single-flight and stale-while-revalidate. |
| TR5 | Long-running ADF migration locks legacy DB | Medium | High | Snapshot extract pattern (read from snapshot view, not live table); migration window scheduled in legacy maintenance window. |
| TR6 | Partner certificate expiry in production | Medium | High | Calendar-managed rotation; 90/30/7-day alerts; pre-shared backup cert with each partner. |
| TR7 | Wrong-patient PHI exposure via authorisation bug | Low | Critical | Authorisation tested as a first-class concern: dedicated test suite, abuse-case scenarios in UAT, mandatory code-review check on every controller change. |
| TR8 | Frontend bundle size growth → patient app regress on 3G | Medium | Medium | Bundle-size budget enforced in CI; route-level code splitting from day 1; 3G performance budget in synthetic monitoring. |
| TR9 | Clinical safety — duplicate or wrong-order placement | Low | Critical | Idempotency at the adapter; UI guards (confirm-before-sign); audit trail; review of all signed orders by clinical safety officer for first 4 weeks post go-live. |
| TR10 | ABDM API instability during early integration | Medium | Low | ABHA linkage is optional and feature-flagged; ABDM failures degrade gracefully without blocking platform use. |
| TR11 | Vendor lock-in concern at handover | Low | Medium | Stock Spring/React/Azure; no Meridian-proprietary frameworks; runbooks written for "a competent Java/Azure engineer" not "an engineer who knows Meridian's way". |

(Commercial and programmatic risks are in SOW §9; this register covers engineering-only.)

---

## 19. Assumptions

These assumptions underpin the design and sizing. If any is invalidated, an impact note will be raised per SOW §4.3.

| # | Assumption |
|---|---|
| TA1 | Aurora's Epic release is on a version that supports FHIR R4 (USCDI v3) APIs and SMART-on-FHIR backend services. Older versions require a change request to design around HL7 v2 fallback. |
| TA2 | Aurora's partner-gateway HL7 message-set scope is bounded to lab orders/results and imaging reports, with up to five partner integrations in v1. Additional partners are change requests after onboarding the fifth. |
| TA3 | Aurora's Entra tenant permits Meridian-registered app registrations and group claims in tokens. |
| TA4 | Peak daily appointment volume is in the order of 25 000 / day across all facilities, with morning-rush burst of ~200 bookings/sec; sizing is built around this. If true peak is materially higher, sizing is revised at G2. |
| TA5 | Clinical document storage growth is bounded at ~200 GB / year (discharge summaries + lab PDFs + imaging reports). Lifecycle tiering to cool tier after 180 days, archive after 2 years; total retention 7 years per ICMR. |
| TA6 | Aurora has an Azure Enterprise Agreement; Meridian deploys under Aurora's tenant and subscriptions, not Meridian's. |
| TA7 | Network connectivity between Azure India Central and Epic (whether Epic is Aurora-hosted or Epic-hosted) provides < 50ms round trip. If higher, integration p95 budgets need re-baselining. |
| TA8 | Partner HL7 gateways are reachable from Azure India Central over Aurora-provided VPN or ExpressRoute; if neither exists, Aurora provisions it. |
| TA9 | The patient user base at go-live is ≤ 500 000 registered patients and ≤ 5 000 concurrent sessions at peak. Beyond this, sizing review required. |
| TA10 | All API consumers (internal services + future external partners) accept JSON. No SOAP, no XML-RPC. HL7 v2 over MLLP is the only non-JSON channel and is isolated in the partner adapter. |
| TA11 | ABDM (ABHA) integration is sandbox-grade at go-live; full HIP/HIU certification is an optional Year 1 add-on. |
| TA12 | Clinical workflows in scope match Aurora's current Epic configuration; redesign of clinical workflows is out of scope and a change request. |

---

## Annexure T1 — Component Sizing Worksheet

*Provided as a separate working spreadsheet at architecture sign-off (G2 gate). Captures, per service: target throughput, p95 latency budget, vCPU / memory allocation, replica count (min/max), autoscale rules, and projected monthly Azure cost. Reviewed jointly with Aurora IT before lock-in.*

---

## Annexure T2 — Sequence Diagrams (illustrative)

### T2.1 — Appointment Booking (Patient → Platform → Epic)

```
Patient App     API Mgmt    scheduling-service     Redis        epic-fhir-adapter      Epic Interconnect      Service Bus      notification-service
     │              │              │                  │                  │                       │                  │                   │
     │ GET /slots   │              │                  │                  │                       │                  │                   │
     │─────────────▶│              │                  │                  │                       │                  │                   │
     │              │ GET /slots   │                  │                  │                       │                  │                   │
     │              │─────────────▶│                  │                  │                       │                  │                   │
     │              │              │ GET cached slots │                  │                       │                  │                   │
     │              │              │─────────────────▶│                  │                       │                  │                   │
     │              │              │   slots          │                  │                       │                  │                   │
     │              │              │◀─────────────────│                  │                       │                  │                   │
     │              │  slots       │                  │                  │                       │                  │                   │
     │              │◀─────────────│                  │                  │                       │                  │                   │
     │   slots      │              │                  │                  │                       │                  │                   │
     │◀─────────────│              │                  │                  │                       │                  │                   │
     │ POST /hold   │              │                  │                  │                       │                  │                   │
     │─────────────▶│─────────────▶│ acquire lock 90s │                  │                       │                  │                   │
     │              │              │─────────────────▶│                  │                       │                  │                   │
     │ 200 hold-id  │              │                  │                  │                       │                  │                   │
     │◀─────────────│◀─────────────│                  │                  │                       │                  │                   │
     │ POST /confirm│              │                  │                  │                       │                  │                   │
     │─────────────▶│─────────────▶│ $book Appointment│                  │                       │                  │                   │
     │              │              │─────────────────────────────────────▶│                       │                  │                   │
     │              │              │                  │                  │ POST Appointment       │                  │                   │
     │              │              │                  │                  │──────────────────────▶│                  │                   │
     │              │              │                  │                  │   201 Appointment      │                  │                   │
     │              │              │                  │                  │◀──────────────────────│                  │                   │
     │              │              │ persist + emit appointment.booked   │                       │                  │                   │
     │              │              │─────────────────────────────────────────────────────────────▶│                  │                   │
     │ 201 booked   │              │                  │                  │                       │                  │ appointment.booked│
     │◀─────────────│◀─────────────│                  │                  │                       │                  │──────────────────▶│
     │              │              │                  │                  │                       │                  │                   │ SMS + push
```

### T2.2 — Lab Order Placement & Result Delivery

```
Clinician App   orders-service     Service Bus    partner-hl7-adapter    Partner Gateway    External Lab     results-service     patient-app
     │              │                   │                  │                   │                  │                  │                  │
     │ sign order   │                   │                  │                   │                  │                  │                  │
     │─────────────▶│                   │                  │                   │                  │                  │                  │
     │              │ persist + emit order.placed          │                   │                  │                  │                  │
     │              │──────────────────▶│                  │                   │                  │                  │                  │
     │              │                   │ consume          │                   │                  │                  │                  │
     │              │                   │─────────────────▶│                   │                  │                  │                  │
     │              │                   │                  │ ORM^O01 (MLLP)    │                  │                  │                  │
     │              │                   │                  │──────────────────▶│                  │                  │                  │
     │              │                   │                  │                   │   forward        │                  │                  │
     │              │                   │                  │                   │─────────────────▶│                  │                  │
     │              │                   │                  │                   │  ORR^O02 ack     │                  │                  │
     │              │                   │                  │                   │◀─────────────────│                  │                  │
     │              │                   │                  │   ack received    │                  │                  │                  │
     │ 201 placed   │                   │                  │                   │                  │                  │                  │
     │◀─────────────│                   │                  │                   │                  │                  │                  │
     │                                  │                  │                   │                  │                  │                  │
     │                                  │                  │ ORU^R01 (result) │                  │                  │                  │
     │                                  │                  │◀──────────────────│◀─────────────────│                  │                  │
     │                                  │ emit result.received                 │                  │                  │                  │
     │                                  │◀─────────────────│                   │                  │                  │                  │
     │                                  │                                                          │ consume         │                  │
     │                                  │─────────────────────────────────────────────────────────▶│                 │                  │
     │                                  │                                                          │ persist + push  │                  │
     │                                  │                                                          │────────────────▶│ "Result ready"  │
     │                                  │                                                          │                 │────────────────▶│
```

---

*End of Technical Proposal — MIS-AHN-2026-TECH-001 Rev 1.0*

*This document is submitted in confidence as the engineering companion to SOW MIS-AHN-2026-SOW-001 in response to RFP AHN-RFP-2026-CARE-009. All contents are proprietary to Meridian InfoSystems Pvt. Ltd. and Aurora Healthcare Network Pvt. Ltd.*
