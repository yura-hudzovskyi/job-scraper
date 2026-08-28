# Deployment (Cloudflare Pages + Oracle Cloud Free Tier)

Frontend and backend deploy separately, to different places:

- **Frontend** → **Cloudflare Pages**. Connects directly to the GitHub repo; Cloudflare
  builds and redeploys it on every push by itself. Nothing in this repo's CI touches
  it — no config needed here beyond the two build settings in Part 4.
- **Backend + Postgres + Redis + Celery worker/beat** → one **Oracle Cloud Always
  Free VM**, as containers. Push to `main` → GitHub Actions builds the backend image,
  pushes it to GHCR, SSHes into the VM, and restarts.

Since Cloudflare Pages always serves over HTTPS, the backend needs real HTTPS too —
a browser blocks an HTTPS page from calling a plain-HTTP API outright (mixed
content), it's not just a warning. Oracle doesn't hand out a public domain for the
VM (only an IP), so this uses **DuckDNS** — a free dynamic-DNS service — to get a
stable hostname Caddy can get a Let's Encrypt certificate for. If you'd rather use a
domain you own instead, skip Part 2 and point that domain's A record at the VM
instead of using a `.duckdns.org` name; everything else is identical.

## Part 1 — Create the VM (one-time, in the Oracle Cloud console)

1. **Sign up / log in** at [cloud.oracle.com](https://cloud.oracle.com) — the Always
   Free tier needs no time-limited trial, it's free indefinitely within its limits.
2. **Create a compute instance**: Compute → Instances → Create Instance.
   - **Image**: Ubuntu 22.04 (or newer LTS) — easiest for the Docker install step.
   - **Shape**: click "Change shape" → Ampere → **VM.Standard.A1.Flex** — the Always
     Free ARM shape. Give it 2-4 OCPUs and 12-24 GB RAM (the free tier's total
     allowance across all A1 instances is 4 OCPU / 24 GB, so one VM can use all of
     it). The x86 "Micro" free shapes only have 1 GB RAM each — too little for
     Postgres + sentence-transformers + everything else running together. Ampere is
     also plain ARM64, which Ollama and every other image here already supports
     natively — nothing extra to configure. If you're running Ollama too (the
     default — see Part 3), lean toward the 24 GB end; a 12 GB VM works but limits
     you to a small model (see "Choosing an Ollama model" below).
   - **SSH key**: generate a keypair locally if you don't have one —
     `ssh-keygen -t ed25519 -f ~/.ssh/oracle_vm -C "oracle-vm"` — and paste the
     **public** key (`~/.ssh/oracle_vm.pub`) into the "Add SSH keys" box.
   - Create the instance.
3. **Reserve the public IP** — by default Oracle assigns an *ephemeral* public IP,
   which can change if the instance stops/restarts. Networking → IP Management →
   Reserved Public IPs → create one and attach it to the instance, so DuckDNS only
   needs to be pointed at it once. Note the IP.
4. **Open the ports it needs**: Networking → Virtual Cloud Networks → (your VCN) →
   Security Lists → (the default list) → Add Ingress Rules:
   - Source CIDR `0.0.0.0/0`, TCP, port `80` (Let's Encrypt's HTTP-01 challenge)
   - Source CIDR `0.0.0.0/0`, TCP, port `443` (HTTPS)
   - Port 22 (SSH) is normally open by default — confirm it's there too.
5. **Oracle's VMs also firewall themselves at the OS level**, separately from the
   Security List above — a well-known trip-up: the Security List can be wide open
   and traffic still won't get through until you also open the port on the VM
   itself. SSH in and run:
   ```bash
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```
   (If `netfilter-persistent` isn't installed: `sudo apt install iptables-persistent`
   first. If the VM uses `ufw` instead, `sudo ufw allow 80,443/tcp` is the equivalent.)

## Part 2 — Point a hostname at it (DuckDNS)

