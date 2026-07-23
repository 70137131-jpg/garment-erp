# Deployment Plan — Textile & Apparel ERP

**Document:** 06 — Deployment Plan
**Status:** Draft v0.1
**Owner:** Rupesh
**Last updated:** 23 July 2026
**Related:** `02-Technical-Architecture.md`, `04-System-Architecture.md`

---

## 1. Purpose

How the ERP is containerised, orchestrated, built, and released — and how it sustains 200+ concurrent users. Covers Docker, Kubernetes, and GitHub as a single toolchain.

## 2. Capacity Target

### 2.1 What "200 users" means

Named users and concurrent load are different numbers. Sizing against the wrong one wastes money or causes outages.

| Metric | Assumption |
|--------|------------|
| Named users | 200–250 |
| Peak concurrent sessions | 200 |
| Actively issuing requests at any instant | ~20 (10% of concurrent) |
| Requests per active user per minute | 6 |
| Peak sustained request rate | ~20 req/s |
| Peak burst (shift change, morning login) | ~60 req/s |
| Target p95 API latency | < 500 ms |
| Target p95 list-view latency | < 2 s |
| Availability target | 99.5% during factory hours |

**Why 10%:** ERP usage is bursty and human-paced. A user opens a screen, reads it, types, saves. Between actions they issue nothing. Sizing for 200 simultaneous in-flight requests would overprovision by roughly 10×.

**Shift change is the real peak.** 200 people logging in within five minutes is a burst pattern, not a steady rate. It is what the autoscaler must absorb.

### 2.2 Capacity math

```
Gunicorn workers per pod:     4  (sync workers)
Requests per worker:          1 concurrent
Average request duration:     150 ms
Throughput per pod:           4 ÷ 0.150 = ~26 req/s

Sustained load 20 req/s   → 1 pod technically sufficient
With headroom + HA        → 3 pods minimum
Burst 60 req/s            → 3 pods handle it (78 req/s capacity)
```

**Sizing conclusion:** 3 application pods baseline, autoscaling to 8. This is modest infrastructure. A single well-provisioned node would serve 200 users; the multi-pod arrangement buys availability, not throughput.

### 2.3 Database sizing

The database is the constraint, not the application tier.

```
Connections per pod:      4 workers × 1 connection  = 4
Celery workers:           4 concurrency × 2 pods    = 8
Peak pods (8) + workers:  8 × 4 + 8                 = 40 connections
PgBouncer pool:           50
PostgreSQL max_connections: 100
```

**PgBouncer is not optional at this scale.** Django opens a connection per worker and holds it. Without pooling, autoscaling to 8 pods exhausts PostgreSQL's connection limit before it exhausts its CPU.

Recommended instance: 4 vCPU, 16 GB RAM, SSD storage with provisioned IOPS. Grow vertically first — PostgreSQL scales up more gracefully than out.

---

## 3. Docker

### 3.1 Image strategy

One image, multiple roles. The application server, Celery worker, and beat scheduler all run the same image with different commands. This guarantees they run identical code — a worker running last week's build while the web tier runs today's is a genuinely painful bug class.

### 3.2 Dockerfile

Multi-stage build. Build dependencies never reach the runtime image.

```dockerfile
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/.local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /home/app

COPY --from=builder /wheels /wheels
COPY --chown=app:app requirements.txt .
RUN pip install --user --no-cache-dir --no-index --find-links=/wheels -r requirements.txt

COPY --chown=app:app . .

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
```

**Points that matter:**

- **Non-root user.** A container running as root is one escape away from a host compromise.
- **Wheel build in stage one.** `build-essential` and `libpq-dev` are ~300 MB and are not in the final image.
- **Requirements copied before source.** Docker layer caching — a code change does not reinvalidate the dependency install.
- **Logs to stdout.** Kubernetes collects them. Writing log files inside a container is writing to a disk that disappears.

### 3.3 Local development — Docker Compose

