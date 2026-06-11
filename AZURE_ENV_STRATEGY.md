# Azure Environment Strategy: UAT & QA Expansion

**Date:** June 6, 2026  
**Status:** Draft for Manager & Architect Review  
**Subject:** Infrastructure expansion to support UAT/QA and Performance/Stress Testing.

---

## 1. Executive Summary
The goal is to expand the existing **DEV** infrastructure to include **QA** (Quality Assurance) and **UAT** (User Acceptance Testing) environments. A critical requirement is the ability to perform **stress and performance testing** to ensure production readiness.

We have two primary paths: **Cost-Optimized (Shared)** and **Performance-Isolated (Dedicated)**. This document outlines the trade-offs between them and proposes a **Hybrid Model**.

---

## 2. Comparison of Approaches

### A. Database (PostgreSQL Flexible Server)
| Feature | Cost-Optimized (Shared) | Performance-Ready (Dedicated) |
| :--- | :--- | :--- |
| **Architecture** | Add `db-uat` and `db-qa` to existing DEV server. | Dedicated server per environment. |
| **Stress Testing** | **Unreliable.** Load on QA impacts DEV/UAT stability. | **Accurate.** 100% isolation; no cross-impact. |
| **Resource Tier** | Limited to "Burstable" (B-Series) credits. | "General Purpose" (consistent IOPS). |
| **Risk** | DB "Credit Exhaustion" causes false performance fails. | Clean performance metrics. |

### B. Compute (Azure Container Apps)
| Feature | Cost-Optimized | Performance-Ready |
| :--- | :--- | :--- |
| **Scaling** | Scale to Zero (pay nothing when idle). | Minimum Replicas (always active). |
| **Latentcy** | High "Cold Start" delay on first request. | Instant response; consistent load balancing. |
| **Isolation** | Shared Environment (CAE). | Dedicated Environment (optional). |

---

## 3. The Performance Testing Problem
Performing stress tests on a **Shared Burstable Server** (our current DEV setup) is not recommended because:
1. **CPU Credits:** Once the test exhausts the server's "burst credits," performance throttles to ~10%, leading to inaccurate "Slow API" reports.
2. **Noisy Neighbor:** A stress test on QA will likely crash the Database for the DEV and UAT teams simultaneously.
3. **IOPS Limits:** Shared disks will bottleneck during high-volume write/read tests.

---

## 4. Proposed "Hybrid" Model (Recommended)

To balance monthly budget with technical rigor, we propose the following tiering:

| Environment | Purpose | Infrastructure Strategy |
| :--- | :--- | :--- |
| **DEV** | Coding & Sandbox | Shared Burstable Server (Existing). |
| **QA** | Functional Testing | Shared DB on DEV server; Dedicated Container App. |
| **UAT** | **Perf/Stress Testing** | **Dedicated Server & Dedicated Container App.** |

### Key Advantages of UAT Isolation:
* **Elastic Performance:** We can scale the UAT Database up to 8+ vCores *only during the test window* and scale it back down to 1 vCore afterward to save costs.
* **Baseline Accuracy:** Metrics collected in UAT will directly translate to Production expectations.
* **Zero Downtime for Devs:** Performance engineers can "break" the UAT environment without stopping the developers from working in DEV/QA.

---

## 5. Cost Estimates (Approximate)

| Setup Type | Est. Monthly Cost | Reliability for Perf-Testing |
| :--- | :--- | :--- |
| **Fully Shared** | ~$80 - $120 | Low (Inaccurate results) |
| **Hybrid (Recommended)** | ~$180 - $250 | High (UAT matches Prod) |
| **Fully Isolated** | ~$350 - $500 | Very High (Gold Standard) |

---

## 6. Next Steps for Implementation
Upon approval of the **Hybrid Model**, the following steps will be taken:
1. **Infrastructure as Code (IaC):** Create Bicep/Terraform templates to automate environment creation.
2. **CI/CD Update:** Modify GitHub Actions to support branch-based deployments (`develop` -> DEV, `release` -> QA/UAT, `main` -> PROD).
3. **Data Scrubbing:** Implement a script to periodically sync "Sanitized" (PII-removed) data from PROD to UAT for realistic testing.

---
**Reviewer Comments:**
*Architect:* ________________________
*Manager:* ________________________
