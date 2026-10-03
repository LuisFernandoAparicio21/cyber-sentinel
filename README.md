<div align="center">

# 🛡️ Cyber Sentinel

**Real-time security monitoring for AI workloads on SAP BTP — ingests SAP's LLM-alert events, stores them in SAP HANA Cloud and flags anomalies for a SOC dashboard.**

<br>

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white)
![SAP BTP](https://img.shields.io/badge/SAP_BTP-Cloud_Foundry-0FAAFF?style=for-the-badge&logo=sap&logoColor=white)
![SAP HANA Cloud](https://img.shields.io/badge/SAP_HANA-Cloud-0FAAFF?style=for-the-badge&logo=sap&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=for-the-badge&logo=scikitlearn&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?style=for-the-badge&logo=githubactions&logoColor=white)

<sub>Team project · UPAEP, Innovación Tecnológica · SAP mentors · Jan–Apr 2026</sub>

</div>

> **Live MVP:** https://lnkd.in/eA6-ywxQ
> This public copy contains the **cloud integration and data side** (ingestion, HANA schema, detection hook, self-healing). The dashboard and the final ML model live in teammates' modules and are not included. Credentials were removed; everything sensitive is read from the environment.

---

## The Challenge

SAP provided an API that streams alerts from LLM workloads (request telemetry such as response time, finish reason and HTTP status). Companies running AI environments have fragmented logs that hide real attacks. The team had to:

1. Connect to that API and ingest the events continuously.
2. Normalize them into a relational model on **SAP HANA Cloud**.
3. Classify each event as **ATTACK** or **BENIGN**.
4. Show the alerts on a live dashboard.

---

## Stack

| Layer | Technology |
|---|---|
| Ingestion | **Python · Flask** service on **SAP BTP Cloud Foundry** (`soc-api-ingestor`), polls the SAP API every 2 minutes |
| Storage | **SAP HANA Cloud** — schema `SOC_LOGS`: 6 tables with foreign keys created by the ingestor, plus the dashboard view |
| Detection | Rule-based classifier with a hook for the ML model · baseline **RandomForest** (scikit-learn) |
| Alerts in | `/api/webhook` and `/api/replenish`, protected by an API key (constant-time check) |
| Alerts out | `notifier.py` pushes security alerts through the webhook |
| Access control | HANA roles per team area: ML (read), backend (read/write), dashboard (read) |
| Self-healing | **GitHub Actions** watchdog restarts stopped CF apps · keep-alive pings · `auto_recover.py` |
| Deploy | Cloud Foundry `manifest.yml` + `Procfile` · secrets via `cf set-env` |

---

## Architecture

```
SAP LLM-alerts API (provided by SAP)
        │  GET /logs/current  (paged, retries with backoff)
        ▼
┌───────────────────────────────┐
│  soc-api-ingestor  (CF)        │  01_Cloud_Integration_Fernando
│  Python · Flask                │  normalize → classify → bulk insert
│  /health  /api/webhook         │
│  /api/replenish  /api/logs     │
│  /api/stats                    │
└───────────────┬───────────────┘
                ▼
┌───────────────────────────────┐
│  SAP HANA Cloud · SOC_LOGS     │  SOC_EVENTS · SOC_LLM_REQUESTS ·
│                                │  MODEL_PREDICTIONS · SOC_APPLICATIONS ·
│                                │  SOC_SERVICES · SOC_REGIONS + views
└───────────────┬───────────────┘
                ▼
     ML scoring + Next.js dashboard  (teammates' modules, not in this copy)

GitHub Actions (cron) ──▶ cf start <app>   keeps the CF apps alive
```

---

## My Part (Luis Fernando Aparicio — Data & Cloud Engineer)

- Built the **ingestion service** (`01_ingest_api.py`, ~800 lines): pulls every page of the SAP API, normalizes each field, classifies the event and bulk-inserts it into HANA.
- Designed the **HANA schema** that the ingestor creates on startup (6 tables, 5 foreign keys) and the dashboard view (`scripts/apply_v_alerts_view.py`).
- Exposed the **REST endpoints** used by the team: health check, webhook, bulk replenishment, recent logs and stats.
- Wrote the **rule-based classifier** with a hook for the team's ML model, and trained a **baseline RandomForest** on attack data pulled from HANA (later replaced by the team's final model).
- Set up the **self-healing**: a GitHub Actions watchdog that restarts stopped Cloud Foundry apps, keep-alive pings and recovery scripts, because HANA Cloud's free tier stops on its own.
- Handled **deployment and orchestration** on SAP BTP Cloud Foundry.

---

## Project Structure

```
cyber-sentinel/
├── 01_Cloud_Integration_Fernando/      # soc-api-ingestor (my module)
│   ├── 01_ingest_api.py                # ingestion loop + REST API
│   ├── sap_classifier.py               # ATTACK / BENIGN rules + ML hook
│   ├── notifier.py                     # pushes alerts through the webhook
│   ├── 04_create_users.py              # HANA roles and team users
│   ├── manifest.yml · Procfile · runtime.txt · requirements.txt
│   ├── hana_config.example.json        # template, real values go in env vars
│   └── eliminados/                     # retired scripts (baseline RandomForest, first CF sprint)
├── scripts/                            # HANA diagnostics, view creation, CF restart, keep-alive, auto-recovery
├── soc-watchdog/                       # CF manifest of the keep-alive app
├── .github/workflows/                  # watchdog + keep-alive (schedules off in this public copy)
├── docs/                               # technical report, pipeline plan, Excalidraw diagrams
├── archive/                            # sprint plans and reports
└── 05_Project_Management_Manuel/       # planning documents
```

---

## Prerequisites

- [Cloud Foundry CLI](https://docs.cloudfoundry.org/cf-cli/install-go-cli.html) and an SAP BTP account (trial works)
- An **SAP HANA Cloud** instance
- Python 3.11+

---

## Setup and Deploy

### 1. Environment variables

The code has **no default credentials**: it stops if one of these is missing.

| Variable | Used by | Description |
|---|---|---|
| `SOC_API_BASE` | ingestor | Base URL of the SAP-provided events API |
| `SOC_API_TOKEN` | ingestor | Bearer token for that API |
| `WEBHOOK_SECRET` | ingestor, notifier | API key expected in the `X-API-Key` header |
| `HANA_HOST` / `HANA_PORT` / `HANA_USER` / `HANA_PASSWORD` | ingestor, scripts | SAP HANA Cloud connection |
| `NEW_USER_PASSWORD` | `04_create_users.py` | Initial password for the team users |
| `CF_USER` / `CF_PASSWORD` / `CF_ORG` | watchdog, restart script | Cloud Foundry account |

### 2. Deploy the ingestor

```bash
cd 01_Cloud_Integration_Fernando
cf push soc-api-ingestor --no-start
cf set-env soc-api-ingestor SOC_API_BASE   "https://<sap-provided-api>"
cf set-env soc-api-ingestor SOC_API_TOKEN  "<token>"
cf set-env soc-api-ingestor WEBHOOK_SECRET "<long-random-key>"
cf set-env soc-api-ingestor HANA_HOST      "<your-hana-host>.hanacloud.ondemand.com"
cf set-env soc-api-ingestor HANA_PASSWORD  "<password>"
cf start soc-api-ingestor
```

The ingestor creates the `SOC_LOGS` schema on first start, then pulls new events every 2 minutes (`INGEST_INTERVAL_S`).

### 3. Try the API

```bash
curl https://<your-app>.cfapps.<region>.hana.ondemand.com/health
curl -X POST https://<your-app>.cfapps.<region>.hana.ondemand.com/api/webhook \
  -H "X-API-Key: <WEBHOOK_SECRET>" -H "Content-Type: application/json" \
  -d '{"events": [{"_id": "test-1", "sap_function_log_type": "SECURITY_ALERT"}]}'
```

### 4. Self-healing (optional)

The workflows in `.github/workflows/` restart stopped apps and ping them. Their schedules are commented out in this copy: add `SAP_CF_USER` and `SAP_CF_PASSWORD` as repository secrets and `CF_ORG` as a variable, then uncomment the `schedule:` block.

---

## Design Decisions

- **Paged download with backoff** — each page of the SAP API is retried after 10, 30 and 60 s; pages that still fail are logged for audit instead of stopping the whole cycle.
- **Download registry per time window** — a window that was already downloaded is skipped, so restarts do not duplicate events.
- **Field normalizers** — log type, source, environment, LLM status and IDs are normalized before insert, so the dashboard queries one consistent shape.
- **Schema bootstrap on startup** — the service creates its own tables if they do not exist, so a fresh HANA instance works with no manual SQL.
- **Rules first, ML hook second** — the pipeline classified real data from day one while the team's ML model was still training.
- **Least-privilege roles in HANA** — ML and dashboard users can only read; only the backend role writes.
- **Self-healing over manual restarts** — HANA Cloud's free tier stops after about 18 hours, so a scheduled watchdog brings the apps back.
- **Secrets only in the environment** — `cf set-env` and repository secrets; nothing sensitive in code or manifests.

---

## Team

| Member | Area | Module |
|---|---|---|
| Luis Fernando Aparicio | Cloud integration and data | `01_Cloud_Integration_Fernando` (this copy) |
| Carlos / Iker | Data science and ML | not included |
| René / Luis | Data architecture | not included |
| Gerardo | Security visualization (dashboard) | not included |
| Manuel | Project management | `05_Project_Management_Manuel` |

Thanks to the SAP mentors who guided the project and provided the events API.