Compose is for local development only. Production uses Kubernetes.

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: textile_erp
      POSTGRES_USER: erp
      POSTGRES_PASSWORD: devpassword
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U erp"]
      interval: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 5

  web:
    build: .
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - .:/home/app
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgres://erp:devpassword@db:5432/textile_erp
      REDIS_URL: redis://redis:6379/0
      DJANGO_SETTINGS_MODULE: config.settings.dev
    depends_on:
      db: {condition: service_healthy}
      redis: {condition: service_healthy}

  worker:
    build: .
    command: celery -A config worker -l info --concurrency=2
    volumes:
      - .:/home/app
    environment:
      DATABASE_URL: postgres://erp:devpassword@db:5432/textile_erp
      REDIS_URL: redis://redis:6379/0
      DJANGO_SETTINGS_MODULE: config.settings.dev
    depends_on:
      db: {condition: service_healthy}
      redis: {condition: service_healthy}

  beat:
    build: .
    command: celery -A config beat -l info
    volumes:
      - .:/home/app
    environment:
      DATABASE_URL: postgres://erp:devpassword@db:5432/textile_erp
      REDIS_URL: redis://redis:6379/0
      DJANGO_SETTINGS_MODULE: config.settings.dev
    depends_on:
      db: {condition: service_healthy}

volumes:
  pgdata:
```

`docker compose up` gives any developer a working environment. This is the single highest-value piece of tooling for onboarding.

### 3.4 Image tagging

```
ghcr.io/<org>/textile-erp:sha-<git-sha>     immutable, every build
ghcr.io/<org>/textile-erp:v1.4.2            release tags
ghcr.io/<org>/textile-erp:staging           moving pointer
ghcr.io/<org>/textile-erp:latest            never deployed from
```

**Deploy by SHA, always.** `latest` in a production manifest means you cannot say what is running. Rollback becomes guesswork.

---

## 4. Kubernetes

### 4.1 Is Kubernetes justified?

For 200 users on a single plant — Kubernetes is more machinery than the load requires. A pair of VMs behind a load balancer would serve this comfortably.

It earns its place if:
- Multi-plant (V1) is genuinely planned
- Zero-downtime deploys during factory hours are required
- Autoscaling for shift-change bursts matters
- The team already runs Kubernetes elsewhere

It does not if:
- This is the only workload and nobody on the team operates Kubernetes today
- Hosting is on-premises without a platform team

**Honest recommendation:** if you have Kubernetes expertise available, use it — the manifests below are straightforward and the operational model is well-documented. If you do not, managed container hosting (ECS Fargate, Cloud Run, App Platform) delivers the same availability with substantially less to learn, and migration to Kubernetes later is not difficult. The containerisation work in section 3 is identical either way, so this decision is deferrable.

The rest of this section assumes Kubernetes.

### 4.2 Cluster layout

```
Namespace: erp-prod
Namespace: erp-staging

erp-prod
├── Deployment  erp-web        3–8 replicas
├── Deployment  erp-worker     2–4 replicas
├── Deployment  erp-beat       exactly 1
├── Deployment  pgbouncer      2 replicas
├── StatefulSet redis          1 replica
├── Service     erp-web-svc    ClusterIP
├── Ingress     erp-ingress    TLS via cert-manager
├── ConfigMap   erp-config
├── Secret      erp-secrets
├── HPA         erp-web-hpa
├── PDB         erp-web-pdb
└── Job         erp-migrate    run per release
```

**PostgreSQL runs outside the cluster** — managed service (RDS, Cloud SQL) or a dedicated VM. Running a production database in Kubernetes is possible but adds operational risk that a 200-user ERP does not need to take.

### 4.3 Web deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: erp-web
  namespace: erp-prod
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels: {app: erp-web}
  template:
    metadata:
      labels: {app: erp-web}
    spec:
      containers:
      - name: web
        image: ghcr.io/<org>/textile-erp:sha-abc1234
        ports:
        - containerPort: 8000
        envFrom:
        - configMapRef: {name: erp-config}
        - secretRef: {name: erp-secrets}
        resources:
          requests: {cpu: "250m", memory: "512Mi"}
          limits:   {cpu: "1000m", memory: "1Gi"}
        readinessProbe:
          httpGet: {path: /health/ready/, port: 8000}
          initialDelaySeconds: 10
          periodSeconds: 5
        livenessProbe:
          httpGet: {path: /health/live/, port: 8000}
          initialDelaySeconds: 30
          periodSeconds: 15
        startupProbe:
          httpGet: {path: /health/live/, port: 8000}
          failureThreshold: 30
          periodSeconds: 5
```

