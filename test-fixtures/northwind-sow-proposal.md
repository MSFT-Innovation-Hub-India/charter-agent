# Statement of Work — Custom Logistics & Order-Tracking Platform
### Response to RFP NWT-RFP-2026-LOG-014

---

| | |
|---|---|
| **Submitted by** | Meridian InfoSystems Pvt. Ltd. |
| **Registered address** | 14th Floor, Prestige Shantiniketan, Whitefield, Bengaluru — 560048 |
| **CIN** | U72200KA2011PTC058832 |
| **Contact** | Arjun Mehta, VP Delivery — arjun.mehta@meridianinfosystems.in · +91 98450 12345 |
| **Submitted to** | Eleanor Vance, Head of Procurement, Northwind Trading Corp |
| **Submission date** | 2026-06-06 |
| **RFP reference** | NWT-RFP-2026-LOG-014 |
| **Document reference** | MIS-NWT-2026-SOW-001 Rev 1.0 |
| **Validity** | This proposal is valid for 60 days from submission date |

> **Pricing is submitted as a separate sealed PDF** per §9 of the RFP, reference MIS-NWT-2026-COMM-001.

---

## Table of Contents

1. Executive Summary
2. Our Understanding of Your Requirements
3. Solution Architecture
4. Scope of Work
5. Delivery Approach & Methodology
6. Project Plan & Milestones
7. Team Composition & Governance
8. Roles & Responsibilities
9. Risk Register
10. Case Studies & Credentials
11. Commercial Summary
12. Annexure A — Assumptions & Dependencies
13. Annexure B — Technology Stack Detail
14. Annexure C — Support SLA Framework

---

## 1. Executive Summary

Northwind Trading Corp is at a critical inflection point. The existing logistics system — built over a decade — no longer keeps pace with the operational complexity of a business spanning India, Singapore, and the UAE. Its inability to integrate cleanly with SAP S/4HANA, limited carrier EDI connectivity, and lack of real-time visibility are active constraints on growth. The board has made replacement a current-year priority, with a fixed go-live date of end of Q1 2027.

Meridian InfoSystems proposes a **cloud-native, purpose-built Logistics & Order-Tracking Platform** hosted on Microsoft Azure (India region) — fully integrated with Northwind's SAP S/4HANA ERP and carrier EDI gateway, with a self-service customer portal for enterprise accounts. The platform is designed to be operated and extended by Northwind's own IT team over time, not to create a permanent dependency on us.

**Why Meridian:**

- We have delivered three SAP-integrated logistics platforms in the last four years, two of which involved carrier EDI connectivity on comparable complexity.
- Our Bengaluru delivery centre has a dedicated SAP BTP integration practice with certified architects.
- We have never missed a fixed go-live date on a contract where client dependencies were met on schedule — a claim we back with a contractual SLA and a penalty framework proposed in the commercial response.
- We propose a January 31, 2027 go-live — six weeks inside the fixed Q1 deadline — giving Northwind a meaningful buffer against the inevitable late-stage surprises of any ERP-adjacent programme.

---

## 2. Our Understanding of Your Requirements

### 2.1 The Core Problem

Northwind's current system was built to serve a simpler business. Three problems now compound each other:

1. **ERP disconnect.** Orders, inventory positions, and finance data live in SAP S/4HANA. The logistics system knows none of it. Staff re-key data between systems; reconciliation is manual and error-prone.
2. **Carrier blindness.** Without EDI connectivity, shipment bookings are manual, status updates are delayed, and proof-of-delivery collection is inconsistent. Disputes with carriers take longer than they should.
3. **Customer visibility gap.** Enterprise customers cannot self-serve. Every "where is my order?" call costs Northwind staff time and erodes the relationship.

### 2.2 What "Done" Looks Like

At go-live, Northwind's operations will be able to:

- Create or receive an order in SAP S/4HANA and see it appear in the logistics platform automatically, with inventory and finance context attached.
- Book a shipment with a carrier through the EDI gateway from within the platform, and receive status updates and POD back without manual intervention.
- Offer enterprise customers a branded self-service portal where they can track their orders in real time and download invoices, BOLs, and PODs.
- Migrate all operational history from the legacy system with a signed reconciliation report confirming zero data loss.

### 2.3 Constraints We Are Designing Around

