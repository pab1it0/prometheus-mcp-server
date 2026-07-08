# Alerts/Rules, Label Exploration, and Runtime Status Tools

**Date:** 2026-07-08
**Status:** Approved

## Goal

Add three groups of read-only tools that close the biggest gaps for LLM-driven
investigation: seeing what is firing, discovering labels to build valid PromQL, and
inspecting the server itself.

## Current state

The server exposes 6 tools: `health_check`, `execute_query`, `execute_range_query`,
`list_metrics`, `get_metric_metadata`, `get_targets`. All Prometheus API access goes through
`make_prometheus_request(endpoint, params)` which unwraps the `{"status": "success", "data": ...}`
envelope, handles auth/headers/TLS, and raises on errors.

## Selected features

### 1. Alerts & rules visibility

The single biggest gap: an LLM investigating an incident cannot see what is firing.

- **`list_alerts`** — `GET /api/v1/alerts`. No parameters. Returns `{"alerts": [...]}` plus a
  computed `alert_count` for convenience.
- **`list_rules`** — `GET /api/v1/rules`. Optional `type` parameter (`alert` | `record`,
  validated before the request) passed through as `?type=`. Optional `rule_name` and `rule_group`
  filters passed as repeated `rule_name[]` / `rule_group[]` params (server-side filtering,
  supported since Prometheus 2.44) **and re-applied client-side**, because older Prometheus and
  some compatible backends (Thanos, VictoriaMetrics) silently ignore the params — without the
  fallback they would return unfiltered data presented as filtered.
  Returns `{"groups": [...], "group_count": N}`.

### 2. Label & series exploration

`list_metrics` only exposes `__name__` values. LLMs need generic label discovery to construct
valid PromQL.

- **`list_label_names`** — `GET /api/v1/labels`. Optional `match` (list of series selectors,
  sent as repeated `match[]`), optional `start`/`end` timestamps. Returns
  `{"labels": [...], "count": N}` for consistency with the repo's dict-shaped tool results.
- **`list_label_values`** — `GET /api/v1/label/{label_name}/values`. Same optional `match`,
  `start`, `end`. Returns `{"values": [...], "count": N}`. `label_name` must be non-empty and is
  escaped with Prometheus's `U__` values-escaping when it falls outside the classic
  `[a-zA-Z_][a-zA-Z0-9_]*` charset (Prometheus 3.x UTF-8 names like `app.kubernetes.io/name`
  contain `/` and would otherwise change the request path).
- **`find_series`** — `GET /api/v1/series`. Required `match` (list of series selectors; the API
  requires at least one), optional `start`/`end`, optional `limit` (must be positive). The limit
  is forwarded server-side as `limit+1` (honored since Prometheus 2.53, harmlessly ignored by
  older versions) so a broad selector on a high-cardinality server doesn't transfer the full
  series set; the extra series only signals `has_more`. Returns `{"series": [...],
  "returned_count": N, "has_more": bool}` — no exact `total_count`, which a bounded fetch cannot
  know.

### 3. Runtime & build diagnostics

Lets an LLM answer "what version is this, how is it configured at runtime, and what is eating
storage" without shell access.

- **`get_runtime_info`** — `GET /api/v1/status/runtimeinfo`.
- **`get_build_info`** — `GET /api/v1/status/buildinfo`.
- **`get_tsdb_stats`** — `GET /api/v1/status/tsdb`. Optional `limit` (passed through as `?limit=`,
  controls how many items per stats list; supported by Prometheus ≥ 2.41).

## Approaches considered

1. **Follow existing single-file pattern (chosen).** Add tools to `server.py` using the same
   `@mcp.tool` + `_tool_name()` + annotations idiom, with structured logging. Low risk, matches
   repo conventions and how every existing tool is written.
2. **Split server.py into modules per toolset.** Better long-term structure but a refactor
   unrelated to the goal, high review surface. Rejected (YAGNI).
3. **Add more surface in the same pass (exemplar queries, embedded docs browsing, TSDB admin
   endpoints).** Docs browsing requires bundling a docs snapshot; TSDB admin endpoints are
   destructive and need an opt-in safety flag; exemplar queries are niche. Rejected — out of
   scope for this change.

## Design details

- All 8 new tools are read-only GETs: annotations `readOnlyHint: true`, `destructiveHint: false`,
  `idempotentHint: true`, `openWorldHint: true`, each with a `title` and icon, named through
  `_tool_name()` so `TOOL_PREFIX` keeps working.
- Repeated query params (`match[]`, `rule_name[]`, `rule_group[]`) are passed to `requests` as
  lists, which serializes them as repeated keys.
- Error handling: inherited from `make_prometheus_request` (raises `ValueError` on API error,
  `requests` exceptions on transport failure) — identical to existing tools. Numeric and enum
  inputs (`limit`, `type`) are validated client-side so callers get a clear message instead of an
  opaque HTTP 400 whose reason `raise_for_status` discards.
- No new dependencies, no config changes.

## Testing

Tests go in `tests/test_tools.py`, following the existing pattern: mock
`make_prometheus_request`, call through `fastmcp.Client(mcp)`, assert both the forwarded
endpoint/params and the shaped result. One happy-path test per tool plus param-forwarding
variants (`type` filter for rules, `match`/`start`/`end` for labels/series, `limit` handling
for series and TSDB stats) and error-case tests for every client-side validation.

## Documentation

Add the 8 tools to the "Available Tools" table in `README.md` under their categories
(Alerting, Discovery, Status).
