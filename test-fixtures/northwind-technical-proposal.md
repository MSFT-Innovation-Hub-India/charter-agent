# Technical Proposal — Custom Logistics & Order-Tracking Platform
### Companion to SOW MIS-NWT-2026-SOW-001 · Response to RFP NWT-RFP-2026-LOG-014

---

| | |
|---|---|
| **Submitted by** | Meridian InfoSystems Pvt. Ltd. |
| **Registered address** | 14th Floor, Prestige Shantiniketan, Whitefield, Bengaluru — 560048 |
| **Contact** | Karthik Subramaniam, Solution Architect — karthik.subramaniam@meridianinfosystems.in · +91 98860 23456 |
| **Submitted to** | Eleanor Vance, Head of Procurement, Northwind Trading Corp |
| **Submission date** | 2026-06-06 |
| **RFP reference** | NWT-RFP-2026-LOG-014 |
| **Document reference** | MIS-NWT-2026-TECH-001 Rev 1.0 |
| **Companion documents** | Commercial Response MIS-NWT-2026-COMM-001 (sealed) · SOW MIS-NWT-2026-SOW-001 |

> This Technical Proposal is the engineering companion to our SOW. The SOW defines **what** we will deliver and on **what commercial terms**; this document defines **how** we will build it. Where the two documents reference the same item, the SOW is authoritative for scope and the Technical Proposal is authoritative for engineering detail.

---

## Table of Contents

1. Engineering Principles
2. Logical Architecture
3. Physical Deployment on Azure (India)
4. Service Decomposition & Domain Model
5. SAP S/4HANA Integration — Detailed Design
6. Carrier EDI Integration — Detailed Design
7. Customer Self-Service Portal — Technical Design
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
| P1 | **Standard APIs, never custom plumbing into SAP.** | No ABAP. No direct DB access. SAP integration via Business Accelerator Hub APIs through BTP Integration Suite only. |
| P2 | **Loosely coupled services behind a single gateway.** | Independently deployable domain services; API Management is the only ingress. Internal services do not call each other directly across domain boundaries — they go via the gateway or async events. |
| P3 | **Async by default, sync only when the user is waiting.** | UI calls are synchronous and snappy. Everything else (SAP posts, EDI dispatch, notification fan-out, document generation) is queued via Azure Service Bus with dead-letter recovery. |
| P4 | **Infrastructure is code, never clicks.** | Every environment — dev, QA, UAT, staging, prod, DR — is rebuilt from Terraform. Manual portal edits to production are explicitly forbidden by policy. |
| P5 | **Observability is a build-time concern, not a launch-time bolt-on.** | Distributed tracing, structured logging, and SLO-aligned metrics are wired in Sprint 1 — not retrofitted before UAT. |
| P6 | **Northwind owns the platform at handover.** | No proprietary Meridian frameworks. Stock Spring Boot, stock React, stock Azure services. Anything Northwind's IT team cannot Google must be justified in writing. |

---

## 2. Logical Architecture

The platform is composed of six logical layers, each with a single responsibility and a stable contract to its neighbours.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Presentation Layer                                                     │
│  ├── Internal Web App (React 18 + TS)                                   │
│  └── Customer Self-Service Portal (React 18 + TS)                       │
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
│  ├── order-service          ├── shipment-service                        │
│  ├── document-service       ├── portal-service                          │
│  ├── notification-service   └── admin-service                           │
└────────┬────────────────────────────────────────────┬───────────────────┘
         │                                            │
┌────────▼────────────────────┐         ┌─────────────▼──────────────────┐
│  Integration Layer          │         │  Persistence Layer             │
│  ├── sap-iflow (BTP)        │         │  ├── Azure PostgreSQL Flex     │
│  ├── edi-logicapps          │         │  ├── Azure Cache for Redis     │
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
| Static frontends | Azure Front Door (global anycast, India PoPs) | — |
| Backups | Geo-redundant storage between India Central → India South | — |

No data leaves Indian Azure regions. This is enforced by Azure Policy assignments at the subscription level (denying resource creation in non-India regions) committed alongside the Terraform.

### 3.2 Subscription & Resource Group Layout

| Subscription | Purpose |
|---|---|
| `northwind-logistics-nonprod` | dev, QA, UAT — shared lower environments, separated by resource group |
| `northwind-logistics-prod` | Production + DR + production observability |
| `northwind-logistics-shared` | Shared services (Key Vault, ACR, DNS zones, Front Door) |

Resource groups follow `rg-nwt-log-{env}-{layer}` (e.g. `rg-nwt-log-prod-data`). Tags: `costCenter`, `environment`, `owner`, `dataClassification`, `compliance`.

