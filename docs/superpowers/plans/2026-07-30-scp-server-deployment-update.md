# SCP Server Deployment Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update `DEPLOY_SERVER.md` with a safe, copy-ready SCP deployment and rollback procedure for the MySQL realtime-cache release.

**Architecture:** Build a clean release archive locally, upload it to `/tmp` using SCP, then deploy it on the server while preserving `.env` and a rollback copy of the previous application directory. Initialize the realtime-cache schema before restarting Python and Spring, then verify both forced-refresh and database fast-path behavior through the Spring gateway.

**Tech Stack:** Bash, SCP, tar, Python 3.10, FastAPI, MySQL, Java 17, Maven, Spring Boot, Vue static files, Nginx, systemd.

## Global Constraints

- The release archive must not contain `.env`, Git metadata, Python caches, Java build output, test output, or runtime files.
- The server application directory is `/opt/quant`; the archive is uploaded to `/tmp`.
- The server `.env` and MySQL database must be backed up before replacing application files.
- Python port 8000 and Spring port 8081 remain bound to localhost and exposed only through Nginx.
- Deployment commands use placeholders for SSH host, SSH user, and passwords.
- Realtime cache retains five trading days and must be initialized without manually maintaining SQL migration files.

---

### Task 1: Rewrite the SCP update and realtime-cache deployment guide

**Files:**
- Modify: `DEPLOY_SERVER.md`

**Interfaces:**
- Consumes: `realtime_cache.init_realtime_cache()`, Python endpoints `/api/intraday-monitor` and `/api/realtime-info`, Spring endpoints `/api/quant/intraday-monitor` and `/api/quant/realtime-info`.
- Produces: A deployment guide whose commands can be copied from a local workstation and a Linux server shell without mixing their execution contexts.

- [ ] **Step 1: Update project and database descriptions**

Add `realtime_cache.py`, `realtime_info_service.py`, and `intraday_monitor_service.py` to the documented deployment structure. Document the automatically created `realtime_minute_cache` and `realtime_result_cache` tables and their five-trading-day retention rule.

- [ ] **Step 2: Replace the update procedure with a local SCP release flow**

Document these local operations with explicit shell blocks:

```bash
cd /本地项目父目录
tar \
  --exclude='piao/.env' \
  --exclude='piao/.git' \
  --exclude='piao/__pycache__' \
  --exclude='piao/tests/__pycache__' \
  --exclude='piao/.runtime' \
  --exclude='piao/quantServer/quantServer/target' \
  -czf "quant-release-$(date +%Y%m%d-%H%M%S).tar.gz" piao

scp quant-release-YYYYMMDD-HHMMSS.tar.gz \
  root@你的服务器:/tmp/quant-release.tar.gz
```

Clearly label these as local commands and explain how to replace `root`, host, and local directory.

- [ ] **Step 3: Add server backup, unpack, and configuration restoration**

Document server-side commands that validate the archive, back up `/opt/quant/.env`, dump MySQL, move the old directory to a timestamped rollback path, unpack into a staging directory, move the staged `piao` directory to `/opt/quant`, and restore `.env`.

The commands must stop before replacing files when the archive does not contain `piao/app.py`, `piao/realtime_cache.py`, `piao/quantClient/index.html`, and `piao/quantServer/quantServer/pom.xml`.

- [ ] **Step 4: Add schema initialization, builds, and restarts**

Document:

```bash
cd /opt/quant
python3 -m pip install -r requirements.txt
python3 -m py_compile app.py realtime_cache.py realtime_info_service.py intraday_monitor_service.py
python3 -c "import settings; settings.load_env_files(); from realtime_cache import init_realtime_cache; init_realtime_cache(); print('realtime cache schema ready')"

cd /opt/quant/quantServer/quantServer
mvn test
mvn clean package -DskipTests

sudo systemctl restart quant-python
sudo systemctl restart quant-spring
sudo nginx -t
sudo systemctl reload nginx
```

Include log and status commands for both services.

- [ ] **Step 5: Add cache warm-up and verification**

Document forced refresh first:

```bash
curl --fail 'http://127.0.0.1:8081/api/quant/intraday-monitor?force_refresh=true'
curl --fail 'http://127.0.0.1:8081/api/quant/realtime-info?limit=10&force_refresh=true'
```

Then document fast-path requests without `force_refresh`, plus MySQL checks for table existence and row counts. Explain that the first deployment starts with an empty result cache and requires one successful forced refresh.

- [ ] **Step 6: Add rollback and troubleshooting**

Document stopping both services, moving the failed `/opt/quant` aside, restoring the timestamped rollback directory, restoring `.env`, rebuilding Spring if needed, and restarting both services. Add troubleshooting for empty quick results, MySQL permission errors, stale browser assets, and long forced-refresh requests.

- [ ] **Step 7: Verify the document**

Run:

```bash
rg -n "realtime_cache|realtime_minute_cache|realtime_result_cache|force_refresh|scp|回滚" DEPLOY_SERVER.md
git diff --check -- DEPLOY_SERVER.md
```

Expected: every required topic appears and `git diff --check` prints no errors.

- [ ] **Step 8: Commit**

```bash
git add DEPLOY_SERVER.md
git commit -m "docs: update scp server deployment"
```