**`maxUnavailable: 0`** — never drop below the desired replica count during a rollout. Combined with `maxSurge: 1`, the new pod must become ready before an old one is terminated.

**Three probes, three jobs:**
- **Startup** — is the process up yet? Generous failure threshold covers slow first boot.
- **Readiness** — should this pod receive traffic? Checks database and Redis. A pod that cannot reach the database is removed from the load balancer rather than serving errors.
- **Liveness** — is this process wedged? Restarts it. Keep this check trivial; a liveness probe that checks the database will restart every pod during a database blip, turning a partial outage into a total one.

### 4.4 Worker and beat

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: erp-worker
  namespace: erp-prod
spec:
  replicas: 2
  selector:
    matchLabels: {app: erp-worker}
  template:
    metadata:
      labels: {app: erp-worker}
    spec:
      terminationGracePeriodSeconds: 120
      containers:
      - name: worker
        image: ghcr.io/<org>/textile-erp:sha-abc1234
        command: ["celery", "-A", "config", "worker", "-l", "info", "--concurrency=4"]
        envFrom:
        - configMapRef: {name: erp-config}
        - secretRef: {name: erp-secrets}
        resources:
          requests: {cpu: "250m", memory: "512Mi"}
          limits:   {cpu: "1000m", memory: "1Gi"}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: erp-beat
  namespace: erp-prod
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels: {app: erp-beat}
  template:
    metadata:
      labels: {app: erp-beat}
    spec:
      containers:
      - name: beat
        image: ghcr.io/<org>/textile-erp:sha-abc1234
        command: ["celery", "-A", "config", "beat", "-l", "info"]
        envFrom:
        - configMapRef: {name: erp-config}
        - secretRef: {name: erp-secrets}
```

**Beat uses `Recreate`, not `RollingUpdate`, and replicas is exactly 1.** Two beat instances dispatch every scheduled job twice. During a rolling update, two would briefly coexist. `Recreate` terminates the old pod before starting the new one — a few seconds of no scheduler is harmless; duplicate scheduling is not.

**`terminationGracePeriodSeconds: 120`** on workers gives an in-flight task time to finish before SIGKILL.

### 4.5 Autoscaling

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: erp-web-hpa
  namespace: erp-prod
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: erp-web
  minReplicas: 3
  maxReplicas: 8
  metrics:
  - type: Resource
    resource:
      name: cpu
      target: {type: Utilization, averageUtilization: 65}
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
      policies:
      - type: Percent
        value: 100
        periodSeconds: 30
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Percent
        value: 50
        periodSeconds: 60
```

**Asymmetric windows are deliberate.** Scale up fast (30 s) to absorb the shift-change burst. Scale down slowly (300 s) so a brief lull does not remove capacity that is needed again two minutes later.

**`minReplicas: 3`** is availability, not load. Three pods survive a node failure and a rolling deploy simultaneously.

### 4.6 Disruption budget

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: erp-web-pdb
  namespace: erp-prod
spec:
  minAvailable: 2
  selector:
    matchLabels: {app: erp-web}
```

Prevents a node drain from taking all pods at once during cluster maintenance.

### 4.7 Migration job

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: erp-migrate-<release>
  namespace: erp-prod
spec:
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: migrate
        image: ghcr.io/<org>/textile-erp:sha-abc1234
        command: ["python", "manage.py", "migrate", "--noinput"]
        envFrom:
        - configMapRef: {name: erp-config}
        - secretRef: {name: erp-secrets}
```

Runs to completion before the deployment rollout begins. `backoffLimit: 1` — a failed migration should stop the release, not retry blindly.

**Migrations must be backward-compatible.** During a rolling update, old and new code run simultaneously against one schema. A migration that drops a column the old code still reads breaks every pod that has not yet been replaced.

