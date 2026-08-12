# Scraper-Health-MCP

### Autonomous Self-Healing Scraper Fleet Manager for Bright Data Scraper Studio

> The first agent that closes Bright Data's self-healing loop end-to-end without a human in the middle — turning the `resume_automation_job` endpoint the official demo couldn't find into a one-command "deploy and forget" scraper fleet.

Built for **WeMakeDevs Into the Scrape-Verse** hackathon (Aug 17–23, 2026).

## 🎯 The Problem

Web scrapers break silently. When a target site renames a CSS class, moves a field, or redesigns a layout, traditional scrapers return empty/garbage rows with no alarm. The failure is discovered only when a human notices the data is wrong — by which point downstream systems have been consuming bad data for hours or days.

## ✅ The Solution

Scraper-Health-MCP is an autonomous AI agent that wraps Bright Data's Scraper Studio self-healing API into a fully automated loop:

1. **Monitor** — Runs scrapers on schedule and health-checks every result
2. **Detect** — Catches breakage via schema validation, null-field detection, and row-count anomaly detection (Sentinel-style statistical baselines)
3. **Heal** — Triggers Bright Data's AI self-healing (`refactor_template`)
4. **Auto-Approve** — Programmatically accepts the AI's proposed extraction diff via `resume_automation_job` (the step Bright Data's own demo couldn't do)
5. **Verify** — Re-scrapes and confirms health is restored
6. **Escalate** — If healing fails, regenerates the scraper from scratch

## 🔑 The Technical Unlock

Bright Data's official self-healing demo repo ([anil-bd/scraper-studio-self-healing-demo](https://github.com/anil-bd/scraper-studio-self-healing-demo)) explicitly states the public API *"does not document an endpoint to approve programmatically"* and exits with code 3 (`awaiting approval`) when the heal hits `pending_answer`, requiring manual UI approval.

**The current docs DO expose `POST /resume_automation_job`** with `{message: true, auto_save: true}` to accept the diff automatically. This means a fully autonomous, hands-off self-healing loop is now buildable — and almost no competitor will have found this.

## 🤖 MCP Server

The agent exposes 5 MCP tools callable from Claude Code, Cursor, or any MCP-compatible AI agent:

| Tool | Description |
|------|-------------|
| `create_scraper` | Create a new scraper from URL + description via Bright Data AI Flow |
| `run_collector` | Trigger a collector and return results |
| `health_check` | Run collector + check output health (schema, nulls, row-count anomaly) |
| `self_heal` | Full autonomous loop: detect → heal → auto-approve → re-scrape → verify |
| `verify` | Verify a healed collector has restored health |

## 🏗️ Architecture

```
Register collector (ID + schema + required fields)
        │
        ▼
  Schedule / trigger run  ──►  POST /dca/trigger
        │
        ▼
  Health-check result  ──►  GET /dca/dataset  (schema drift? nulls? row-count anomaly?)
        │
   ├── healthy ─► log success, schedule next run
   └── broken  ─► TRIGGER SELF-HEAL
                    │
                    ▼
          POST /refactor_template  (targeted heal prompt from the failed field)
                    │
                    ▼
          Poll /refactor_template/progress
                    │
                    ├── status:"pending_answer" ─► POST /resume_automation_job
                    │                              {message:true, auto_save:true}   ← the unlock
                    └── done/failed
                    │
                    ▼
          Re-scrape + verify against baseline schema
                    │
                    ├── passes ─► log heal event, resume schedule
                    └── fails  ─► escalate: regenerate scraper from scratch (Workflow 1)
                                  + alert/notification
```

## 🛠️ Tech Stack

| Layer | Tech |
|-------|------|
| Scraper infra | Bright Data Scraper Studio (CLI + AI Flow API) |
| Backend | Python 3.12 + FastAPI |
| Agent interface | MCP server (5 tools) |
| Anomaly detection | In-package stats (mean/std row-count + schema validator) |
| Dashboard | Jinja2 templates + dark-themed HTML/CSS/JS |
| Deploy | Vercel serverless (free tier) |
| Repo | github.com/0xConsole/scraper-health-mcp |

## 🚀 Setup

```bash
# Clone
git clone https://github.com/0xConsole/scraper-health-mcp.git
cd scraper-health-mcp

# Install
pip install -r requirements.txt

# Run (mock mode — no API key needed)
python -m uvicorn app.main:app --reload

# Run (live mode — with Bright Data API key)
export BRIGHTDATA_API_KEY="your-key-here"
python -m uvicorn app.main:app --reload
```

Open `http://localhost:8000` to see the dashboard.

## 📡 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard |
| GET | `/api/health` | Health check |
| GET | `/api/status` | Full orchestrator status |
| GET | `/api/tools` | List MCP tools |
| GET | `/api/mcp/manifest` | MCP server manifest |
| POST | `/api/create_scraper` | Create scraper (MCP tool 1) |
| POST | `/api/run_collector` | Run collector (MCP tool 2) |
| POST | `/api/health_check` | Health check (MCP tool 3) |
| POST | `/api/self_heal` | Self-heal loop (MCP tool 4) |
| POST | `/api/verify` | Verify heal (MCP tool 5) |
| POST | `/api/demo` | Full demo (break + heal cycle) |
| POST | `/api/trigger_breakage` | Simulate breakage + trigger heal |

## 🎪 Demo

1. Click "Run Full Demo" on the dashboard
2. The agent health-checks all registered collectors
3. Simulates a site change (breakage) on the first collector
4. Detects the breakage via health check (null fields, row count anomaly)
5. Triggers AI self-healing (`refactor_template`)
6. Polls until the AI proposes a diff (`pending_answer`)
7. **Auto-approves** the diff via `resume_automation_job` ← the unlock
8. Re-scrapes and verifies health is restored
9. Logs the entire heal event in the timeline

## 📊 What's Real vs Mocked

| Component | Status |
|-----------|--------|
| MCP server (5 tools) | ✅ Real — fully functional |
| Health checker (schema, nulls, anomaly) | ✅ Real — Sentinel-style stats |
| Heal orchestrator (full loop) | ✅ Real — all states wired |
| Auto-approve (`resume_automation_job`) | ✅ Real — calls actual endpoint when API key set |
| Bright Data API calls | 🔧 Mock mode (no key) / Real (with key) |
| Demo collectors | ✅ 3 seeded (HN, e-commerce, docs) |
| Dashboard + heal timeline | ✅ Real — live data from orchestrator |

## 📝 License

Apache 2.0

## 🔗 Links

- **Live demo:** https://scraper-health-mcp.vercel.app
- **GitHub:** https://github.com/0xConsole/scraper-health-mcp
- **Hackathon:** WeMakeDevs Into the Scrape-Verse (Aug 17–23, 2026)
- **Bright Data Scraper Studio:** https://brightdata.com/scraper-studio

---

*Built by Sentinel Dev · Team: Xayaan Ibrahim (Individual)*