1. Go to [duckdns.org](https://www.duckdns.org), sign in (GitHub/Google/etc.).
2. Create a subdomain, e.g. `job-scraper-api` → gives you
   `job-scraper-api.duckdns.org`.
3. Set its IP to the VM's **reserved** public IP from Part 1 and save. That's it —
   because the IP is reserved (static), you don't need DuckDNS's dynamic-update
   script or a cron job; this is a one-time setting.

## Part 3 — One-time server setup

SSH in (`ssh -i ~/.ssh/oracle_vm ubuntu@<VM_PUBLIC_IP>`) and run the bootstrap
script:

```bash
curl -fsSL https://raw.githubusercontent.com/yura-hudzovskyi/job-scraper/main/deploy/bootstrap.sh -o bootstrap.sh
chmod +x bootstrap.sh
./bootstrap.sh
```

It installs Docker, clones the repo to `~/job-scraper`, and stops there the first
time to let you fill in `.env`:

```bash
nano ~/job-scraper/.env
```

At minimum set:
- `API_DOMAIN=job-scraper-api.duckdns.org` (or your own domain, if you used one)
- `API_CORS_ORIGINS=` your Cloudflare Pages URL(s) — e.g.
  `https://job-scraper.pages.dev` once you know it from Part 4, comma-separated if
  you also add a custom domain there later
- `SECRET_KEY` — any random string (`openssl rand -hex 32`)
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — from @BotFather, same as local dev
- `LLM_PROVIDER=ollama` (the default) runs CV analysis on a local model in the
  `ollama` container in `docker-compose.prod.yml` — no API key, no per-token cost.
  Set `LLM_MODEL` too (see "Choosing an Ollama model" below). If you'd rather use a
  hosted model instead, set `LLM_PROVIDER=anthropic` or `openai` and the matching
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` — the `ollama` container then just sits idle
  and you can remove it from the compose file if you want the RAM back.
- `EMBEDDING_PROVIDER=sentence_transformers` (the default — needs no key, runs
  locally in the API/worker containers)

Then run the script again to actually start everything and apply migrations:

```bash
./bootstrap.sh
```

This second run also pulls the Ollama model set in `LLM_MODEL` (only if
`LLM_PROVIDER=ollama`) — a one-time download, see sizing below.

Check it's up: `docker compose -f docker-compose.prod.yml ps`, and
`curl https://job-scraper-api.duckdns.org/api/sources` (from your own machine, once
DNS has propagated — usually under a minute) should return `[]` or your two sources,
with a valid certificate, rather than an error.

### Choosing an Ollama model

CV analysis is a manual, occasional action (you click "Analyze" once per CV, not
something that runs per-job), so it doesn't need to be fast — a CPU-only response in
10-30s on the A1 shape is fine. What matters is fitting comfortably in RAM alongside
Postgres, the API/worker processes, and sentence-transformers, which together use
roughly 2-3 GB.

| Model | Download / resident size | Free-tier fit |
|---|---|---|
| `llama3.2:3b` | ~2 GB | Comfortable even on a 12 GB VM; recommended default |
| `llama3.1:8b` | ~4.7 GB | Fine on a 24 GB VM (4 OCPU / 24 GB shape); tight on 12 GB |
| `qwen2.5:7b` | ~4.4 GB | Similar footprint to `llama3.1:8b`, often better structured-output adherence |

Set `LLM_MODEL` to whichever you pick before the second `./bootstrap.sh` run (or
change it later and run `docker compose -f docker-compose.prod.yml exec ollama ollama
pull <model>` by hand, then update `.env` and restart the `api` container). If you're
on the smaller 12 GB VM.Standard.A1.Flex config, stick to `llama3.2:3b` — an 8B model
plus Postgres can OOM the box under load.

## Part 4 — Deploy the frontend (Cloudflare Pages)

1. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git → pick
   this repo.
2. Build settings:
   - **Root directory**: `frontend`
   - **Build command**: `npm run build`
   - **Build output directory**: `dist`
3. Environment variables (same screen, or Settings → Environment variables after
   creating the project): add `VITE_API_BASE_URL` = `https://job-scraper-api.duckdns.org`
   (your actual `API_DOMAIN` from Part 3, with `https://`). This is a **build-time**
   Vite value — changing it means triggering a rebuild (Cloudflare does this
   automatically on the next push, or you can hit "Retry deployment").