The safe pattern is expand-then-contract across two releases:
```
Release N:    add new column, write to both, read from old
Release N+1:  read from new, stop writing old
Release N+2:  drop old column
```

### 4.8 Configuration and secrets

Non-sensitive values in a ConfigMap. Secrets in a Kubernetes Secret, ideally backed by an external manager (AWS Secrets Manager, Vault) through External Secrets Operator.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: erp-config
  namespace: erp-prod
data:
  DJANGO_SETTINGS_MODULE: config.settings.prod
  ALLOWED_HOSTS: erp.example.com
  CELERY_BROKER_URL: redis://redis:6379/0
  GUNICORN_WORKERS: "4"
```

**Never commit secrets to Git.** Not in a manifest, not base64-encoded — base64 is encoding, not encryption.

### 4.9 Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: erp-ingress
  namespace: erp-prod
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "25m"
    nginx.ingress.kubernetes.io/limit-rps: "50"
spec:
  ingressClassName: nginx
  tls:
  - hosts: [erp.example.com]
    secretName: erp-tls
  rules:
  - host: erp.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: erp-web-svc
            port: {number: 8000}
```

`proxy-body-size` accommodates tech pack and artwork uploads. The default 1 MB will reject them.

---

## 5. GitHub

### 5.1 Repository structure

