# LIMS Project Blueprint

Status: Proposal for review (docs only). No code changes are part of this document.
Program: lims-build. Work item: w1.
Binding contracts: `lims-requirements@v2` (BRD chapters 1 to 3, including BR-1.12 and CEO decision d12), `lims-nfr@v1` (NFR-1 to NFR-11).
Decisions applied: d15 (CEO), 99.99% availability covers in-cluster failures only; d16 (CEO), 450 peak concurrent users is the only user-count input to sizing. d17 and d18 are resolved (scope and period to be set in Define); the scope in section 3.4 and the period in section 3.5 are approved by the CEO (relay record; d17 and d18 set timing only). d5 (CEO) sets the applicable standards. d14 is superseded by d19. d20 (CEO): the in-cluster failover drill satisfies the NFR-5 proof. d8 (CEO): document control is integrated with the existing system. d9 (CEO): RTO and RPO set in Define and a hosting owner named; shared storage RTO 15 min and RPO 0, and Redis RPO under 1 s, are approved per operator relay (decisions.md entry pending); the Redis RTO and the other values are proposed; the owner is deferred to production by relay. d10 (CEO): site and user breakdown before the load test (w28 in program.json; d10 wording says w27); deferred to production by relay. d11 (CEO): UAT owners named per stage before stage gates; deferred to production by relay. d6 (CEO): w11 stays generic on ASTM and HL7. d13 (contract v2): approved by the CEO (decisions.md, 2026-11-24). d7 (CEO): migration added as a work item. d22 (CEO): backup design deferred to the production environment (D11). d23 (CEO): PMS NFR reuse deferred to a later stage (D3). d19 and d21: see D7. d3 (CEO): Define time is added on top of the program duration. d4 (CEO): Chapter 3 work packages are scheduled w24 to w26 as drafted in P2 and P3; The d4 answer covers w24 to w26; whether it covers w22 and w23 is ambiguous and open to the CEO.

## Table of Contents
1. Scope
2. Assumptions and open decisions
3. Architecture blueprint
4. Reuse of existing healthcare doctypes
5. Compute sizing for 450 concurrent users
6. Availability, RTO and RPO proposal
7. NFR traceability
8. Delivery phases and BR mapping

## 1. Scope

The LIMS covers BRD chapters 1, 2 and 3 on the ERPNext / Frappe Healthcare module. Not yet scheduled: the final patient lab report (no work item), the BR-1.11 reporting step, the NFR-8 security review (the NFR-8 proof), and BR-1.12 (w29; dependency-blocked by D7). The pilot is not in the contracts. The Redis Sentinel placement, NFR-4 event rates and witness voter RTO are also to be raised to the CEO (not yet logged). These are to be raised to the CEO (not yet logged in decisions.md). A sample moves from patient registration to an authorised report with full traceability and an unalterable audit trail.

Build phase only; Operate and Transfer are excluded (requirements v2, Constraints). Not built by this item (docs only): legacy data migration (BR-1.12, item w29; see D7; BR-1.12 remains binding and is gated by D7), and hardware procurement or production hosting choice (excluded by the requirements contract unless the CEO adds them). Section 5 gives a sizing proposal, not a procurement decision. Site-loss disaster recovery is excluded from the 99.99% target and is not sized here (d15).

## 2. Assumptions and open decisions

### 2.1 Assumptions

| ID | Assumption | Value | Source |
|----|-----------|-------|--------|
| A1 | Peak concurrent users | 450 | NFR-1 (binding) |
| A2 | Mean think time between requests per active user | 10 s | Working assumption, to measure |
| A3 | Peak burst factor over the average | 3x | Working assumption |
| A4 | Mean server time per request (form, list, result grid) | 200 ms | Working assumption, to measure in load test |
| A5 | Target CPU utilisation at peak per worker | 60% | Engineering headroom |
| A6 | Lab throughput | 1,500 samples per day | Working assumption |
| A7 | DB footprint per sample (sample, results, versions, audit) | 30 KB | Working assumption |
| A8 | Attachment per sample (report PDF, raw instrument file) | 200 KB | Working assumption |
| A9 | Sizing horizon for database and attachments | 3 years for the main sizing; 5 years shown for the retention case | Working assumption. The planning basis is 3 years, except shared storage, which is sized to an upper bound of all attachments at 5 years (section 5.5); the approved 5-year period covers raw files only (section 3.5) |
| A10 | Unplanned DB failovers per year | 5 | Planning assumption, to measure |
| A11 | Planned DB switchovers per year | 4 | Planning assumption, to measure |

### 2.2 Decisions

CEO approvals in this document come from the operator's relay ("all pending items approved"). d9, d17 and d18 are timing-only entries in the decision log; the content approvals need decisions.md entries.

Blueprint items are numbered D1 to D14. D12 and D13 have no decision-log entry; d12 and d13 are different decisions. The d-numbers in brackets are decision-log IDs, and they differ (for example, D5 is d6).