### 3.3 Azure SKUs (production sizing baseline)

| Service | SKU | Rationale |
|---|---|---|
| Azure Container Apps environment | Workload Profiles (D8s v3, min 2 / max 10 nodes) | Predictable CPU; serverless autoscale on top |
| Azure PostgreSQL Flexible Server | GP_Standard_D8s_v5, 512 GB storage, zone-redundant HA | 200 concurrent internal users + 500 portal sessions per §3.7 of SOW |
| Azure Cache for Redis | Premium P1 (6 GB), zone-redundant | Session store + tracking hot-path |
| Azure Service Bus | Premium (1 MU), zone-redundant | Predictable latency, VNet integration, geo-DR pairing |
| API Management | Premium v2, 1 unit + auto-scale, zone-redundant | VNet integration, multi-region capable for DR |
| Azure Front Door | Standard | WAF, caching for static assets |
| Azure Blob Storage | RA-GZRS (read-access geo-redundant) | Immutable tier for POD/BOL retention |
| Key Vault | Premium (HSM-backed for signing keys) | Customer-managed keys for PostgreSQL TDE |

All sizing is **baseline**. Final sizing is locked at architecture sign-off (G2 gate) after load-test results in Week 16. Annexure T1 carries the full sizing worksheet.

---

## 4. Service Decomposition & Domain Model

### 4.1 Bounded Contexts

| Service | Bounded context | Owns (writes) | Reads (via API) |
|---|---|---|---|
| `order-service` | Order lifecycle | `orders`, `order_lines`, `order_events` | inventory snapshots from SAP cache |
| `shipment-service` | Carrier shipments | `shipments`, `shipment_legs`, `tracking_events` | orders (via order-service) |
| `document-service` | BOL / POD / invoices | `documents`, `document_metadata` | blob storage |
| `portal-service` | Customer portal façade | `portal_users`, `portal_preferences` | orders, shipments, documents (composed view) |
| `notification-service` | Outbound email/SMS | `notification_log` | none — pure consumer of events |
| `admin-service` | Internal RBAC, config | `users`, `roles`, `audit_log` | identity provider |

Each service has its own PostgreSQL schema in a shared cluster (cost-optimised baseline; can be split into per-service clusters later without code change).

### 4.2 Canonical Domain Events

Published to Service Bus topics; consumed by any service that needs them.

| Event | Publisher | Typical consumers |
|---|---|---|
| `order.created` | order-service | notification-service, portal-service (cache warm) |
| `order.allocated` | order-service | shipment-service |
| `shipment.booked` | shipment-service | order-service, notification-service |
| `shipment.status_updated` | shipment-service | notification-service, portal-service |
| `shipment.delivered` | shipment-service | order-service, document-service (POD ingest), notification-service |
| `document.attached` | document-service | portal-service |

Schema: CloudEvents 1.0 JSON envelope. Schema registry: a versioned `events/` folder in the platform mono-repo, with consumer-driven contract tests gating breaking changes.

---

## 5. SAP S/4HANA Integration — Detailed Design

### 5.1 Integration Topology

```
Platform Service  ──HTTPS──▶  API Management  ──HTTPS──▶  BTP Integration Suite (iFlow)  ──OData/REST──▶  SAP S/4HANA
       ▲                                                          │
       │                                                          │
       └─────────── async callback via Service Bus ◀──────────────┘
                       (for long-running SAP posts)
```

We do **not** call SAP APIs directly from application services. Every SAP interaction goes through an iFlow in BTP Integration Suite, which:

- Handles SAP authentication (OAuth 2.0 client credentials against SAP IAS).
- Performs payload transformation (canonical JSON ↔ SAP IDoc/OData structures).
- Applies retry policy (3 attempts, exponential back-off 5 / 30 / 120 min).
- Writes failures to a Service Bus dead-letter queue with structured error context.

### 5.2 Per-Flow Specification

| Flow | SAP API | Direction | Trigger | Latency budget | Failure mode |
|---|---|---|---|---|---|
| Sales order ingest | Sales Order A2X (`API_SALES_ORDER_SRV`) | SAP → Platform | SAP `BO_SALESORDER_CHANGED` event via BTP | < 30s end-to-end | DLQ + ops alert; SAP order remains source of truth |
| Inventory check | Product Availability A2X | Platform → SAP | Allocation step in order-service | < 800ms p95 | Returns "availability unknown" → order parked for manual review |
| Outbound delivery | Outbound Delivery API | Platform → SAP | shipment.booked event | < 5s p95 | Retried via DLQ; shipment marked `pending_erp_sync` |
| Goods issue | Goods Movement API | Platform → SAP | Carrier pickup confirmation | < 5s p95 | Same as above |
| POD sync | Delivery Document API | Platform → SAP | shipment.delivered event | < 10s p95 | Retried; POD doc remains in platform blob even if SAP post fails |
| Invoice status | Customer Invoice A2X | SAP → Platform | SAP invoice post event | < 60s | DLQ + ops alert; portal shows last-known status |