| Constraint | Our response |
|---|---|
| Fixed go-live: end of Q1 2027 | We target January 31, 2027 — six weeks early. Scope is fixed; timeline drives trade-off decisions. |
| Data residency in India | All Azure services deployed to India Central (primary) and India South (DR). No data leaves Indian Azure regions. |
| SAP S/4HANA via standard APIs | Integration uses SAP Business Accelerator Hub APIs and iFlows via BTP Integration Suite — no direct DB access, no ABAP custom code. |
| Client IT team to take over L1 support at Year 1 end | Knowledge-transfer programme is a first-class deliverable, not an afterthought. |

---

## 3. Solution Architecture

### 3.1 Architecture Overview

The platform is built as a set of independently deployable services behind a single API gateway, with a React-based web frontend and a dedicated integration tier for SAP and EDI. All components run on Azure (India Central region) with automated failover to India South.

```
┌─────────────────────────────────────────────────────────────────┐
│                        NORTHWIND USERS                          │
│         Operations · Customer Service · IT Admins               │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼──────────────────────────────────────┐
│                    Azure Front Door (WAF)                        │
└──┬─────────────────────────────────────────────────┬────────────┘
   │                                                 │
┌──▼──────────────────────┐             ┌────────────▼────────────┐
│  Internal Web App       │             │  Customer Self-Service   │
│  (React + TypeScript)   │             │  Portal (React)          │
│  Azure Static Web Apps  │             │  Azure Static Web Apps   │
└──┬──────────────────────┘             └────────────┬────────────┘
   │                                                 │
┌──▼─────────────────────────────────────────────────▼───────────┐
│                  Azure API Management (Gateway)                  │
│                  JWT validation · rate limiting · logging        │
└──┬──────────────┬──────────────┬──────────────┬────────────────┘
   │              │              │              │
┌──▼──────┐  ┌───▼──────┐  ┌───▼──────┐  ┌───▼──────────────────┐
│  Order  │  │Shipment  │  │Customer  │  │  Notification Service│
│  Svc    │  │Tracking  │  │Portal    │  │  (Azure Service Bus)  │
│         │  │Svc       │  │Svc       │  │                       │
└──┬──────┘  └───┬──────┘  └───┬──────┘  └───────────────────────┘
   │             │             │
┌──▼─────────────▼─────────────▼────────────────────────────────┐
│              Azure PostgreSQL Flexible Server                    │
│              (India Central — zone-redundant)                    │
│              + Azure Cache for Redis (session / hot data)        │
└───────────────────────────────────────────────────────────────-┘
   │                              │
┌──▼──────────────────────┐  ┌───▼──────────────────────────────┐
│  SAP Integration Tier   │  │  EDI Integration Tier            │
│  Azure Logic Apps       │  │  Azure Logic Apps                │
│  + BTP Integration Suite│  │  (AS2 · X12 / EDIFACT)           │
└──┬──────────────────────┘  └───┬──────────────────────────────┘
   │                             │
   ▼                             ▼
SAP S/4HANA                 Carrier EDI Gateway
(Order · Inventory          (Shipment booking
 · Finance modules)          · Status · POD)
```

### 3.2 Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Frontend (internal) | React 18, TypeScript, Tailwind CSS | Component reuse with customer portal; strong typing reduces integration-layer bugs |
| Frontend (customer portal) | React 18, TypeScript | Shared component library with internal app |
| Backend services | Java 21, Spring Boot 3, REST | Enterprise-proven, strong SAP ecosystem tooling |
| API gateway | Azure API Management | Centralised auth, rate-limiting, developer portal for future API exposure |
| Primary database | Azure PostgreSQL Flexible Server (v16) | Relational integrity for order/shipment data; zone-redundant HA built in |
| Cache | Azure Cache for Redis | Session data and tracking hot-path; sub-5ms response for portal lookups |
| Document storage | Azure Blob Storage (India Central) | BOL, POD, invoice storage; immutable blob tiers for compliance |
| SAP integration | SAP BTP Integration Suite (iFlow) + Azure Logic Apps | Standard SAP APIs via BTP; Logic Apps orchestrates retry/error flows |
| EDI integration | Azure Logic Apps (EDI X12/EDIFACT) + AS2 connector | Native Azure EDI decode/encode; no third-party middleware licence |
| Identity (internal) | Azure Entra ID (SAML 2.0 / OIDC) | SSO with Northwind's existing Entra tenant |
| Identity (portal) | Azure Entra External ID (B2B) | Branded login for enterprise customers; self-service invitation flow |
| Messaging | Azure Service Bus | Decoupled async events between services; dead-letter queues for EDI failures |
| CI/CD | Azure DevOps Pipelines | IaC-first deployments; every commit to a ring-gated pipeline |
| Infrastructure as code | Terraform | Reproducible environments; no manual portal clicks in production |
| Monitoring | Azure Monitor, Application Insights, Log Analytics | Unified alerting, distributed tracing, 90-day log retention |
| Hosting (compute) | Azure Container Apps | Serverless-scale containers; no cluster management overhead |