4. Save and deploy. Cloudflare gives you a `https://<project>.pages.dev` URL
   immediately, and rebuilds it on every push to `main` from here on — no further
   setup, and nothing in this repo's GitHub Actions is involved.
5. Go back to the VM's `.env` and set `API_CORS_ORIGINS` to that `.pages.dev` URL
   (add a custom domain later the same way, comma-separated), then
   `docker compose -f docker-compose.prod.yml up -d api` to pick it up.

## Part 5 — Wire up backend auto-deploy (GitHub Actions)

1. **Generate a dedicated deploy key** (don't reuse your personal one) — on your own
   machine:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/job_scraper_deploy -C "github-actions-deploy" -N ""
   ```
2. **Authorize it on the VM**:
   ```bash
   ssh-copy-id -i ~/.ssh/job_scraper_deploy.pub -o IdentityFile=~/.ssh/oracle_vm ubuntu@<VM_PUBLIC_IP>
   ```
3. **Add three repo secrets** — GitHub repo → Settings → Secrets and variables →
   Actions → New repository secret:
   | Secret | Value |
   |---|---|
   | `DEPLOY_HOST` | the VM's reserved public IP |
   | `DEPLOY_USER` | `ubuntu` (or whatever user you SSH as) |
   | `DEPLOY_SSH_KEY` | the full contents of `~/.ssh/job_scraper_deploy` (the **private** key) |
4. **Make the `job-scraper-backend` GHCR package public** (simplest option — avoids
   the server needing registry credentials to pull). After the first push to `main`
   triggers the workflow and creates the package: GitHub profile → Packages →
   `job-scraper-backend` → Package settings → Change visibility → Public.
   (Alternative if you'd rather keep it private: add a `docker login ghcr.io` step
   to the SSH script using a
   [PAT with `read:packages`](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) —
   not covered here since public is simpler for a personal project with no secrets
   baked into the image.)

That's it — push a backend change to `main` and watch the "Deploy backend" workflow
in the Actions tab (it only triggers on changes under `backend/`,
`docker-compose.prod.yml`, or `Caddyfile` — a frontend-only push won't run it, since
Cloudflare Pages handles that independently). It builds the image, pushes it to
GHCR, then SSHes in and runs:

```bash
cd ~/job-scraper && git pull
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```

`git pull` picks up changes to `docker-compose.prod.yml`/`Caddyfile`/migrations
themselves; the actual application code comes from the freshly-pulled image, not
from the git checkout.

## Notes

- **Backups**: `pgdata` is a named Docker volume on the VM's own disk — it survives
  container restarts and redeploys, but not VM deletion. For anything you'd be upset
  to lose, add a cron job doing `docker compose exec -T postgres pg_dump -U
  job_scraper job_scraper | gzip > backup-$(date +%F).sql.gz` somewhere durable
  (Oracle Object Storage's free tier is a reasonable target). Not set up here —
  personal-scale scraped-job data is regenerable by re-running the scrapers, so this
  is a "nice to have," not blocking.
- **Local `docker-compose.yml` is unaffected** — it still runs the dev build (bind
  mounts, `--reload`, `npm run dev`) exactly as before. `docker-compose.prod.yml` is
  a separate, self-contained file for the server, and doesn't include a frontend
  service at all.
- **First deploy will be slow**: the backend image bundles sentence-transformers
  (and its PyTorch dependency), which is a large download. Subsequent deploys reuse
  Docker's layer cache and GitHub Actions' build cache, so they're much faster
  unless `pyproject.toml` changes. The Ollama model pull (Part 3) is separate from
  this and only happens once, not on every deploy.
- **Ollama cost/latency tradeoff**: CPU inference on the A1 shape is noticeably
  slower than a hosted API (seconds-to-tens-of-seconds per CV instead of ~1s), but
  it's the only zero-cost option, and CV analysis is a manual, low-frequency action
  where that's an acceptable trade. Matching/scoring itself never calls an LLM
  regardless of provider, so this only affects the "Analyze CV" action.
