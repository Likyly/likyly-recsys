# API gateway (APISIX) + Hoppscotch

Adds a gateway layer in front of `recsys-api` and a self-hosted API testing
tool, both running on the droplet alongside the existing recsys-api compose
stack. This directory is config only - nothing here is deployed
automatically; the CI pipeline (`.github/workflows/deploy.yml`) still only
builds and pulls the `recsys-api` image. Redeploying/updating this stack is
manual, following the steps below.

## Status

Live on the droplet as of 2026-09-23. `api.likyly.com` and
`www.likyly.com/recsys-api/` route through APISIX (rate-limit verified: 60
concurrent requests against the 20/s+burst-10 limit returned 37×429).
Hoppscotch is up (frontend/admin/backend all verified responding, DB
migrated) but **not yet reachable publicly** - `hoppscotch.likyly.com` has no
DNS record yet, so its TLS cert can't be issued. Point DNS at the droplet
(step 10 below) to finish that part.

Unrelated finding from this rollout, not caused by it and not fixed here:
`datatoform.com` has no TLS certificate on disk and Caddy has been silently
failing to obtain one (`/data/caddy/locks/issue_cert_datatoform.com.lock` on
the droplet). Worth a look separately.

## Topology

```
Caddy (droplet, TLS)
 ├── api.likyly.com              -> apisix:9080 -> recsys-api:6061
 ├── www.likyly.com/recsys-api/  -> apisix:9080 -> recsys-api:6061
 └── hoppscotch.likyly.com       -> hoppscotch:80    (bypasses APISIX)

APISIX Admin API + embedded dashboard: 127.0.0.1:9180 on the droplet only,
reached via SSH tunnel - never through Caddy, never on the internet.
```

Two things confirmed only by inspecting the running containers, not
documented anywhere obvious, worth knowing before touching this again:
- The `hoppscotch/hoppscotch` aio image's internal Caddy listens on **80**,
  not 3000 - only the host-side publish in `docker-compose.yml` uses 3000.
  Anything reaching the container directly (Caddy, `docker exec`, another
  container) needs port 80.
- The droplet's live Caddyfile is **not** bind-mounted into the `caddy`
  container - it's baked in at build time (`image: caddy-edge, build:
  ./caddy` in `~/app/licenses/docker-compose.yml`). Editing
  `/home/deployer/app/licenses/caddy/Caddyfile` alone does nothing to the
  running container. To apply a change without rebuilding/recreating it
  (and dropping every other domain it fronts for a moment):
  `docker cp` the edited file into the container, then
  `docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`.
  The host file stays the correct source for the next real rebuild either way.

APISIX sits between Caddy and `recsys-api` for the API traffic only
(rate-limiting today; more plugins can be added via the dashboard or Admin
API later). Its routes live in etcd, not a static file, so its dashboard
can manage them. Hoppscotch is a separate dev tool for exercising the API
by hand and isn't in the request path.

`etcd` and `hoppscotch-db` sit on a private `gateway_net` network, not the
shared `licenses_web` network that `xp-flightdeck-api`, `datatoform`, and
`likyly-website` also run on - only `apisix` and `hoppscotch` bridge both,
which is the minimum needed for Caddy/recsys-api reachability. Admin API
access is layered: `admin_key` (required, random, `gateway/apisix.env`),
port 9180 published to the droplet's loopback only (never to Caddy/the
internet), and an `allow_admin` allowlist scoped to `gateway_net`'s actual
subnet - confirmed live rather than guessed, since a host-published port
arrives NAT'd through the bridge gateway, not as `127.0.0.1` (see the
comment in `apisix_conf/config.yaml`).

Note: the droplet's Caddy build (`caddy-edge`) already has the
`caddy-ratelimit` module and uses it for `xp-flightdeck-api` (see its
`rate_limit` block in the Caddyfile). APISIX was chosen anyway per your call,
mainly for the dashboard and room to grow into other API-gateway concerns
later - a Caddy-only `rate_limit` block on `api.likyly.com` would also have
worked with zero new containers, worth knowing if you ever want to simplify
back down.

## First-time deploy (manual, on the droplet)

Network name (`licenses_web`), Caddyfile structure, and the aio image's real
ports below are already confirmed against the live droplet - this is the
sequence that was actually run, not a guess.

1. Copy this `gateway/` directory to the droplet, e.g. `~/app/gateway/`.
2. `cp apisix.env.example apisix.env` and set `ADMIN_KEY` to
   `openssl rand -hex 32` - not the well-known docs example key.
3. `cp hoppscotch.env.example hoppscotch.env` and fill in real values -
   `HOPPSCOTCH_DB_PASSWORD` *and* `POSTGRES_PASSWORD` (same value, both
   required, see the comment in `hoppscotch.env.example` for why) via
   `openssl rand -hex 16`, and `DATA_ENCRYPTION_KEY` via
   `openssl rand -hex 16`. Never commit either `.env` file.
4. `docker compose -f gateway/docker-compose.yml up -d` - this also runs
   `hoppscotch-migrate` (Prisma migrations) automatically before starting
   Hoppscotch; nothing manual needed there.
5. Bootstrap the recsys-api route: `ADMIN_KEY=<same value as apisix.env>
   ./bootstrap-route.sh` (run on the droplet).
6. Confirm APISIX proxies correctly, from a container on `licenses_web`
   (the host has no route to container-internal ports/names):
   `docker run --rm --network licenses_web curlimages/curl:latest -H 'Host: api.likyly.com' http://apisix:9080/`
   should return the real recsys-api response.
7. Open the dashboard to sanity-check the route: from your machine,
   `ssh -L 9180:127.0.0.1:9180 do-deployer`, then browse
   `http://127.0.0.1:9180/ui/` and log in with the admin key.
8. Confirm Hoppscotch works on `127.0.0.1:3000` (host loopback) before
   wiring Caddy: `/` (frontend), `/admin`, and `/backend/health` should all
   return 200.
9. Back up the live Caddyfile first
   (`cp Caddyfile Caddyfile.bak.$(date +%Y%m%d%H%M%S)`), merge
   `Caddyfile.snippet`'s changes into `/home/deployer/app/licenses/caddy/Caddyfile`
   by hand (it's a diff, not a drop-in replacement), then apply without a
   full container rebuild: `docker cp` the edited file into the running
   `caddy` container and `docker exec caddy caddy reload --config
   /etc/caddy/Caddyfile --adapter caddyfile` (see the topology section above
   for why a plain `caddy reload` on the host doesn't work here).
10. DNS: point `hoppscotch.likyly.com` at the droplet (same as the other
    `*.likyly.com` records) - until this exists, Caddy will keep retrying
    (and failing) to obtain its TLS certificate, harmlessly.

## Changing routes/rate limits

Either the dashboard (`http://127.0.0.1:9180/ui/` via SSH tunnel) or
`bootstrap-route.sh` / the Admin API directly. There's no file to keep in
sync with what's live - etcd is the source of truth once deployed, so if you
change something via the dashboard, update `bootstrap-route.sh` too so a
future re-run doesn't revert it.

## Rollback

Point the Caddyfile's `api.likyly.com` / `/recsys-api/*` blocks back at
`recsys-api:6061` directly and reload Caddy. `docker compose -f
gateway/docker-compose.yml down` removes the gateway containers (add `-v` to
also drop the etcd/hoppscotch-db volumes).