```
textile-erp/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml
│   │   ├── build.yml
│   │   ├── deploy-staging.yml
│   │   └── deploy-prod.yml
│   ├── CODEOWNERS
│   └── pull_request_template.md
├── config/
├── core/  masters/  sales/  procurement/  inventory/
├── production/  quality/  costing/  finance/  reporting/
├── deploy/
│   ├── base/
│   └── overlays/{staging,prod}/
├── docs/
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

**Manifests live with the code.** A single commit carries both the code change and the manifest change it requires.

### 5.2 Branching

Trunk-based with short-lived branches.

```
main         always deployable; protected
feature/*    branched from main, merged within days
hotfix/*     branched from main, expedited review
```

**Not Git Flow.** Long-lived develop and release branches produce merge conflicts and delayed integration. For a team shipping continuously, trunk-based is less ceremony and fewer surprises.

**Branch protection on `main`:**
- Pull request required, minimum one approval
- All status checks must pass
- Branches must be current before merge
- Force push disabled
- CODEOWNERS approval for `deploy/`, `config/`, and `finance/`

### 5.3 CI workflow

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: test_erp
        options: >-
          --health-cmd pg_isready --health-interval 5s
          --health-timeout 5s --health-retries 5
        ports: ['5432:5432']
      redis:
        image: redis:7
        ports: ['6379:6379']

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - run: pip install -r requirements.txt -r requirements-dev.txt

      - name: Lint
        run: ruff check .

      - name: Format check
        run: ruff format --check .

      - name: Type check
        run: mypy .

      - name: Module boundary check
        run: lint-imports

      - name: Migration check
        run: python manage.py makemigrations --check --dry-run

      - name: Tests
        env:
          DATABASE_URL: postgres://postgres:postgres@localhost:5432/test_erp
          REDIS_URL: redis://localhost:6379/0
        run: pytest --cov --cov-report=xml --cov-fail-under=80

      - name: Security scan
        run: pip-audit
```

Two checks worth calling out:

**`lint-imports`** enforces the module dependency direction from Architecture §4. Without it, the boundaries erode within months — someone imports `finance` from `inventory` because it is convenient, review misses it, and the layering is gone.

**`makemigrations --check`** catches a model change committed without its migration. That failure otherwise surfaces at deploy time.

### 5.4 Build workflow

```yaml
name: Build
on:
  push:
    branches: [main]
    tags: ['v*']

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=sha,prefix=sha-
            type=ref,event=tag
            type=raw,value=staging,enable={{is_default_branch}}

      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Scan image
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: ghcr.io/${{ github.repository }}:sha-${{ github.sha }}
          severity: CRITICAL,HIGH
          exit-code: '1'
```

### 5.5 Deploy workflows

**Staging — automatic on merge to main:**

```yaml
name: Deploy staging
on:
  workflow_run:
    workflows: [Build]
    types: [completed]
    branches: [main]

jobs:
  deploy:
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/checkout@v4

      - uses: azure/setup-kubectl@v4

      - name: Configure cluster access
        run: echo "${{ secrets.KUBECONFIG_STAGING }}" | base64 -d > $HOME/.kube/config

      - name: Run migrations
        run: |
          kubectl -n erp-staging delete job erp-migrate --ignore-not-found
          kubectl -n erp-staging create job erp-migrate \
            --image=ghcr.io/${{ github.repository }}:sha-${{ github.sha }} \
            -- python manage.py migrate --noinput
          kubectl -n erp-staging wait --for=condition=complete job/erp-migrate --timeout=600s

      - name: Deploy
        run: |
          kubectl -n erp-staging set image deployment/erp-web \
            web=ghcr.io/${{ github.repository }}:sha-${{ github.sha }}
          kubectl -n erp-staging set image deployment/erp-worker \
            worker=ghcr.io/${{ github.repository }}:sha-${{ github.sha }}
          kubectl -n erp-staging set image deployment/erp-beat \
            beat=ghcr.io/${{ github.repository }}:sha-${{ github.sha }}
          kubectl -n erp-staging rollout status deployment/erp-web --timeout=300s
```

**Production — manual approval:**

```yaml
name: Deploy production
on:
  workflow_dispatch:
    inputs:
      image_sha:
        description: 'Git SHA to deploy'
        required: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production      # requires reviewer approval
    steps:
      - uses: actions/checkout@v4
      - uses: azure/setup-kubectl@v4

      - name: Configure cluster access
        run: echo "${{ secrets.KUBECONFIG_PROD }}" | base64 -d > $HOME/.kube/config

      - name: Backup database
        run: kubectl -n erp-prod create job --from=cronjob/db-backup pre-deploy-${{ github.run_id }}

      - name: Run migrations
        run: |
          kubectl -n erp-prod delete job erp-migrate --ignore-not-found
          kubectl -n erp-prod create job erp-migrate \
            --image=ghcr.io/${{ github.repository }}:sha-${{ inputs.image_sha }} \
            -- python manage.py migrate --noinput
          kubectl -n erp-prod wait --for=condition=complete job/erp-migrate --timeout=900s

      - name: Deploy
        run: |
          for d in erp-web erp-worker erp-beat; do
            kubectl -n erp-prod set image deployment/$d \
              "*=ghcr.io/${{ github.repository }}:sha-${{ inputs.image_sha }}"
          done
          kubectl -n erp-prod rollout status deployment/erp-web --timeout=600s

      - name: Verify
        run: curl -fsS https://erp.example.com/health/ready/

      - name: Rollback on failure
        if: failure()
        run: |
          for d in erp-web erp-worker erp-beat; do
            kubectl -n erp-prod rollout undo deployment/$d
          done
```

**Production deploys are explicit, not automatic.** A GitHub Environment with required reviewers gives you an approval gate and an audit record of who released what.

**The backup step before migration matters more than it looks.** It is the difference between a bad migration costing ten minutes and costing a day.

### 5.6 Other GitHub usage

| Feature | Use |
|---------|-----|
| Issues | Work tracking, linked to PRs |
| Projects | Sprint board, module-level roadmap |
| Dependabot | Weekly dependency PRs, grouped |
| CodeQL | Static analysis on PR |
| Secret scanning | Push protection enabled |
| Releases | Tagged versions with changelog |
| Environments | Approval gates and scoped secrets |
| CODEOWNERS | Mandatory review for finance, deploy, config |

---

## 6. Scaling Path

### 6.1 Where load actually lands

At 200 users, the application tier is not the constraint. In order of likelihood:

1. **Database connections** — solved by PgBouncer
2. **Slow queries on the stock ledger** — solved by indexing and materialised balances
3. **Report queries competing with transactions** — solved by a read replica
4. **Lock contention on stock rows** — solved by short transactions and lock ordering
5. **Application CPU** — the last thing to saturate

**Adding pods will not fix a slow query.** It will add connections to a database that is already the bottleneck and make things worse. Diagnose before scaling.

### 6.2 Staged growth

| Stage | Users | Configuration |
|-------|-------|---------------|
| Launch | 50 | 2 web, 1 worker, 1 beat, PgBouncer, 2 vCPU DB |
| Target | 200 | 3–8 web (HPA), 2 worker, 1 beat, 4 vCPU DB |
| Growth | 500 | 5–12 web, 4 worker, read replica, 8 vCPU DB |
| Multi-plant | 1,000+ | Per-plant namespaces, partitioned ledger, dedicated reporting store |

### 6.3 Read replica

Introduce when report queries measurably affect transaction latency. Route with a Django database router: all writes and transactional reads to primary, reporting selectors to replica.

**The catch:** replication lag. A report reading the replica immediately after a write may not see it. Acceptable for analytics, not for anything a user acts on within seconds of saving. Keep the replica confined to the reporting module.

### 6.4 Caching

Applied in order of value:

1. **Reference data** — UOM, currencies, defect types, chart of accounts. Long TTL, invalidated on change.
2. **Computed reports** — ageing, stock summary. 5–15 minute TTL.
3. **Session data** — already in Redis.

**Do not cache stock balances or anything a user makes a decision from.** A cached stock figure that says material is available when it is not causes a cutting-table shortage. The whole system exists to prevent exactly that.

---

## 7. Monitoring

| Metric | Alert threshold |
|--------|-----------------|
| p95 API latency | > 1 s for 5 min |
| Error rate | > 1% for 5 min |
| Pod restarts | > 3 in 15 min |
| DB connections | > 80% of pool |
| DB replication lag | > 30 s |
| Celery queue depth | > 100 for 10 min |
| **Celery beat heartbeat** | **No tick in 5 min** |
| Disk usage | > 80% |
| Certificate expiry | < 14 days |

**The beat heartbeat is the alert to configure first.** Every other failure produces errors somebody notices. A stopped scheduler produces silence — no ageing recalculation, no reorder alerts, no backup verification — and the absence of work goes unnoticed for days.

Stack: Prometheus for metrics, Grafana for dashboards, Loki or CloudWatch for logs, Sentry for exception tracking.

---

## 8. Backup and Recovery

| Aspect | Policy |
|--------|--------|
| Full backup | Nightly, automated |
| WAL archiving | Continuous |
| Retention | 30 daily, 12 monthly |
| Storage | Off-site, encrypted |
| RPO | 15 minutes |
| RTO | 4 hours |
| Restore test | Quarterly, to a scratch environment |

**An untested backup is not a backup.** The quarterly restore is what turns a backup policy into a recovery capability. Put it in the calendar, assign an owner, and record the result.

---

## 9. Rollout Sequence

| Phase | Work |
|-------|------|
| 1 | Dockerfile, Compose, CI workflow. Every developer can run the stack locally. |
| 2 | Build workflow, GHCR publishing, image scanning. |
| 3 | Staging cluster, manifests, automated staging deploy. |
| 4 | Monitoring and alerting, verified against staging. |
| 5 | Production cluster, backup automation, restore rehearsal. |
| 6 | Production deploy workflow with approval gate and rollback. |
| 7 | HPA tuning under load test at 200 concurrent sessions. |

**Do not skip phase 7.** The autoscaling thresholds in section 4.5 are reasoned estimates, not measurements. A load test at the target concurrency is what turns them into configuration you can trust.

---

## 10. Open Questions

| # | Question | Blocks |
|---|----------|--------|
| DP1 | Cloud or on-premises? | Cluster provisioning, managed database choice |
| DP2 | Is Kubernetes justified, or is managed container hosting sufficient? | Section 4 entirely |
| DP3 | Factory operating hours — when is the maintenance window? | Deploy scheduling |
| DP4 | Is there existing monitoring infrastructure to integrate with? | Section 7 stack choice |
| DP5 | Who operates the cluster — internal team or vendor? | Runbook depth, on-call |

---

## Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| v0.1 | 23 Jul 2026 | Rupesh | Initial draft |