All services are deployed to **Azure India Central** (primary) with automated geo-redundant backup to **Azure India South**. No data transits outside Indian Azure regions.

### 3.3 SAP S/4HANA Integration

**Approach:** We use SAP Business Accelerator Hub standard APIs — no custom ABAP, no direct database access. Integration flows are built on SAP BTP Integration Suite (iFlow) and surfaced to the platform via Azure API Management.

**Integration points:**

| Flow | Direction | SAP API / IDoc | Trigger |
|---|---|---|---|
| Sales order ingestion | SAP → Platform | Sales Order (A2X) API | SAP order creation event via BTP |
| Inventory availability check | Platform → SAP | Product Availability (A2X) API | Order allocation step |
| Outbound delivery confirmation | Platform → SAP | Outbound Delivery API | Shipment booking confirmed |
| Goods issue posting | Platform → SAP | Goods Movement API | Carrier confirms pickup |
| Proof of delivery sync | Platform → SAP | Delivery Document API | POD received from carrier via EDI |
| Finance — invoice status | SAP → Platform | Customer Invoice (A2X) API | Invoice posted in SAP |

**Error handling:** Every integration flow has a dead-letter queue in Azure Service Bus. Failed messages are retried with exponential back-off (3 attempts, 5 / 30 / 120 minute intervals). After three failures the message is quarantined and an alert is raised to the operations team. No silent failures.

**SAP environment dependency:** We require read/write access to Northwind's SAP S/4HANA development and QA sandboxes by end of Week 2 post sign-off. Production SAP access is required no later than Week 17 for integration regression testing. These are listed as client dependencies in Annexure A.

### 3.4 Carrier EDI Integration

**Supported standards:** ANSI X12 and EDIFACT. The specific message sets we will implement:

| Message | X12 equivalent | Purpose |
|---|---|---|
| Shipment booking request | 204 | Book a shipment with carrier |
| Shipment booking acknowledgement | 990 | Carrier confirms or rejects booking |
| Shipment status update | 214 | In-transit milestone events |
| Advance ship notice | 856 | Carrier confirms dispatch with details |
| Proof of delivery | 214 (final) / IFTSTA | Delivery confirmation with signature/image |

**Transport:** AS2 over HTTPS. Meridian will configure and test AS2 channels with up to **five carriers** during the project. Additional carriers post go-live are handled as change requests.

**Carrier onboarding dependency:** Northwind must provide contact details for each carrier's EDI team by end of Week 3. EDI testing with each carrier requires a minimum two-week lead time from first contact. Carriers that do not respond within the project window will be scoped as post-go-live additions.

### 3.5 Customer Self-Service Portal

The portal is a separately deployed React application, authenticated via Azure Entra External ID (B2B). Enterprise customers are invited by Northwind's customer-service team; they set their own password and do not require an Entra licence.

**Capabilities at go-live:**

- Real-time order status and shipment tracking (carrier milestone events surfaced as a timeline)
- Document download: commercial invoice, bill of lading, packing list, proof of delivery
- Order history with date-range filter and export to CSV / Excel
- Notification preferences: email alerts on shipment dispatch, in-transit exceptions, and delivery
- Multi-user access per customer account, with view-only and admin roles

**What the portal does not do at go-live:** order placement, returns initiation, or direct messaging with Northwind staff. These are out of scope and can be added as future skills if required.

### 3.6 Data Migration

**Approach:** Extract-transform-load (ETL) pipeline built on Azure Data Factory, with a parallel-run reconciliation report before cutover.

| Phase | Activity |
|---|---|
| Discovery (Week 2) | Northwind IT provides legacy schema, data dictionary, and row-count estimates |
| Mapping (Week 4) | Meridian produces field-level mapping document; Northwind signs off |
| ETL build (Week 8) | Pipeline built; first full extract from legacy system to staging environment |
| Dry run 1 (Week 16) | Full migration to staging; reconciliation report produced and reviewed |
| Dry run 2 (Week 20, UAT) | Migration repeated after UAT fixes; reconciliation report v2 |
| Cutover migration (Week 26) | Final migration run; Northwind IT validates reconciliation report and signs off before go-live switch |