1. **D1, availability scope (NFR-4). Resolved by d15 (CEO):** the 99.99% target covers in-cluster failures only. Site-loss disaster recovery is excluded and not sized.
2. **D2, named users. Resolved by d16 (CEO):** 450 peak concurrent users is the only user-count input to sizing. No named-user count is used.
3. **D3, PMS and AD/MFA (d12, d23).** AD and MFA: resolved by d12, built fresh in this repository (BR-1.8). PMS NFR reuse: deferred by d23 to a later stage. The program.json w4 note (reuse PMS AD/MFA) is superseded by d12 and should be updated. The program.json w21, w24 and w25 notes are also stale (d8 and d4 resolved). The w1 note (Define open questions go to the CEO) and the w11 note (vendor question) are also stale (d3, d5 and d6 answered). The w27 note is partly stale: shared storage and Redis values are approved (operator relay; decisions.md entry pending); hosting ownership is deferred to production (D8). The contract constraint reads "Reuse PMS NFRs where they exist." Where PMS NFRs live is unverified; the search covered the healthcare app only.
4. **D4, two-person validation (d17).** d17 resolved (scope set in Define before w17). The scope in section 3.4 is approved by the CEO per operator relay (decisions.md entry pending).
5. **D5, instrument list (d6).** Which analysers and their protocols (ASTM or HL7) are in scope for BR-2.1. Partly resolved: d6 settles generic ASTM and HL7 only. The analyser list is open and blocks w11 specifics. For w11, do not name vendor models.
6. **D6, raw file retention (d18). d18 resolved (period set in Define). The 5-year period from result release with a purge step (section 3.5) is approved by the CEO per operator relay (decisions.md entry pending). If the primary NABL 112 check changes the period, the CEO re-decides; the check is pre-release, owner to be named.** Checked against secondary sources only (unverified against the NABL text): the NABL 112 minimum for test reports is 5 years for histopathology and cytopathology and 1 year for other disciplines; secondary summaries state that erasure is required once the purpose is served; this is unverified against the Act text. Confirm the primary NABL text as a pre-release check (owner to be named).
7. **D7, migration data sets (BR-1.12, w29; d7, d19, d21). Split accepted by the CEO (d24): build the import module now on synthetic CSV data whose field names match the target doctypes; no real migration runs until the source system and named data sets are approved at production cutover.** Delivery path set by d19: a legacy data import module. Known so far (operator relay; decisions.md entry pending): the data is CSV, the CSV field names mirror the target doctype fields. Deferred: the source system, the named data sets and the target doctype per data set. Conflict to record: requirements v2 says source systems and data sets are set in Define and approved by the CEO before the migration item is dispatched. A real migration run must not be dispatched until those are named and approved, whatever the development schedule. The reconciliation acceptance (per data set, record counts, sample check, synthetic data only) still applies.
8. **D8, hosting owner (d9). Deferred to production by the operator's relay of the CEO's decision; decisions.md entry pending.** Conflict to record: d9 says name a hosting owner in Define. The owner is not named here. shared storage RTO and RPO and Redis RPO are approved (operator relay; decisions.md entry pending) (section 6.1); Redis RTO and other values are proposed.
9. **D9, UAT owners (d11). Deferred to production by the operator's relay of the CEO's decision; decisions.md entry pending.** Conflict to record: d11 says name UAT owners per stage before the stage gates. Stage gates in this document are not signed off until the owners are named.
10. **D10, site and user breakdown (d10). Deferred to production by the operator's relay of the CEO's decision; decisions.md entry pending.** Conflict to record: d10 says provide the breakdown before the load test. The w28 load-test design uses the 450 peak concurrent figure only (d16) until the breakdown is provided.
11. **D11, backup design (section 6.3; d22).** Stock Frappe backup encryption and retention do not meet the backup proposal. A failed encryption leaves files unencrypted under the -enc name, so each set must be checked. Deferred by d22 (CEO): backup design deferred to the production environment. Backups are out of scope for this blueprint and are not relied on until that design is accepted.
12. **D12, measurement uncertainty (BR-2.5, w16). Recommended; open until the CEO decides and a decisions.md entry exists:** report measurement uncertainty only for quantitative tests that have a reference method and enough QC data. Estimate it from within-lab precision (QC data) and bias (reference material or proficiency results), combine them, and report the expanded uncertainty with coverage factor k = 2. Qualitative tests carry no uncertainty. The lab supplies the test list and the QC data; the method is checked against the accreditation requirement before the w16 build.
13. **D13, patient portal scope (BR-3.3, w26). Recommended; open until the CEO decides and a decisions.md entry exists:** the portal is read-only in this build: patients view released reports and invoices. Online booking is out of scope and is raised as a separate item if wanted, because it needs scheduling integration that this build does not cover.
14. **D14, Chapter 3 schedule (d4).** The d4 answer schedules w24 to w26 as drafted in P2 and P3. Whether it covers BR-3.4 (w22) and BR-3.5 (w23) is ambiguous. Open; CEO to confirm.

## 3. Architecture blueprint

### 3.1 Placement

Build the LIMS as a module inside the existing `healthcare` app, not as a new Frappe app. This is intended to reuse Patient, Healthcare Practitioner (healthcare_practitioner), Lab Test, Sample Collection and Specimen records directly (field fit unverified, section 4), and keeps one deployment unit. It follows the constraint "extend, do not fork the data model without approval".

### 3.2 Layers

- **Data:** MariaDB with Frappe. Proposed new DocTypes (section 4) for worklist, QC run, instrument batch, result version, custody entry (which also records sample location), critical-value notification.
- **Service:** Whitelisted APIs for accessioning, result entry, autoverification, validation and release. Each state change runs in one transaction and writes an audit row.
- **Background:** Frappe queues for instrument interface messages, label printing, report PDF generation and notifications. Instrument file import is recorded as an Instrument Import Batch before queueing (proposed, to confirm); if it is not, NFR-10 for file import is an open conflict. Instrument messages are persisted in the proposed DB-backed Instrument Message Queue DocType before acknowledgement, and Redis only notifies workers for those. Other background jobs are queued in Redis (see section 6.1).
- **Presentation:** Desk forms and list views for lab staff. A barcode-friendly accessioning screen. A portal result view for BR-3.3; read-only scope is recommended, pending CEO confirmation (D13).
- **Integration:** ASTM and HL7 instrument interfaces with error logging and a persistent queue (NFR-10, BR-2.1). AD login and MFA are built fresh in this repository and are not reused from PMS (BR-1.8, decision d12).