### 5.3 Idempotency

Every SAP-bound message carries a platform-generated `X-Idempotency-Key` (UUID v7, time-ordered). The iFlow caches keys for 7 days; duplicate POSTs return the original response. This protects against double-posting under retry storms — the single most common SAP integration bug in our experience.

### 5.4 SAP Environment Strategy

| Environment | SAP system | Use |
|---|---|---|
| Platform dev | SAP dev sandbox | iFlow development, smoke tests |
| Platform QA | SAP QA sandbox | Integration regression, performance baselining |
| Platform UAT | SAP QA sandbox (frozen during UAT) | UAT execution |
| Platform prod | SAP production | Live |

We do **not** require a dedicated SAP sandbox per platform environment. SAP QA is shared between Platform QA and UAT but is frozen during UAT (Northwind's SAP team to coordinate) to prevent UAT defect contamination.

---

## 6. Carrier EDI Integration — Detailed Design

### 6.1 Logical Flow

```
shipment-service ─event─▶ Service Bus ─trigger─▶ edi-logicapps ─AS2─▶ Carrier EDI Gateway ─AS2─▶ Carrier
                                                                              ▲
                                                                              │
                                              edi-logicapps ◀─AS2 inbound────┘
                                                     │
                                                     ▼
                                              shipment-service (status / POD update)
```

### 6.2 Standards & Message Mapping

| Business event | X12 | EDIFACT equivalent |
|---|---|---|
| Shipment booking request | 204 | IFTMIN |
| Booking acknowledgement | 990 | IFTMBC |
| Status milestones (in-transit) | 214 | IFTSTA |
| Advance ship notice | 856 | DESADV |
| Proof of delivery | 214 (final segment) | IFTSTA |

The Vendor will negotiate the active flavour (X12 vs EDIFACT) per carrier during onboarding. Both code paths are implemented; selection is configuration-only.

### 6.3 AS2 Channel Configuration

Each carrier channel is provisioned in Logic Apps Integration Account with:
- AS2 partner agreement (MIC algorithm SHA-256, encryption AES-256, signing required).
- Certificate rotation calendar (12-month default, reminders at 90 / 30 / 7 days).
- Inbound MDN required, ackTimeout 10 minutes.
- Inbound message archival to immutable Blob (7-year retention for audit).

### 6.4 Carrier Onboarding Runbook

Per carrier (~2 weeks elapsed, ~3 person-days of effort):

| Day | Activity |
|---|---|
| 1–2 | Exchange AS2 URLs and certificates; partner agreement set up in lower environment |
| 3 | Smoke test: 204 → 990 round trip |
| 4–6 | Carrier-specific quirks discovery (segment qualifiers, non-standard date formats — every carrier has them) |
| 7–9 | Full message-set regression (204, 990, 214 ×N, 856, POD) |
| 10 | UAT in lower env with Northwind ops witness |
| 11–14 | Promotion to QA → UAT → production windows |

### 6.5 Failure Handling

EDI is the most failure-prone integration in any logistics platform. Specific guards:

- **Dead-letter queue per carrier** — failures are not commingled, so a single misbehaving carrier does not poison the queue.
- **Carrier-level circuit breaker** — 5 consecutive failures within 5 minutes opens the breaker; bookings to that carrier queue for manual retry; ops alert raised.
- **Status update reconciliation job** — daily 02:00 IST job reconciles platform-known statuses against a fresh inbound from each carrier; gaps trigger an exception report.

---

## 7. Customer Self-Service Portal — Technical Design

### 7.1 Architecture

The portal is a **separate React SPA** deployed to Azure Static Web Apps with its own domain (`portal.northwindtrading.com`) and its own API ingress (`portal-api.northwindtrading.com` → API Management → `portal-service`).

Internal app and portal share a component library (`@northwind/ui-kit`, internal NPM package) but are otherwise separately deployed and separately scaled. A misbehaving portal load test cannot impact internal operations.

### 7.2 Identity (See §11 for full detail)

Azure Entra External ID (B2B), invitation-only. No public sign-up. Northwind customer-service team invites users via an internal admin page; users receive an email with a one-time setup link.

### 7.3 Capabilities — Technical Notes

| Capability | Implementation note |
|---|---|
| Real-time tracking | Status timeline rendered from `shipment.status_updated` events; portal uses long-polling (5s interval) backed by Redis cache (TTL 30s) — keeps p95 below 200ms without requiring web sockets in v1 |
| Document download | Signed Blob SAS URLs, 5-min TTL, scope-locked to the requesting user's account |
| CSV/Excel export | Async — request queued, email link sent when ready; prevents browser timeouts on large exports |
| Notification preferences | Per-user, per-event-type; stored in `portal_preferences`; consumed by notification-service |
| Multi-user per customer account | Two roles: `account_admin` (manages own users), `account_viewer`; enforced at API Management policy + service-layer check |

### 7.4 Explicit Non-Goals (v1)

Order placement, returns, in-portal messaging are out of scope per SOW §3.5. The portal's component library and routing are structured to allow these as additive future work without core refactor.

---

## 8. Data Model & Persistence Strategy

### 8.1 Database Topology

Single Azure PostgreSQL Flexible Server, one logical database (`logistics`), one schema per service:
- `orders`, `shipments`, `documents`, `portal`, `notifications`, `admin`.

Cross-schema foreign keys are **forbidden**. Cross-service references are stored as opaque IDs and resolved via API. This is enforced by a CI check on migrations (Flyway).

### 8.2 Core Entity Shapes (illustrative)

```
orders.order
  id (UUID v7, PK)
  external_ref (text, SAP sales order number, unique)
  customer_account_id (text)
  status (enum: created, allocated, shipped, delivered, cancelled)
  created_at, updated_at (timestamptz, both indexed)
  payload (jsonb, full canonical order)
  sap_sync_status (enum: pending, synced, failed)
  sap_sync_attempts (int)

shipments.shipment
  id (UUID v7, PK)
  order_id (UUID, FK within shipments schema mirror table — NOT cross-schema)
  carrier_code (text)
  carrier_booking_ref (text)
  status (enum: booked, in_transit, delivered, exception, cancelled)
  ...

shipments.tracking_event
  id (UUID v7, PK)
  shipment_id (FK)
  event_code (text)        -- carrier-specific
  event_code_normalized (text)  -- mapped to platform vocabulary
  event_time (timestamptz)
  source (enum: edi, manual, reconciliation_job)
```

### 8.3 Migrations

Flyway, versioned forward-only migrations. Schema changes go through the CI/CD pipeline like any other code; production migrations run in the deploy step with a guarded rollback window.

### 8.4 Audit Trail

`admin.audit_log` table captures every create/update/delete with: `user_id`, `service`, `entity_type`, `entity_id`, `action`, `before_jsonb`, `after_jsonb`, `at`. Retention 2 years (Postgres) then archived to immutable Blob (5 additional years).

---

## 9. Data Migration — Technical Approach

### 9.1 Pipeline

```
Legacy SQL Server  ──Azure Data Factory──▶  Staging Blob (Parquet)  ──ADF Mapping Data Flow──▶  PostgreSQL staging schema  ──validation──▶  PostgreSQL production schemas
```

ADF is selected over custom Spring Batch jobs because (a) it gives Northwind IT a portal-driven view of pipeline runs they can operate themselves post-handover, and (b) its Mapping Data Flows make field-level mapping reviewable by business analysts without a code change.

### 9.2 Reconciliation Methodology

For each entity:
1. **Row count parity** — source count vs target count must match exactly. Mismatch fails the run.
2. **Financial total parity** — sum of order values, sum of outstanding deliveries; match within ₹1 tolerance (rounding only).
3. **Random sample spot check** — 100 randomly selected records per entity, full field-by-field comparison; Northwind QA reviews.
4. **Edge-case checklist** — null handling, multi-byte characters (customer names in Hindi/Tamil/Arabic), historical date overflows, carrier codes no longer in active use.

All reconciliation output is a versioned PDF report committed to the project SharePoint and signed off by Northwind IT.

### 9.3 Cutover Window

| Window | Activity |
|---|---|
| T-7 days | Final dry-run reconciliation; sign-off |
| T-1 day | Freeze legacy writes (read-only mode) |
| T-0 (Saturday night) | Final delta migration (~6 hours); reconciliation; smoke tests |
| T+0 (Sunday) | Northwind go/no-go meeting at 09:00; production switch by 12:00 |
| T+1 (Monday) | Hypercare day 1 — full Meridian team on standby |

---

## 10. Security Architecture

### 10.1 Defence-in-Depth Layers

| Layer | Control |
|---|---|
| Edge | Azure Front Door WAF (OWASP Core Rule Set 3.2), DDoS Standard, geo-fence (India primary, plus negotiated allow-list for Singapore/UAE customers) |
| Ingress | API Management with JWT validation, rate limit (per-subscriber + per-IP), payload size cap (1 MB default, 25 MB for document upload), strict Content-Type allow-list |
| Service | mTLS between API Management and Container Apps; Spring Security at service layer; method-level `@PreAuthorize` |
| Data in transit | TLS 1.3 minimum on every hop; AS2 messages signed + encrypted |
| Data at rest | PostgreSQL TDE with customer-managed keys (Key Vault HSM); Blob SSE with CMK; Redis encryption at rest |
| Secrets | All secrets in Key Vault; services pull via Managed Identity; no secrets in env vars or config files |
| Compute | Container images scanned (Defender for Containers + Trivy in CI); only signed images from Northwind ACR pulled in production |
| Network | All compute in private subnets; egress through Azure Firewall with FQDN allow-list (only SAP BTP, EDI gateway, Entra endpoints) |

### 10.2 OWASP Top 10 (2021) — How Each Is Addressed

| Item | Mitigation |
|---|---|
| A01 Broken Access Control | Method-level auth; portal multi-tenancy enforced both at API Management (policy) and service (defence in depth) |
| A02 Cryptographic Failures | TLS 1.3 only; HSM-backed keys; no homegrown crypto |
| A03 Injection | Parameterised SQL (JPA + named parameters); no string-built queries; input validation at edge + service |
| A04 Insecure Design | Threat model per service, reviewed at design gate; abuse cases in test plan |
| A05 Security Misconfiguration | IaC-only deployments; Defender for Cloud baseline; Azure Policy denies public IP, public Blob, etc. |
| A06 Vulnerable Components | Dependabot + Trivy in CI; weekly base-image rebuild |
| A07 Identification & Authentication Failures | Entra ID enforced; MFA enforced for all internal users; portal MFA optional v1, mandatory roadmap |
| A08 Software & Data Integrity Failures | Signed container images; SBOM published per release; ADO pipelines require code review + 2 approvals on prod branches |
| A09 Logging & Monitoring Failures | Centralised Log Analytics; security events forwarded to Northwind's SIEM if requested (out of scope for v1, hook in place) |
| A10 SSRF | Egress allow-list at Azure Firewall; no user-controlled URLs in server-side fetches |

### 10.3 Penetration Test

Third-party penetration test (Meridian-retained, ISO 27001-certified tester) in Week 23, after UAT defect closure and before cutover. Findings categorised Critical/High/Medium/Low. Critical and High must be remediated before go/no-go. Medium and Low ride into Year 1 backlog with agreed deadlines.

### 10.4 Compliance Posture

- **DPDP Act 2023 (India)**: Personal data of Indian data principals stored only in India regions. Right-to-erasure handled via a documented runbook against `portal_users` + audit-log redaction.
- **ISO 27001:2022**: Meridian's Bengaluru delivery centre is certified. Northwind inherits the controls applied to this engagement.
- **SOC 2**: Not pursued in v1 unless added as a change request.

---

## 11. Identity, Authentication & Authorisation

### 11.1 Internal Users

- IdP: Northwind's existing Azure Entra tenant.
- Protocol: OIDC (auth code + PKCE).
- MFA: enforced via Northwind Conditional Access policy (no change required from Meridian).
- Group-to-role mapping: configured by Northwind IT, consumed by `admin-service` via Microsoft Graph at login time.
- Session: 8-hour sliding, hard 12-hour expiry.

### 11.2 Customer Portal Users

- IdP: Azure Entra External ID (separate tenant, B2B).
- Sign-up: invitation-only.
- MFA: optional in v1 (email OTP available); mandatory MFA recommended at portal v1.1.
- Account binding: each portal user is bound to one customer account; multi-account access via separate invitations.
- Session: 1-hour sliding, hard 8-hour expiry.

### 11.3 Service-to-Service

- Managed Identity for every Container App.
- API Management validates inbound JWT from Entra; subscription key required for system-to-system callers.
- Service-to-service calls cross API Management (no direct mesh in v1; service mesh deferred unless complexity warrants).

### 11.4 Authorisation Model

Hybrid: coarse-grained role check at API Management policy (fast reject for unauthorised paths) + fine-grained, resource-level check in service layer (e.g. "this portal user belongs to the account that owns this order"). Both checks are required; neither is sufficient alone.

---

## 12. Non-Functional Engineering

### 12.1 Performance Targets & Headroom

| Metric | Target (SOW §3.7) | Engineering headroom plan |
|---|---|---|
| API response p95 | < 800ms | Internal target 400ms; alert at 600ms |
| Portal page load | < 2s @ 10 Mbps | Internal target 1.2s; CDN-cached shell, lazy-loaded routes |
| Concurrent internal users | 200 | Sized for 400, autoscale ceiling 800 |
| Concurrent portal sessions | 500 | Sized for 1 000, autoscale ceiling 2 000 |

### 12.2 Scalability Strategy

- **Stateless services** behind Azure Container Apps autoscale (KEDA rules: CPU > 70% for 60s, or HTTP queue length > 50).
- **Database**: vertical scale (D8s → D16s) is the v1 strategy; read replicas added if portal read load demands it (no application change required).
- **Redis**: cluster-mode-capable SKU chosen for v1 to enable horizontal scale without redeployment.
- **Service Bus**: Premium messaging units scale 1 → 4 with no code change.

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

- **RTO 4 hours, RPO 1 hour** per SOW §3.7.
- **Strategy**: warm-standby in India South. Infrastructure deployed via the same Terraform; database replication via PostgreSQL geo-restore (RPO governed by backup frequency, configured at 15 minutes).
- **DR drill**: scheduled in Week 26 (hypercare); annual thereafter, planned as part of Year 1 support.

### 12.5 Maintenance Windows

- Scheduled: every second Sunday, 02:00–05:00 IST. Five business days' notice via the platform status page and email.
- Emergency: zero-downtime patching for everything except database major-version upgrades.

---

## 13. Observability & Operations

### 13.1 The Three Pillars

| Pillar | Tooling | What's instrumented |
|---|---|---|
| **Logs** | Log Analytics workspace, 90-day hot retention, 2-year archive | Structured JSON, correlation ID per request, sensitive fields redacted at source |
| **Metrics** | Azure Monitor + Application Insights | RED metrics (Rate, Errors, Duration) per endpoint; USE metrics (Utilisation, Saturation, Errors) per resource |
| **Traces** | Application Insights distributed tracing | W3C Trace Context propagated through API Management → services → SAP iFlow → EDI Logic Apps |

### 13.2 SLOs

| SLO | Target | Error budget |
|---|---|---|
| Internal app availability | 99.9% monthly | ~43 min/month |
| Portal availability | 99.5% monthly | ~3.6 hr/month |
| Order ingest end-to-end (SAP → platform visible) | 99% < 60s | 7.2 hr/month |
| Booking dispatch (event → AS2 sent) | 99% < 30s | 7.2 hr/month |

Error budget burn-rate alerts (fast-burn 2% / 1h, slow-burn 5% / 6h) routed to the on-call rota.

### 13.3 Dashboards

Three production dashboards delivered at go-live:
- **Platform health** — RED metrics, SLO burn, dependency health (SAP, EDI, IdP).
- **Logistics ops** — orders today, shipments in-transit, exception count, top failing carriers.
- **Customer-service** — portal active sessions, document download rate, support ticket correlation view.

Dashboards committed as JSON in the repo. No portal-clicked dashboards.

### 13.4 Alerting

| Severity | Routing | Examples |
|---|---|---|
| Sev 1 | PagerDuty → primary on-call → escalate at 15 min | Service down, DB primary unhealthy, EDI breaker open for > 5 min |
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
| QA | nonprod | Promoted from dev after CI green | Synthetic + anonymised legacy sample |
| UAT | nonprod | Promoted from QA at sprint boundary | Full anonymised legacy migration dry-run dataset |
| staging | prod | Promoted from UAT after sign-off; production-parity infra | Production-restore snapshot, anonymised |
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
UAT deploy  (manual approval — Northwind PO)
   ↓ UAT acceptance
staging deploy  (manual approval — Meridian Delivery Lead)
   ↓ smoke + pen-test results
prod deploy  (manual approval — Northwind PM + Meridian Delivery Lead)
```

Deploy strategy: blue-green for application services; rolling for stateless support services; in-place with migration guard for the database.

### 14.3 Infrastructure as Code

- **Terraform 1.7+**, modules-per-service.
- State in Azure Storage with per-environment containers and state-locking.
- `terraform plan` posted as a PR comment on every IaC change; merge requires explicit Northwind IT approval for prod-affecting changes.

### 14.4 Branching

Trunk-based development with short-lived feature branches (max 5 days). Feature flags (LaunchDarkly OSS alternative or stock Spring property-driven flags) for incomplete work in `main`.

### 14.5 Artefact Management

- Container images → Northwind ACR (private, geo-replicated India Central + South).
- Maven artefacts → ADO Artifacts feed.
- NPM artefacts → ADO Artifacts feed.
- Images signed with Notation; only signed images deployable to production (enforced by Azure Policy).

---

## 15. Testing Strategy

### 15.1 Test Pyramid

| Layer | Coverage target | Owner |
|---|---|---|
| Unit | ≥ 80% line coverage per service | Devs |
| Integration | All inter-service contracts + all SAP iFlows + all EDI message round-trips | Devs + QA |
| Contract (CDC) | Every event producer ↔ consumer pair | Devs |
| End-to-end (UI) | Top 30 user journeys (internal + portal) automated with Playwright | QA |
| Performance | Sustained 1× target load + 5-min 2× spike, before UAT | QA + DevOps |
| Security | SAST per PR; DAST in QA; third-party pen test pre-go-live | QA + external |
| Chaos (light) | Quarterly in Year 1: AZ failover, dependency outage, DB failover | DevOps |

### 15.2 Test Data Strategy

- Synthetic data generator (custom Spring CLI, committed to repo) for dev/QA.
- Anonymised production-shape dataset for UAT and staging, refreshed quarterly.
- Personally identifiable fields irreversibly hashed; financial amounts perturbed within 5%.

### 15.3 UAT Support

- Pre-written test scripts (one per FSD section).
- Defect-triage call daily during UAT (30 min, 09:30 IST).
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
| Code review | 2 approvers; at least one with backend or frontend specialisation per touched layer |

### 16.3 Documentation as Code

- ADRs (Architecture Decision Records) committed to repo, numbered, immutable.
- Service READMEs follow a template (purpose, owns, depends on, env vars, runbooks).
- OpenAPI spec generated from code; published to API Management developer portal.

---

## 17. Open-Source & Third-Party Components

### 17.1 Significant OSS Dependencies

| Component | Licence | Use |
|---|---|---|
| Spring Boot 3.x | Apache 2.0 | Backend framework |
| Spring Security | Apache 2.0 | AuthN/Z |
| React 18 | MIT | Frontend |
| Tailwind CSS | MIT | Styling |
| TanStack Query | MIT | Frontend data fetching |
| Flyway Community | Apache 2.0 | DB migrations |
| Testcontainers | MIT | Integration tests |
| Playwright | Apache 2.0 | E2E tests |

Full SBOM published per release in CycloneDX format. No copyleft licences (GPL/LGPL/AGPL) in shipped code; this is enforced in CI by a licence scanner.

### 17.2 Third-Party Commercial Components

None in the proposed v1 build. The platform is delivered on stock Azure + OSS so that Northwind has no surprise per-seat or per-transaction licences to absorb at handover.

---

## 18. Technical Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| TR1 | SAP A2X API version drift during build | Medium | Medium | Pin SAP API versions in iFlow; subscribe to SAP release notes; contract tests against SAP mock break the build on shape change. |
| TR2 | Carrier returns non-standard EDI variant | High | Medium | Per-carrier transformation maps in Logic Apps; "unknown segment" fallback writes to inspection queue rather than dropping the message. |
| TR3 | PostgreSQL row-level lock contention on hot tables | Low | High | Order/shipment events written append-only; status materialisation via async projection; lock-monitoring dashboard in place from Sprint 1. |
| TR4 | Redis cache stampede during portal traffic spikes | Medium | Medium | Cache-aside with single-flight (one DB read per key per 30s window) and stale-while-revalidate. |
| TR5 | Long-running ADF migration locks legacy DB | Medium | High | Snapshot extract pattern (read from snapshot view, not live table); migration window scheduled in legacy maintenance window. |
| TR6 | AS2 certificate expiry in production | Medium | High | Calendar-managed rotation; 90/30/7-day alerts; pre-shared backup cert with each carrier. |
| TR7 | Distributed transaction integrity (platform ↔ SAP) | Medium | High | Saga pattern with compensating actions; never use 2PC across the SAP boundary. |
| TR8 | Frontend bundle size growth → portal performance regress | Medium | Low | Bundle-size budget enforced in CI; route-level code splitting from day 1. |
| TR9 | Vendor lock-in concern at handover | Low | Medium | Stock Spring/React/Azure; no Meridian-proprietary frameworks; runbooks written for "a competent Java/Azure engineer" not "an engineer who knows Meridian's way". |

(Commercial and programmatic risks are in SOW §9; this register covers engineering-only.)

---

## 19. Assumptions

These assumptions underpin the design and sizing. If any is invalidated, an impact note will be raised per SOW §4.3.

| # | Assumption |
|---|---|
| TA1 | Northwind's SAP S/4HANA release is on or above the version that supports the listed A2X APIs (2022 release minimum). |
| TA2 | Northwind's carrier EDI gateway supports AS2 inbound and outbound natively; Meridian does not need to deploy AS2 infrastructure. |
| TA3 | Northwind's Entra tenant permits Meridian-registered app registrations and group claims in tokens. |
| TA4 | Peak daily order volume is in the order of 10 000 / day with intra-day burst < 5×; sizing is built around this. If true peak is materially higher, sizing is revised at G2. |
| TA5 | Document storage growth is bounded at ~50 GB / year (BOL + POD + invoice images). Lifecycle tiering to cool tier after 180 days, archive after 2 years. |
| TA6 | Northwind has an Azure Enterprise Agreement; Meridian deploys under Northwind's tenant and subscriptions, not Meridian's. |
| TA7 | Network connectivity between Azure India Central and SAP S/4HANA (whether SAP is on-premises or on SAP RISE) provides < 50ms round trip. If higher, integration p95 budgets need re-baselining. |
| TA8 | Carrier EDI gateway is reachable from Azure India Central without site-to-site VPN; if VPN is required, Northwind provisions it. |
| TA9 | The portal user base at go-live is ≤ 500 distinct enterprise accounts and ≤ 2 000 distinct portal users. Beyond this, sizing review required. |
| TA10 | All API consumers (internal services + future external partners) accept JSON. No SOAP, no legacy XML-RPC. |

---

## Annexure T1 — Component Sizing Worksheet

*Provided as a separate working spreadsheet at architecture sign-off (G2 gate). Captures, per service: target throughput, p95 latency budget, vCPU / memory allocation, replica count (min/max), autoscale rules, and projected monthly Azure cost. Reviewed jointly with Northwind IT before lock-in.*

---

## Annexure T2 — Sequence Diagrams (illustrative)

### T2.1 — Sales Order Ingest (SAP → Platform)

```
SAP S/4HANA          BTP iFlow          API Mgmt          order-service        Service Bus       notification-service
     │                   │                   │                   │                   │                   │
     │  order_created    │                   │                   │                   │                   │
     │──────event───────▶│                   │                   │                   │                   │
     │                   │   POST /orders    │                   │                   │                   │
     │                   │  (canonical JSON) │                   │                   │                   │
     │                   │──────────────────▶│                   │                   │                   │
     │                   │                   │   POST /orders    │                   │                   │
     │                   │                   │──────────────────▶│                   │                   │
     │                   │                   │                   │  persist + emit   │                   │
     │                   │                   │                   │  order.created    │                   │
     │                   │                   │                   │──────────────────▶│                   │
     │                   │                   │      202          │                   │                   │
     │                   │                   │◀──────────────────│                   │                   │
     │                   │      202          │                   │                   │                   │
     │                   │◀──────────────────│                   │                   │                   │
     │                   │                   │                   │                   │  order.created    │
     │                   │                   │                   │                   │──────────────────▶│
     │                   │                   │                   │                   │                   │ send confirmation email
```

### T2.2 — Shipment Booking (Platform → Carrier via EDI)

```
order-service     Service Bus     shipment-service     edi-logicapps     Carrier EDI GW     Carrier
     │                │                 │                   │                  │              │
     │  order.allocated│                 │                  │                  │              │
     │───────────────▶│                  │                  │                  │              │
     │                │  consume         │                  │                  │              │
     │                │─────────────────▶│                  │                  │              │
     │                │                  │  book (book req) │                  │              │
     │                │                  │─────────────────▶│                  │              │
     │                │                  │                  │  EDI 204 (AS2)   │              │
     │                │                  │                  │─────────────────▶│              │
     │                │                  │                  │                  │   forward    │
     │                │                  │                  │                  │─────────────▶│
     │                │                  │                  │                  │  EDI 990     │
     │                │                  │                  │                  │◀─────────────│
     │                │                  │                  │   ack received   │              │
     │                │                  │  shipment.booked │                  │              │
     │                │                  │◀─────────────────│                  │              │
     │                │  shipment.booked │                  │                  │              │
     │                │◀─────────────────│                  │                  │              │
```

---

*End of Technical Proposal — MIS-NWT-2026-TECH-001 Rev 1.0*

*This document is submitted in confidence as the engineering companion to SOW MIS-NWT-2026-SOW-001 in response to RFP NWT-RFP-2026-LOG-014. All contents are proprietary to Meridian InfoSystems Pvt. Ltd. and Northwind Trading Corp.*