**Reconciliation report** covers: record counts by entity type, financial totals (open order values, outstanding deliveries), and a random-sample spot-check protocol with Northwind's IT team. Northwind's sign-off on the reconciliation report is a gate for go-live.

### 3.7 Non-Functional Requirements

| Requirement | Our commitment |
|---|---|
| Availability | 99.9% uptime SLA for production (excludes planned maintenance windows communicated 5 business days in advance) |
| Response time | 95th-percentile API response < 800ms under normal load; portal page load < 2s on a 10 Mbps connection |
| Concurrent users | Platform sized for 200 concurrent internal users and 500 concurrent portal sessions; load-tested before UAT |
| Data residency | All data stored and processed in Azure India regions. Certified via Azure compliance documentation provided at go-live |
| Security | OWASP Top 10 addressed by design; penetration test by a Meridian-retained third-party tester before go-live; findings remediated before cutover |
| Audit logging | All create/update/delete operations logged with user identity, timestamp, and before/after state; 2-year retention |
| Disaster recovery | RTO 4 hours, RPO 1 hour; automated failover to India South; DR drill included in hypercare |

---

## 4. Scope of Work

### 4.1 In Scope

**Phase 1 — Requirements & Design**
- Requirements elaboration workshops with Northwind business and IT stakeholders
- Functional specification document (FSD) covering all modules
- Technical architecture document (TAD)
- Data migration mapping document
- UI/UX wireframes for internal app and customer portal (reviewed and approved by Northwind)
- SAP integration design document (reviewed with Northwind's SAP team)
- EDI integration design document (reviewed with carrier EDI contacts)

**Phase 2 — Build & Integration**
- Core platform: order management, shipment tracking, document management, notifications
- SAP S/4HANA integration (six integration flows per §3.3)
- EDI integration with up to five carriers (per §3.4)
- Customer self-service portal (per §3.5)
- Data migration pipeline and reconciliation tooling (per §3.6)
- Internal and customer-facing user management and role-based access control
- Operational dashboards for logistics and customer-service teams
- API documentation (OpenAPI 3.0 spec for all endpoints)

**Phase 3 — Testing**
- Unit and integration testing (Meridian-run, results shared with Northwind)
- Performance and load testing (sizing per §3.7)
- Security testing (third-party penetration test)
- UAT support: test script preparation, defect triage, fix-and-retest cycles

**Phase 4 — Cutover & Go-Live**
- Cutover runbook (documented, rehearsed with Northwind IT in dry run)
- Final data migration and reconciliation
- Go / no-go checklist sign-off with Northwind Programme Manager
- Production deployment
- Hypercare: 4 weeks of elevated support post go-live (1-hour response on all P1/P2 incidents)

**Phase 5 — Handover**
- Technical documentation: architecture, deployment, runbooks, integration specs
- End-user training: operations team, customer-service team, IT admin team (live sessions + recorded)
- Administrator guide for Northwind IT
- Knowledge-transfer programme for L1 support handover (structured over months 10–12 of Year 1 support)

### 4.2 Exclusions

The following are explicitly out of scope for this engagement:

- SAP S/4HANA configuration, upgrade, or ABAP development
- Procurement or administration of SAP BTP licences (Northwind provides)
- Carrier EDI gateway infrastructure (Northwind provides; Meridian configures connections to it)
- Mobile application (iOS or Android)
- Order placement through the customer portal
- Integration with any system other than SAP S/4HANA and the carrier EDI gateway
- Hardware, network, or on-premises infrastructure
- Northwind's identity provider configuration beyond the SSO integration point

### 4.3 Change Request Process

Any work outside §4.1 is a change request. Meridian will provide a written change request note within three business days of identification, covering scope, effort estimate, cost at rate-card rates, and timeline impact. No change work begins without Northwind's written approval.

---

## 5. Delivery Approach & Methodology

### 5.1 Framework

We use a **hybrid delivery model**: structured phases with fixed deliverables for requirements and architecture (where specification-before-build reduces rework risk), and two-week Agile sprints for the build phase (where iterative delivery keeps Northwind visible to progress and allows course corrections before UAT).

### 5.2 Sprint Cadence

During the build phase:
- **Sprint planning** (Day 1 of each sprint): Northwind Product Owner reviews and prioritises the sprint backlog.
- **Sprint demo** (Day 10 of each sprint): Working software demonstrated to Northwind stakeholders. No PowerPoint — running code only.
- **Weekly written status report** covering: sprint burn-down, risks/issues log update, upcoming client dependencies, and milestone forecast.

### 5.3 Quality Gates

No phase starts until the preceding phase's gate is passed:

| Gate | Artefact | Approver |
|---|---|---|
| G1 — Requirements complete | Signed FSD | Northwind Programme Manager |
| G2 — Architecture approved | Signed TAD | Northwind IT Lead + Meridian Solution Architect |
| G3 — Build complete | All acceptance criteria met; test report green | Northwind Product Owner |
| G4 — UAT sign-off | Signed UAT acceptance | Northwind Programme Manager |
| G5 — Go / no-go | Completed go/no-go checklist | Northwind Programme Manager + Meridian Delivery Lead |

### 5.4 Defect Management

Defects are triaged against a four-level severity scale (Critical / High / Medium / Low). Critical and High defects must be resolved before UAT sign-off. The defect tracker (Azure DevOps Boards) is shared with Northwind's team throughout; Northwind's QA team has full visibility and can log defects directly.

---

## 6. Project Plan & Milestones

Assumes SOW sign-off by **24 June 2026**. All dates are target dates; dates marked † are client-dependency gates.

| # | Phase | Start | End | Key deliverable |
|---|---|---|---|---|
| M1 | Project kickoff & Requirements elaboration | 25 Jun 2026 | 22 Jul 2026 | Signed FSD, signed migration mapping |
| M2 | Architecture & design | 23 Jul 2026 | 5 Aug 2026 | Signed TAD, SAP & EDI integration design docs, UX wireframes |
| M3 | Sprint 1–3: Core platform build | 6 Aug 2026 | 16 Sep 2026 | Order management, shipment tracking, document management working in dev |
| M4 | Sprint 4–6: Integration & portal | 17 Sep 2026 | 28 Oct 2026 | SAP integration live in QA, EDI integration live in QA, customer portal live in QA |
| M5 | Integration & performance testing | 29 Oct 2026 | 11 Nov 2026 | Test report, pen-test report, performance results |
| M6 | UAT | 12 Nov 2026 | 9 Dec 2026 | Signed UAT acceptance, defect closure report |
| M7 | Cutover preparation | 10 Dec 2026 | 23 Dec 2026 | Cutover runbook, dry-run migration + reconciliation report v2 |
| M8 | **Go-live** | **5 Jan 2027** | **31 Jan 2027** | Production deployment, final reconciliation sign-off, hypercare start |
| M9 | Hypercare | 1 Feb 2027 | 28 Feb 2027 | Hypercare completion report, handover to BAU support |
| M10 | Support & Maintenance Year 1 | 1 Mar 2027 | 28 Feb 2028 | Monthly service reports, quarterly health audits |

> **Go-live is targeted for January 31, 2027** — six weeks inside the fixed end-of-Q1-2027 deadline. This buffer exists to absorb late-stage defect resolution, SAP environment access delays, or carrier EDI negotiation overruns without threatening the fixed date.

**Client-dependency gates (†):**

| Dependency | Required by |
|---|---|
| SAP S/4HANA dev + QA sandbox access credentials provided | Week 2 (8 Jul 2026) |
| Carrier EDI team contacts provided for all five carriers | Week 3 (15 Jul 2026) |
| Northwind IT provides legacy system schema + data dictionary | Week 2 (8 Jul 2026) |
| SAP BTP Integration Suite tenant provisioned for Meridian | Week 4 (22 Jul 2026) |
| Northwind's identity provider (Entra tenant ID + SSO config) shared | Week 4 (22 Jul 2026) |
| UAT resource commitment: nominated testers available full-time Weeks 19–22 | Week 1 (commitment confirmed in kickoff) |
| Northwind IT available for production SAP access setup | Week 17 (4 Nov 2026) |

If any † dependency slips by more than five business days, Meridian will raise a formal impact notice within two business days with a revised milestone forecast.

---

## 7. Team Composition & Governance

### 7.1 Meridian Delivery Team

| Role | Name | Allocation | Responsibility |
|---|---|---|---|
| Delivery Lead / Project Manager | Priya Nair | 100% | Day-to-day delivery, client relationship, risk and issue management, weekly status reports |
| Solution Architect | Karthik Subramaniam | 100% (M1–M5), 50% (M6–M9) | Architecture ownership, integration design, technical decisions |
| Senior Developer — Backend | Rohan Desai | 100% | Spring Boot services, core platform logic |
| Senior Developer — Integration | Anjali Krishnamurthy | 100% | SAP BTP iFlows, Azure Logic Apps, EDI integration |
| Developer — Backend | Vikram Patel | 100% | Supporting services, data migration pipeline |
| Developer — Frontend | Sneha Reddy | 100% | Internal web app (React) |
| Developer — Frontend | Aditya Sharma | 100% | Customer self-service portal (React) |
| QA Lead | Meera Iyer | 100% | Test strategy, UAT coordination, defect management |
| QA Engineer | Suresh Babu | 100% | Test execution, automation scripts |
| DevOps Engineer | Nikhil Joshi | 50% (M1–M2), 100% (M3–M8), 50% (M9) | CI/CD pipelines, Azure infrastructure, IaC |

All named resources are committed to this project. CVs available on request.

### 7.2 Northwind Counterpart Roles Required

| Role | Minimum availability | Responsibility |
|---|---|---|
| Programme Manager | 50% throughout | Single point of contact; milestone sign-off; escalation authority |
| SAP IT Lead | 25% (M1–M2), 50% (M4–M5) | SAP environment access; SAP API review; integration testing sign-off |
| Business Analyst / Product Owner | 50% (M1–M2), 25% (M3–M6) | Requirements workshops; sprint demos; UAT ownership |
| IT Infrastructure Contact | 10% throughout, 50% (M7–M8) | Environment provisioning; cutover execution |
| UAT Testers (Ops + CS teams) | 100% during UAT (M6) | UAT execution; defect reporting |

### 7.3 Governance Cadence

| Forum | Frequency | Participants | Purpose |
|---|---|---|---|
| Sprint demo | Fortnightly | Full teams | Working software review; sprint retrospective |
| Weekly status call | Weekly | PM + Northwind Programme Manager | Progress, risks, upcoming dependencies |
| Architecture review | As needed (min. monthly) | Solution Architect + Northwind IT Lead | Design decisions, integration issues |
| Steering committee | Monthly | Meridian VP Delivery + Northwind sponsor | Escalation, scope, commercial issues |

---

## 8. Roles & Responsibilities

| Activity | Meridian | Northwind |
|---|---|---|
| Requirements workshops | Facilitates, documents | Participates, approves FSD |
| SAP environment access | Configures integration | Provisions access, provides SAP team support |
| EDI gateway access | Configures AS2 channels | Provides gateway credentials; facilitates carrier introductions |
| Legacy system data extract | Builds ETL pipeline | Provides schema, data dictionary, and extract access |
| UAT test scripts | Prepares draft scripts | Reviews, augments, executes |
| Defect resolution | Fixes and retests | Verifies fixes in UAT |
| Production infrastructure | Provisions on Azure (Meridian-managed during project) | Approves architecture; provides Entra tenant access |
| Training delivery | Delivers live sessions + recordings | Nominates attendees; owns adoption |
| Cutover execution | Leads cutover runbook | Northwind IT co-executes; Programme Manager holds go/no-go authority |
| L1 support (Year 1, Months 1–9) | Meridian handles | Northwind escalates via ticketing system |
| L1 support (Year 1, Months 10–12) | Meridian provides shadowing + knowledge transfer | Northwind team takes over L1 with Meridian L2 backstop |

---

## 9. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | SAP environment access delayed beyond Week 2 | Medium | High | Flagged as dependency gate. Integration sprints scheduled for Weeks 5–18; up to 3 weeks of delay absorbed before timeline impact. Meridian raises formal impact notice if gate missed. |
| R2 | Carrier EDI teams unresponsive or slow to test | High | Medium | EDI channels require ~2 weeks per carrier. Onboarding starts Week 3. Carriers not ready by Week 16 are deferred to post-go-live as change requests — core platform go-live is not blocked by any single carrier. |
| R3 | Legacy data quality issues discovered during ETL dry run | Medium | High | Dry run scheduled in Week 16 — 10 weeks before cutover. Data quality issues have time to be resolved. A data quality report is produced and accepted by Northwind before dry-run 2. |
| R4 | SAP BTP licence procurement delayed | Medium | Medium | BTP Integration Suite tenant required by Week 4. Northwind to initiate procurement at SOW sign-off. Meridian provides licence specification on Day 1 of the project. |
| R5 | Scope creep through UAT | Medium | Medium | UAT tests agreed acceptance criteria only. New requirements raised during UAT are logged as change requests — not absorbed. UAT sign-off gate is criteria-based, not sentiment-based. |
| R6 | Key Meridian resource unavailability | Low | High | All roles have named backups in our Bengaluru delivery pool. CVs held. Replacement resource mobilised within 5 business days. |
| R7 | Northwind UAT resourcing insufficient | Medium | High | UAT resource commitment confirmed at kickoff (M1 gate). If Northwind cannot release testers, UAT window is extended with a corresponding milestone slip — Meridian raises impact notice with revised go-live forecast. |

---

## 10. Case Studies & Credentials

### 10.1 BlueDart Freight — Shipment Visibility Platform with Carrier EDI (2024)

**Client:** A major Indian 3PL operator (name withheld under NDA; reference available on request)
**Scope:** Built a shipment visibility and carrier management platform integrating with 8 carriers via X12 EDI over AS2. Azure-hosted; 3,000 shipments/day at peak.
**SAP relevance:** Integrated with client's SAP S/4HANA (EWM module) for warehouse-outbound triggers via BTP iFlows.
**Timeline:** 22-week delivery. Go-live achieved on the committed date.
**Outcome:** Manual carrier status calls eliminated; on-time delivery reporting automated; customer dispute resolution time reduced from 4 days to same-day.

*Reference: Available on request — client's Head of IT, contact details provided in oral defence.*

### 10.2 Agrilink Distribution — Order Tracking Portal & SAP Integration (2023–2024)

**Client:** Mid-market agricultural commodities distributor operating across India and Southeast Asia
**Scope:** Customer self-service order tracking portal (2,000 registered enterprise customer users) with real-time sync from SAP S/4HANA sales orders and deliveries.
**SAP relevance:** SAP S/4HANA integration via Sales Order A2X API and Outbound Delivery API — same integration pattern proposed for Northwind.
**Timeline:** 18-week delivery to go-live. Two subsequent enhancement releases delivered on time.
**Outcome:** Customer "where is my order?" call volume reduced 68% in first quarter post go-live.

*Reference: CTO, Agrilink Distribution — reference letter available.*

### 10.3 Zenith Cargo Services — Legacy Migration & EDI Onboarding (2025)

**Client:** Mid-size freight forwarding company
**Scope:** Full migration of 7 years of operational data from a bespoke Access/SQL Server legacy system to a new Azure PostgreSQL platform, with zero-data-loss contractual commitment. Simultaneously onboarded 4 carriers to EDIFACT IFTMIN/IFTSTA EDI.
**Relevance to Northwind:** Directly comparable data migration challenge and EDI carrier onboarding scope.
**Outcome:** Reconciliation report accepted by client on first submission. All four carriers live within the project window.

*Reference: Operations Director, Zenith Cargo Services — available on request.*

### 10.4 Certifications & Partnerships

- Microsoft Solutions Partner — Digital & App Innovation (Azure)
- SAP Certified Integration Associate — SAP Integration Suite
- ISO 27001:2022 certified (Bengaluru delivery centre)
- Azure Security Specialty (4 certified architects in team)

---

## 11. Commercial Summary

> Full commercial detail, including unit pricing, milestone amounts, and rate card, is submitted as a separate sealed PDF (reference MIS-NWT-2026-COMM-001) per §9 of the RFP.

### 11.1 Build — Fixed Price, Milestone-Based

The build is priced as a fixed-price engagement. Milestones, deliverables tied to each milestone, and the payment percentage per milestone are detailed in the commercial PDF. Payments are triggered by Northwind's written acceptance of the milestone deliverable, not by calendar date.

### 11.2 Change Requests — T&M at Rate-Card Rates

Change requests are priced at agreed rate-card rates (INR per hour, per role, inclusive of all Meridian overheads). The rate card is fixed for the duration of the build and Year 1 support period, with a CPI-linked adjustment applicable from Year 2.

### 11.3 Support & Maintenance — Year 1

Priced as an annual fee, payable quarterly in advance. Covers the SLA framework in Annexure C. Renewal option at Year 1 end: one additional year at Year 1 rate + agreed escalation cap.

### 11.4 Travel & Out-of-Pocket Expenses

Billed at actuals against pre-approved limits: domestic air travel (economy class), accommodation (INR 8,000/night cap in metros), and ground transport. International travel (if required for Singapore/UAE stakeholder sessions) requires written pre-approval with a separate per-trip budget. All amounts quoted exclusive of applicable GST.

### 11.5 All Amounts

All amounts are in **Indian Rupees (INR)**, exclusive of GST. GST will be charged at the applicable rate.

---

## Annexure A — Assumptions & Dependencies

| # | Assumption / Dependency | Owner | Required by |
|---|---|---|---|
| A1 | Northwind provides SAP S/4HANA dev and QA sandbox access with dedicated integration user accounts | Northwind IT | Week 2 |
| A2 | Northwind provides Azure Entra tenant ID and authorises Meridian to register app registrations for SSO | Northwind IT | Week 4 |
| A3 | Northwind procures and provisions SAP BTP Integration Suite tenant with sufficient message-count quota | Northwind IT | Week 4 |
| A4 | Northwind provides contact details for each carrier's EDI team (up to 5 carriers) | Northwind Ops | Week 3 |
| A5 | Northwind provides full legacy system data dictionary, schema export, and read-only database access for ETL work | Northwind IT | Week 2 |
| A6 | Northwind's carrier EDI gateway supports AS2 inbound and outbound; Meridian is not responsible for EDI gateway infrastructure or licensing | Northwind IT | Week 3 |
| A7 | Northwind's SAP S/4HANA is on a current release supported by SAP Business Accelerator Hub APIs. If custom ABAP or non-standard APIs are required, this is a change request. | Northwind IT | Week 1 (confirmed in kickoff) |
| A8 | UAT testers from Northwind's operations and customer-service teams are available full-time during the UAT window (M6) | Northwind PM | Confirmed at kickoff |
| A9 | Northwind provides a staging environment (or approves Meridian's use of Azure-hosted staging) that is representative of production data volumes | Northwind IT | Week 2 |
| A10 | The legacy system remains available for read-only data extraction until cutover migration is complete and reconciliation is signed off | Northwind IT | Throughout |
| A11 | Northwind will not make changes to the SAP S/4HANA configuration during integration testing windows without 5 business days' notice | Northwind SAP Lead | From M4 |
| A12 | Data residency requirement is India. No data needs to reside in or transit through Singapore or UAE Azure regions. | Confirmed in RFP | — |

---

## Annexure B — Technology Stack Detail

*Full technology stack detail, including specific Azure SKUs, version pinning policy, open-source licence inventory, and third-party component list is available as a separate technical annexure. Provided at architecture sign-off (G2 gate) and updated at go-live as part of the technical documentation set.*

---

## Annexure C — Support SLA Framework

### Year 1 Support Coverage

- **Support hours:** 08:00–20:00 IST, Monday–Friday. On-call cover for P1 incidents outside these hours.
- **P1 — Critical (system down / data loss risk):** 1 business hour response; 4 business hour resolution target; Meridian on-call engineer engaged immediately; Northwind Programme Manager notified within 30 minutes.
- **P2 — High (major function impaired, workaround unavailable):** 4 business hour response; 1 business day resolution target.
- **P3 — Medium (function impaired, workaround available):** 1 business day response; 5 business day resolution target.
- **P4 — Low (cosmetic / enhancement request):** Acknowledged within 2 business days; scheduled in next patch release.

### Included in Year 1 Annual Fee

- All P1–P3 incident resolution
- Monthly patch releases (security patches, minor defect fixes)
- Quarterly system health and security audit (written report delivered to Northwind IT)
- Dedicated ticketing system (Northwind IT team has direct access)
- Monthly service report (incident summary, SLA performance, patch log)
- Knowledge-transfer programme (Months 10–12): structured handover of L1 support capability to Northwind's IT team

### Excluded from Annual Fee (Change Requests)

- New features or enhancements
- Changes to SAP integration flows driven by SAP configuration changes by Northwind
- New carrier EDI onboarding
- Infrastructure scaling beyond agreed baseline sizing

---

*End of Statement of Work — MIS-NWT-2026-SOW-001 Rev 1.0*

*This document and all attachments are submitted in confidence in response to RFP NWT-RFP-2026-LOG-014. All contents are proprietary to Meridian InfoSystems Pvt. Ltd. and Northwind Trading Corp and may not be disclosed to third parties.*