### 3.3 Result lifecycle

1. Ordered: Lab Test created from an order.
2. Collected: Sample Collection recorded, barcode assigned.
3. Received: sample accessioned or rejected with reason; chain-of-custody entry written (BR-1.3, BR-1.5).
4. In process: result entered manually or by instrument; reflex and add-on tests triggered (BR-1.6, BR-2.4).
5. Autoverified or validated: delta and panic checks (BR-2.2); second-person validation where D4 requires it.
6. Authorised: consultant e-signature (BR-2.6). Critical values alert the responsible staff with acknowledgement recorded (BR-1.7).
7. Released: result visible to the ordering practitioner and, when enabled, the patient portal.
8. Amended: any change after authorisation creates a new result version with reason, user and timestamp (BR-2.8, NFR-9).

Rules that must hold in code, with the phase that delivers each:

- Released results are immutable. Only a new version can change them. Phase 2.
- Audit rows cannot be edited or deleted by any role (BR-1.4). Phase 2 (w18); phase 1 delivers the activity log only (w5).
- Failing internal QC flags or blocks the related patient results (BR-2.3). Phase 3.
- Each result stores the reagent lot used (BR-3.4). Phase 4.

### 3.4 Two-person validation scope (approved by CEO per relay; d17 sets timing; decisions.md entry pending)

Approved by the CEO (operator relay; decisions.md entry pending); set in Define before w17. Two-person validation means a second user with a new Lab Validator role (proposed; not in the repository today) checks and approves the action. The first user cannot approve their own entry.

Results that always need a second person before release:

- Critical or panic values (BR-1.7, BR-2.2).
- Results that fail delta checks, or that autoverification could not pass (BR-2.2).
- Any result entered while the related QC run has failed and was then recovered (BR-2.3).
- Any change to an authorised result (BR-2.8, NFR-9). The change also needs a reason.
- Final histopathology reports before e-signature (BR-2.6).

Phasing: sample-rejection reversal (BR-1.3) and discount approval (BR-1.9) start in phase 1. Reference-range changes and amendment checks (BR-2.8) start in phase 2. Critical values (BR-1.7), panic checks (BR-2.2), delta checks, autoverification and QC-recovery checks start in phase 3, after autoverification (w12). Changes to QC rule sets and autoverification rules start in phase 3 (w12, w13). Histopathology authorisation stays disabled until phase 4 (section 8).

Results that are configurable per test or panel, with the default set to off:

- All other manually entered results. Proposed: the flag is a new field on `lab_test_template` (or the panel template), so the lab can turn it on per test.

Results that do not need a second person:

- Results that pass autoverification with no delta or panic flag. Autoverification is the check in that case.

Actions that need a second person:

- Changes to reference ranges, QC rule sets and autoverification rules. These are masters that change how results are judged.
- Discount slabs above the approved threshold (BR-1.9).
- Reversing a sample rejection after it has been recorded (BR-1.3).

Actions that never need a second person, because they are recorded in the audit trail only:

- Sample accept or reject with a reason (BR-1.3).
- Chain-of-custody hand-overs (BR-1.5).

Deleting or cancelling an authorised report is not allowed for any role (BR-1.4, NFR-9). A correction goes through the amendment route above.

### 3.5 Raw instrument file retention (approved by CEO per relay; d18 sets timing; decisions.md entry pending)

Approved by the CEO (operator relay; decisions.md entry pending). Raw ASTM and HL7 instrument files are kept, per d18, under the standards set by d5.

