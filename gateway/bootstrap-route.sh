#!/usr/bin/env bash
# Creates (or updates, idempotently) the recsys-api routes + rate limits via the APISIX Admin
# API. Run this once after `docker compose up -d`, and again whenever you change a limit here.
# On the droplet, where 127.0.0.1:9180 is reachable directly.
#
# One route per traffic profile instead of a single global limit - the endpoints have very
# different legitimate volumes:
#
#   route                         paths                                    rate/s  burst  why
#   recsys-api-recommendations    /getRec, /getRec/*                       100     100    one call per page view / widget render
#   recsys-api-events             /events/*                                100     200    impressions + clicks + views: the chattiest traffic
#   recsys-api-catalog            /items*, /users*, /products*             20      40     writes/reads by a backend, batched via /items/import
#   recsys-api-import             /clients/me/import/*                     2       5      whole-file CSV uploads, each one heavy
#   recsys-api-account            /admin/*, /clients/*, /data-sources/*    5       10     dashboard + operator + data-source admin/push traffic
#   recsys-api (fallback)         /*                                       20      10     everything else (/generateModel, /models/*, docs) - the previous global limit
#
# Limits are per client IP (limit-req, key http_x_forwarded_for). Higher-priority routes win over
# the fallback. Browsers calling /getRec and /events with the public key each have their own IP, so
# 100/s is per visitor, not per site; a server-side integration sending from a single IP should
# use POST /events/batch (up to 1000 events per request) rather than a request per event.
#
# WHY x-forwarded-for and not remote_addr (verified on the droplet 2026-09-24): APISIX sits behind
# Caddy, so its `remote_addr` is always Caddy's container IP - keying on it put EVERY visitor in a
# single shared bucket (the whole internet sharing 20 req/s). Caddy sets X-Forwarded-For to the real
# client IP and overwrites any value a client sends (a spoofed header was ignored), so it is safe
# to key on.
#
# /metrics (Prometheus) is answered 404 here: Prometheus scrapes recsys-api:6061 directly on the
# docker network, so nothing legitimate needs it through the public gateway, and it was reachable
# by anyone (route labels, request counts, latency).
set -euo pipefail

: "${ADMIN_KEY:?Set ADMIN_KEY in your shell (same value as gateway/apisix.env) before running}"

ADMIN="http://127.0.0.1:9180/apisix/admin/routes"

put_route() {
  local id="$1" priority="$2" uris="$3" rate="$4" burst="$5"
  curl -sS -X PUT "${ADMIN}/${id}" \
    -H "X-API-KEY: ${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
      \"uris\": ${uris},
      \"priority\": ${priority},
      \"upstream\": {
        \"type\": \"roundrobin\",
        \"nodes\": { \"recsys-api:6061\": 1 }
      },
      \"plugins\": {
        \"limit-req\": {
          \"rate\": ${rate},
          \"burst\": ${burst},
          \"rejected_code\": 429,
          \"key_type\": \"var\",
          \"key\": \"http_x_forwarded_for\"
        }
      }
    }"
  echo
}

put_route recsys-api-recommendations 10 '["/getRec", "/getRec/*"]' 100 100
put_route recsys-api-events          10 '["/events/*"]' 100 200
put_route recsys-api-catalog         10 '["/items", "/items/*", "/products", "/products/*", "/users", "/users/*", "/usersPurchases", "/usersRatings", "/usersPageViews"]' 20 40
put_route recsys-api-account         10 '["/admin/*", "/clients/*", "/data-sources/*"]' 5 10
put_route recsys-api-import          20 '["/clients/me/import/*"]' 2 5
put_route recsys-api                  0 '["/*"]' 20 10

# Not an upstream route: the gateway itself answers 404 (fault-injection abort), highest priority.
curl -sS -X PUT "${ADMIN}/recsys-api-block-metrics" \
  -H "X-API-KEY: ${ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "uris": ["/metrics", "/metrics/*"],
    "priority": 100,
    "plugins": {
      "fault-injection": { "abort": { "http_status": 404, "body": "{\"detail\":\"Not Found\"}" } }
    }
  }'
echo