- **Approved period:** 5 years from result release, for each raw file linked to a released result. After that, the file is reviewed and purged from live storage with an audit row. Backup copies follow the retention decided in D11, binlog retention (proposed 7 days) is deferred per d22, see section 6.3.
- **Basis:** NABL 112, from secondary sources (a Scribd copy of NABL Standards 112 and a SlideShare gap-analysis deck, both unverified) and not the NABL text: test reports need 5 years for histopathology and cytopathology and 1 year for other disciplines. The 5-year approved period covers the longest of these for all raw files, as a conservative lab choice. ISO 15189 leaves the period to the lab and national rules, per a secondary source that is not verified. d5 names ISO 15189, NABL and data-protection law as applicable. Confirm against the primary NABL 112 text as a pre-release check (owner to be named).
- **Data-protection limit:** the DPDP Act (s. 8(7); summaries at https://www.mondaq.com/data-retention-and-deletion-under-the-dpdp-act-how-long-can-businesses-retain-personal-data-in-india/1844062 and https://fpf.org/blog/the-digital-personal-data-protection-act-of-india-explained/; Act text at https://www.indiacode.nic.in/bitstream/123456789/22037/2/a2023-22.pdf, s. 8(7) not yet extracted) secondary summaries state that erasure is required once the purpose is served, unless retention is necessary to comply with another law; this is unverified against the Act text. That exception is the possible basis for a legal-minimum period. Check the Act's commencement and rules before relying on it. Raw files are kept for the 5-year period, then purged. This is not an open-ended hold. Backup copies can keep a raw file for up to about a year after purge (deferred per d22, see D11). Files still under legal hold are excluded from purge (proposed; not part of the CEO approval).
- **Scope:** raw instrument files only. Results and audit rows have no retention stated yet; this is open and not covered here.
- **Sizing effect:** section 5.4 uses 3 years for sizing (A9). At 5 years the attachment volume is an upper bound of about 0.55 TB, including report PDFs; the raw-only size is to be measured. It is a separate row in the storage table.

## 4. Reuse of existing healthcare doctypes

These doctypes exist under `healthcare/healthcare/doctype/`; field fit against section 3.3 is not yet verified, so check before reuse:

| Existing doctype | Use in LIMS |
|------------------|-------------|
| `lab_test` | Ordered test and result header |
| `lab_test_template` | Test definition, reference ranges, units. Measurement uncertainty (BR-2.5) is a new field to add here, not an existing one. |
| `lab_test_group_template` | Panel definition (BR-1.10) |
| `lab_test_sample` | Sample link per test |
| `sample_collection` | Collection event from the patient |
| `sample_type` | Container and specimen type master |
| `specimen` | Specimen record that moves through the lab |
| `observation_sample_collection` | Observation-level sample link |
| `lab_prescription` | Order source |
| `healthcare_practitioner` | Ordering and validating practitioner records |
| `patient` | Patient master for registration and sample linkage |

Checked, not yet reused: observation_reference_range already has critical fields (critical_section, critical_boolean_value, critical_from, critical_ratio, critical_select) relevant to BR-1.7 and BR-2.2; ERPNext Batch and Serial and Batch Bundle are candidates for lot traceability (BR-3.4); ERPNext Pricing Rule is a candidate for discount slabs (BR-1.9). Existence of these ERPNext doctypes is checked; field fit is not verified. Reuse decision pending.

New DocTypes proposed: Lab Worklist, Lab Worklist Item, QC Run, QC Rule Set, Instrument Import Batch, Instrument Message Queue, Lab Result Version, Chain of Custody Entry, Critical Value Notification, Discount Slab, Reagent Lot Link, Referral, Risk Register Entry. Discount Slab and Reagent Lot Link apply only if the ERPNext reuse (Pricing Rule; Batch and Serial and Batch Bundle) is rejected.

Before writing any new DocTypes, check each reused DocType's fields against section 3.3. A gap becomes a field on the existing DocType, not a parallel record.

## 5. Compute sizing for 450 concurrent users

### 5.1 Request rate

- Peak concurrent users: 450 (A1).
- Average request rate at peak: 450 / 10 s = 45 requests per second (A2).
- Design peak with burst factor: 45 x 3 = 135 requests per second (A3).

### 5.2 Web workers (gunicorn)

- One sync worker: 1 / 0.2 s = 5 requests per second at full utilisation (A4).
- At 60% utilisation (A5): 3 requests per second per worker.
- Workers needed for 135 requests per second: 135 / 3 = 45.
- Proposal: 7 app nodes, 8 workers each, 56 workers.
- N+1 check: losing one node leaves 48 workers, or 144 requests per second at 60% utilisation. That covers the 135 design peak.

### 5.3 Database connections

- One connection per gunicorn worker: 56.
- Background workers and scheduler: about 12.
- Admin, reporting and maintenance sessions: about 12.
- Planned total: about 80. Set `max_connections` to 250.

### 5.4 Storage

| Item | Calculation | Result |
|------|-------------|--------|
| Database growth per day | 1,500 x 30 KB (A6, A7) | 45 MB per day |
| Database growth per year | 45 MB x 365 | about 16 GB per year |
| Database over retention, with index and overhead x2 | 16 GB x 3 x 2 | about 100 GB |
| Attachment growth per day | 1,500 x 200 KB (A6, A8) | 300 MB per day |
| Attachments over the 3-year sizing horizon (A9) | 300 MB x 365 x 3 | about 330 GB |
| Attachments with 50% headroom | 330 GB x 1.5 | about 500 GB usable, 1 TB replicated capacity |
| Attachments at 5 years, all attachments including report PDFs (upper bound; the approval covers raw files only, section 3.5) | 300 MB x 365 x 5 (full A8 size, includes report PDFs) | about 0.55 TB, about 0.82 TB usable with 50% headroom. Raw-instrument-file-only size is smaller and must be measured. |

Storage is volume-based and does not change with concurrency. Replicated capacity assumes a replication factor of 2 (usable x 2).

Backups (section 6.3; deferred per d22, indicative only) add about 14 TB before compression for attachments alone: 42 copies (30 daily, 12 monthly) at the 330 GB 3-year size. The backup command also includes the database, about 100 GB per copy, which adds about 4.2 TB, giving about 18 TB at 3 years. At the 5-year attachment size the attachment part alone is about 23 TB (42 copies at 0.55 TB), about 30 TB with the database (about 160 GB per copy at 5 years). Backup storage needs its own row in the node table; its size is open until D11 is decided.

Audit rows are append-only and have no stated retention (section 3.5), so audit growth in the database is not capped at 3 years. This is open.

### 5.5 Node proposal

| Role | Count | vCPU | RAM | Disk | Notes |
|------|-------|------|-----|------|-------|
| Load balancer (HA pair, active-passive) | 2 | 2 | 4 GB | 20 GB | Virtual IP. TLS termination (NFR-8). |
| App and web (gunicorn) | 7 | 8 | 16 GB | 50 GB | 8 workers each. N+1 covers the 135 requests per second peak. |
| Background workers and scheduler | 2 | 8 | 16 GB | 50 GB | Two nodes for queue redundancy. Scheduler runs on one node only, with failover. |
| DB primary (MariaDB) | 1 | 16 | 64 GB | 500 GB NVMe | InnoDB buffer pool about 40 GB against a 100 GB working set. |
| DB replicas (semi-synchronous) | 2 | 16 | 64 GB | 500 GB NVMe | Same spec as primary so failover does not need a resize. One replica also serves read-only reports. |
| Failover manager (active-passive) | 2 | 2 | 4 GB | 20 GB | Automatic primary promotion. Tool not yet selected; candidates to evaluate in plan review. |
| Witness voter | 1 | 1 | 2 GB | 10 GB | Third vote for failover decisions. A witness vote reduces split-brain risk; prevention also needs fencing and placement in a third failure domain. To verify when the failover tool is selected. |
| Redis (cache and queue) | 2 | 4 | 8 GB | 20 GB | Primary and replica with Sentinel for failover. Sentinel quorum needs three Sentinel processes. The witness voter is the only third location in this design, and it is shared with DB failover, so one witness host loss removes the third vote from both. Placement is a decision for plan review: add a host, or give Sentinel a separate third location. |
| Shared file storage | HA | - | - | 1.6 TB replicated (upper bound, all attachments at 5 years including report PDFs) | Replicated object storage or HA NFS. Shared by app and worker nodes. The approval covers raw files only; raw-only size to measure. |
| Backup storage | separate from DB host | - | - | about 18 TB at 3 years; about 30 TB at 5 years (indicative; backups deferred per d22; the 3-year figure is an interim floor until the 5-year basis is adopted) | Sized in section 5.4. |
| Load-test environment | on demand | as production | as production | - | Production-sized, used for the NFR-1 test. Otherwise scaled down. |

Total always-on production nodes: 19 (2 load balancer, 7 app, 2 worker, 3 DB, 2 failover manager, 1 witness, 2 Redis), plus shared storage and backup storage. The 19 excludes a third Sentinel host; adding one makes 20. Redis automatic failover is blocked until a third Sentinel location is chosen; the Redis RTO is conditional on that placement. Procurement and hosting are CEO decisions, not decided here. The Redis Sentinel placement, NFR-4 event rates and witness voter RTO are to be raised to the CEO (not yet logged).

Considered: a synchronous multi-master cluster (Galera). Not chosen provisionally. Hypothesis, not verified: Frappe's series and counter rows are hot rows that could cause certification conflicts under concurrent writes. Test or cite this in plan review before relying on it.

### 5.6 Sizing validation

1. Load test at 135 requests per second for 30 minutes on production-sized nodes, using synthetic data at production-like volumes (NFR v1 Constraints; the 135 requests per second and 30-minute duration are blueprint proposals). Approved by the CEO (relay): P95 page response under 1.5 s and app CPU under 70%. The targets are approved in lims-nfr@v1 (decisions.md): registration save at or below 1 s (NFR-2) and every result at or below 500 ms (NFR-3). Not yet approved: the statistic for registration save (NFR-2) and every result at or below 500 ms (NFR-3 contract value; the 500-result batch statistic is proposed, see section 7); the statistic for each is proposed and to be confirmed, no connection exhaustion, and a sustained 450-concurrent run at 45 requests per second.
2. Replace A4 with measured staging server time. The 7-node plan keeps N+1 coverage of 135 requests per second only while mean server time is at or below about 213 ms (48 workers x 0.6 / 135 per second). Above that, re-size. At 300 ms, 10 app nodes give 9 x 8 = 72 N+1 workers, or 144 requests per second.
3. Replace A6 and A8 with the first 90 days of production counts. Re-run section 5.4.

## 6. Availability, RTO and RPO proposal

NFR-4 target: 99.99% uptime, about 52.6 minutes of downtime per year, for in-cluster failures (d15).

### 6.1 In-cluster high availability

Status: shared storage RTO 15 min and RPO 0, and Redis RPO under 1 s, are approved by the CEO per relay (d9 sets timing; decisions.md entry pending); the Redis RTO and the other values are proposed.

| Failure | Mechanism | Recovery target (RTO) | RPO | Downtime effect |
|---------|-----------|-----------------------|-----|-----------------|
| One app node | Load balancer removes it | Immediate | n/a | None |
| One worker node | Queues served by the other node | Immediate | n/a | None |
| DB primary | Semi-sync replica promoted automatically by the failover tool (not yet selected, section 5.5) | RTO 2 min target | RPO 0 target, verified in the failover drill; voided if semi-sync falls back to asynchronous (alert on fallback) | Writes pause up to the RTO |
| Redis primary | Sentinel promotes replica | RTO 1 min target, conditional on Sentinel placement | RPO under 1 s approved by the CEO per relay under d9 (decisions.md entry pending); to measure in the failover drill | Downtime about 1 min; cache rebuilt; see the note below |
| Load balancer node | Active-passive pair takes over the virtual IP | RTO 30 s target | n/a | Connections in flight may retry |
| Failover manager node | Second manager takes over | RTO 1 min target | n/a | None |
| Witness voter (proposed) | Third vote for DB failover (Redis Sentinel placement open) | To set in plan review (open) | n/a | Automatic failover blocked while quorum is lost; the surviving primary keeps serving |
| Scheduler node | Scheduler restarts on the other worker node | RTO 5 min target | Enqueued jobs with no database record may be lost (see note); not measured | Scheduled jobs delayed |
| Shared file storage | Replicated storage with synchronous replication (values in the RTO and RPO columns, approved by the CEO per relay under d9 (decisions.md entry pending)) | RTO 15 min approved (operator relay) | RPO 0 approved (operator relay) | Attachments unavailable until recovered |
| Planned maintenance | Rolling restart node by node; planned DB switchover for primary restart | n/a (planned) | n/a | Zero for app and worker nodes; writes pause up to 2 min during a primary switchover |

Note on Redis: critical-value notifications are written to the proposed Critical Value Notification DocType before dispatch and re-dispatched on recovery until acknowledged. Jobs with no database record (label printing, report PDF, notifications; instrument file import is recorded as an Instrument Import Batch before queueing, to confirm; if not, NFR-10 for file import is an open conflict) can be lost at a failover; the proposed impact is a reprint or resend, for CEO confirmation.

Budget check: five unplanned DB failovers plus four planned DB switchovers per year, at 2 minutes each, is 18 minutes. That leaves about 34.6 minutes of the yearly budget for other in-cluster events. Shared storage (15 min RTO) and Redis (1 min RTO) events are not counted in the 18 minutes. Load balancer events (30 s each) are also not counted here. Their event rates are not yet assumed; they must be added to the budget before NFR-4 is signed off. The 34.6 minutes is not yet allocated to those events. At 15 minutes each, the remainder allows about two shared-storage events. NFR-4 cannot be signed off until Redis, load balancer and shared-storage event rates are assumed.

### 6.2 Site loss (excluded)

Per decision d15, site-loss disaster recovery is excluded from the 99.99% target and is not sized in this document. No site-loss RTO or RPO tiers are proposed here. The site-loss DR drill is out of scope for this Define item and deferred to a later item (d20). If the CEO later asks for site-loss recovery, it is a new decision and a new item.

### 6.3 Data protection within the platform

These measures protect against data loss and corruption. They are not a site-DR design.

| Requirement | Mechanism |
|-------------|-----------|
| Point-in-time recovery | Deferred per d22 (needs a base backup): binary logs kept for 7 days (proposed) on the DB cluster. |
| Daily full backup | Deferred to production per d22 (D11); out of scope for this blueprint. Stock Frappe backups do not meet the backup proposal as written (the backup requirement is a proposal in this document, not in requirements v2 or NFR v1). Encryption applies only when the encrypt_backup system setting is on. If encryption fails, Frappe prints an error and stores the files unencrypted under a name that still carries the -enc suffix (backups.py lines 248-251), so the backup job must check its output and fail instead. The site config copy also carries the -enc name but is never encrypted (backups.py line 213), so the name is not a check for it. Encryption covers the database and file archives but not the site config copy, and the backup key is kept in site_config.json, which is copied in plaintext into every backup set (frappe/utils/backups.py: backup_encryption, copy_site_config). Stock cleanup also deletes local backups older than 23 hours by default (delete_temp_backups; keep_backups_for_hours overrides it). A design is needed before backups are relied on: the key held only in an external secret store, a copy of site config that holds no key, and an external retention job for 30 daily and 12 monthly copies. |
| Restore test | Deferred per d22: quarterly restore of the latest backup to a staging environment, once the backup design is accepted. Record restore time. Confirm the encrypt_backup setting is on, that each backup set decrypts with the key from the secret store, and that a failed encryption step fails the backup job and raises an alert, and that the site config copy holds no key (after D11). |
| Failover drill | Quarterly in-cluster failover drill covering the DB primary, Redis and shared storage. Record measured RTO and RPO. This is the NFR-5 proof for the HA cluster, accepted by CEO decision d20. The site-loss DR drill is out of scope for this item. |

## 7. NFR traceability

Each NFR is listed with the design element that meets it and the test that proves it.

| NFR | Target | Design element | Proof (planned test) |
|-----|--------|----------------|----------------------|
| NFR-1 | 450 peak concurrent users | Section 5: 7 app nodes, N+1 at 135 requests per second | Load test, section 5.6 |
| NFR-2 | Registration and save <= 1 s | Single-transaction save, indexed lookups on patient and sample IDs | Performance test on registration and accessioning |
| NFR-3 | Autoverification <= 500 ms per result | Rule engine runs in the save transaction with precompiled rules | Performance test: every result at or below 500 ms (NFR-3); statistic (maximum across a 500-result batch) proposed, pending CEO confirmation (section 5.6) |
| NFR-4 | 99.99% uptime (in-cluster, d15) | Section 6.1 in-cluster HA; site loss excluded | Failover test, uptime monitoring |
| NFR-5 | RTO and RPO for HA cluster | Section 6.1 (RTO 2 min proposed, RPO 0 target for DB; Redis RTO 1 min (proposed); Redis RPO under 1 s (approved under d9 per operator relay (decisions.md entry pending); measure in the drill)). Shared storage RTO 15 min and RPO 0 are approved by the CEO per relay under d9 (decisions.md entry pending) (section 6.1), as NFR-5 requires them set in Define. | In-cluster failover drill, section 6.3. The NFR contract names a DR drill as proof. The in-cluster failover drill satisfies the proof (CEO decision d20). The site-loss DR drill is out of scope for this item and deferred to a later item. |
| NFR-6 | AD integration, mandatory MFA | AD login and MFA built fresh in this repository (BR-1.8, d12); MFA enforced on all roles | Security test |
| NFR-7 | Strict RBAC on every DocType | Frappe permission rules per DocType; no `ignore_permissions` on user paths | Permission test matrix, including denial cases |
| NFR-8 | HTTPS/TLS; PII/PHI handled | TLS at the load balancer and on internal links (app to DB and Redis); any internal link without TLS is a conflict raised to the CEO, not an exception; field-level masking on PII in logs | Security review |
| NFR-9 | Unalterable audit with reason | Audit rows append-only; reason mandatory on amendment | Audit compliance check |
| NFR-10 | Error logging and queuing on ASTM/HL7 | Persistent Instrument Message Queue with retry and error log | Fault-injection test |
| NFR-11 | Code documentation for custom logic | Docstrings on rule, interface and engine modules | Documentation review |

## 8. Delivery phases and BR mapping

The phase order is this document's proposal. The BRD does not set an order. wNN in this section is a work-item ID from the program plan, not a week number. Routing: every item marked 'to be raised to the CEO' is not yet logged in decisions.md, and includes the witness RTO, the Redis Sentinel placement and BR-1.12. NFR-11 (code documentation for custom logic) applies to phases 1 to 4 and 7, where custom logic is built, and is named in those exits.

| Phase | Content | BR / NFR covered | Schedule | Exit criterion |
|-------|---------|-----------------|----------|----------------|
| 0. Blueprint | This document; D1 and D2 resolved; D3: AD and MFA resolved (d12); PMS NFR reuse deferred (d23); D7 deferred to production (relay); D4 approved per relay, entry pending; D5 partly decided (analyser list open); D6 approved per relay, entry pending; NABL 112 pre-release check open; shared storage RTO and RPO and Redis RPO approved (operator relay; decisions.md entry pending); Redis RTO and other values proposed (section 6); hosting owner (D8 (d9)) and UAT owners (D9 (d11)) deferred to production per operator relay; conflicts with d9, d10 and d11 recorded; site and user breakdown (D10) deferred, load test uses 450 only | NFR-1, NFR-4, NFR-5 | Define (w1), added on top of the 24 weeks (d3) | Sign-off on sections 2, 3.4, 3.5, 5 and 6, Define sign-off is withheld while the UAT owners (d11) and the hosting owner (d9) are unnamed; open items are listed in sections 2.2 and 8; CEO to decide whether to name the UAT owners now or re-scope d11 (3.4 and 3.5 approved, as recorded in section 2.2); NFR-1 sizing basis (d16), NFR-4 scope (d15) and NFR-5 values for shared storage and the Redis RPO approved; every other NFR-5 value approved or recorded as a blocker for w27; hosting owner (D8 (d9)) and UAT owners (D9 (d11)) deferred to production, conflicts recorded; D7 source systems approved by the CEO, or recorded as a blocker for w29 |
| 1. Core and identity | RBAC, unique patient and sample IDs with labels, accessioning, chain of custody, masters, AD and MFA (built fresh), discount slabs | BR-1.1, 1.2, 1.3, 1.4, 1.5, 1.8, 1.9, 1.10, 1.11 (registration through accessioning, w6 and w7; initial invoice, w9); NFR-2, 6, 7, 8, 9, 11 | w2 to w7, w9, w10 (w4 AD and MFA; w5 audit; w10 PII and save time; deps: w2 on w1, w3 on w1, w4 on w3, w5 on w3, w6 on w2 and w3, w7 on w6, w9 on w6, w10 on w4 and w6) | BR-1.1 users, roles, menus and strict RBAC on every DocType, BR-1.2 unique, immutable patient and sample IDs printed on labels and reports, BR-1.3 accept or reject with a recorded reason, routed to department and test, BR-1.4 activity audit of who, what and when (w5; the contract's user, time, action and reason for change, and the edit and delete denial, are phase 2, w18), BR-1.5 chain of custody, every hand-over recorded, with current location and holder visible, BR-1.8 AD login, mandatory MFA, HTTPS/TLS and PII/PHI protection, BR-1.9 configurable discount slabs on invoices, visible and auditable, and BR-1.10 (Core masters: Test, Panel, Specimen; initial inventory setup), demonstrated on synthetic data; second-person test for sample-rejection reversal and discount approval (section 3.4); BR-1.11 registration through accessioning (w6, w7) and initial invoice (w9) demonstrated; permission matrix passes (NFR-7); activity log (part of NFR-9); AD and MFA security test (NFR-6); registration and save <= 1 s (NFR-2); partial NFR-8 check (PII and TLS); the NFR-8 security review proof is unscheduled (Phase 6); NFR-11 documentation review |
| 2. Results and reporting | Test processing and core result entry (units, ranges, abnormal flags), reflex and add-on tests, measurement uncertainty, reason-for-change audit on result edits, reports (Visit Charge Register, TAT, Sales) | BR-1.4, 1.6, 1.11, 2.4, 2.5, 2.8 (BR-1.11 processing only; reporting blocked, see exit); NFR-9, 11 | w8, w14, w16, w18, w19 (deps: w8 on w7; w14 on w8; w16 on w8; w18 on w5 and w8; w19 on w8 and w9) | BR-1.4 append-only audit test denying edit and delete for every role, with reason for change (w18), BR-1.6 result entry with units, reference ranges and abnormal flags, BR-1.11 processing and result (w8); the reporting step is blocked by the unscheduled final patient lab report (w19 covers TAT, sales and visit charge reports only); the final patient lab report is unscheduled (to be raised to the CEO; not yet logged); BR-2.4 reflex and add-on tests, BR-2.5 measurement uncertainty per test shown on reports where required (tests and method recommended, pending CEO confirmation, D12), BR-2.8 any change to an authorised result needs a reason and is kept in the audit trail, all demonstrated on synthetic data; second-person check tested for amendments and reference-range changes (section 3.4); Reports required by the contract (Visit Charge Register, TAT and Sales) demonstrated; result lifecycle tests pass; NFR-7 permission matrix with denial cases for the new doctypes; NFR-9 audit compliance check, reason mandatory on amendment; NFR-11 documentation review |
| 3. Instruments, autoverification and alerts | Critical-value alerts with acknowledgement, consultant e-signature authorisation, ASTM and HL7 interfaces with queue, autoverification with delta and panic checks, Westgard QC (generic ASTM and HL7 only; analyser-specific work blocked by D5) | BR-1.7, 2.1, 2.2, 2.3, 2.6; NFR-3, 10, 11 | w11, w12, w13, w15, w17 (deps: w11, w12, w13 on w8; w15 on w12; w17 on w12 and w15) | BR-1.7 panic and critical values alert the responsible staff, acknowledgement recorded, BR-2.1 bidirectional ASTM/HL7 interfaces with error logging and queuing, BR-2.2 autoverification with delta and panic checks, BR-2.3 internal QC with Westgard rules and failing QC flags or blocks related patient results, BR-2.6 consultant e-signature authorisation with critical value alerts visible at review, all demonstrated on synthetic data; second-person checks for critical values, panic values, delta failures and QC recovery tested (section 3.4); masters change control (reference ranges, QC and autoverification rules) with second-person approval tested; NFR-3 every result at or below 500 ms, measured across a 500-result batch (statistic pending CEO confirmation, section 5.6); NFR-7 permission matrix with denial cases for the new doctypes; NFR-10 fault-injection test passes; NFR-11 documentation review |
| 4. Quality, inventory and extended services | Document control integrated with the existing document control system, reagent lot traceability, quality indicators dashboard, risk management, referrals, patient portal, histopathology report | BR-2.7, 3.1, 3.2, 3.3, 3.4, 3.5; NFR-7, 9, 11 | w20 (HistoPathology, after w17); w21 (BR-2.7; dep w17); BR-3.1 to BR-3.3 in w24 to w26 (d4); BR-3.4 in w22 and BR-3.5 in w23 are not clearly covered by the d4 answer (open to the CEO) (program.json; deps: w22 on w2, w8; w23 on w13, w19; w24 on w3; w25 on w9, w17; w26 on w4, w17) | BR-2.7 document control integration for SOPs, forms and report templates with the existing system; BR-3.1 risk management module; BR-3.2 referral management; BR-3.3 patient portal (scope recommended, pending CEO confirmation, D13); BR-3.4 each result links to the reagent lot used; BR-3.5 quality indicators (turnaround, rejection rate, critical-value reporting time, QC failures), each demonstrated end to end on synthetic data; HistoPathology report (w20; contract Reports required) demonstrated; the final patient lab report has no program.json item and is to be raised to the CEO as unscheduled (not yet logged); dashboard figures for turnaround, rejection rate, critical-value reporting time and QC failures reconciled with source data; SOPs, forms and report templates demonstrated under document control; portal permissions tested; histopathology authorisation enabled only after second-person validation is tested (section 3.4); NFR-7 permission matrix with portal and risk denial cases; NFR-9 audit trail for the new modules; NFR-11 documentation review |
| 5. Availability | HA cluster, failover manager, replication, in-cluster failover drill (backups deferred per d22) | NFR-4, NFR-5 | w27 (deps w4, w8) | Failover test meets the proposed 2 min DB RTO and 0 RPO target; shared storage failover test meets the approved RTO and RPO (operator relay; decisions.md entry pending), and the Redis failover test meets the approved RPO (section 6.1); backup and restore test deferred per d22; 99.99% uptime through the final HA setup; uptime monitoring instrumented and verified in staging; 99.99% demonstrated over the period the CEO sets (to be raised to the CEO; not yet logged); NFR-5 in-cluster failover drill (d20); w27 blockers (blueprint, with the program.json w27 note): NFR-4 event rates for Redis, load balancer and shared storage (not assumed), Redis third Sentinel placement (not chosen), hosting owner (D8, deferred), UAT owners (D9 (d11), deferred; stage gate not signed off until named), witness RTO (open), proposed RTOs awaiting CEO approval |
| 6. Load, security and go-live | NFR-1 load test, security review, pilot with one lab section (proposed; not in the contracts; unscheduled) | NFR-1, NFR-2, NFR-3, NFR-8 | w28 (load test; deps w10, w11, w12, w17, w19, w27); security review and pilot unscheduled, to be raised to the CEO (not yet logged) | NFR-1 load test on synthetic data (section 5.6), CEO-approved thresholds (operator relay; decisions.md entry pending): P95 page response under 1.5 s and app CPU under 70%; the load test uses the 450 peak figure only until the D10 breakdown is provided; NFR-2 (registration save at or below 1 s) and NFR-3 (every result at or below 500 ms) statistics pending CEO confirmation, measured in the load run; NFR-8 security review (unscheduled, to be raised to the CEO; not yet logged; pass criterion: HTTPS/TLS and PII/PHI handled securely); all NFR proofs in section 7 recorded in the PR descriptions |
| 7. Legacy data import (unscheduled, blocked by D7) | Legacy data import module with per-data-set reconciliation | BR-1.12; NFR-11 | w29 (deps w2, w6); unscheduled until the D7 data sets are named and approved | Reconciliation per data set with record counts and a sample check in the PR description, synthetic data only; NFR-7 permission matrix with denial cases; NFR-11 documentation review |

BR-1.12 is a separate item, w29, gated by the open source-system question (D7), and is mapped to phase 7 in the table above. Its acceptance (requirements v2): reconciliation per data set, with record counts and a sample check in the PR description, synthetic data only.

Each phase is delivered as one or more pull requests. Each PR is one change and touches only its own item's files (requirements v2, Constraints).
