# Security And Control

An always-running agent loop needs explicit controls before it can be trusted with real data or write access.

## Capability Boundaries

Every state should declare the tools and skills it can use. Tool access should be narrowed by state. A gather agent may search and read. A publish agent may write accepted artifacts. An auditor may propose procedure mutation, but applying that mutation should be policy-gated.

The scaffold exposes this through `GET /guardrails` and through the `guardrails` block on `GET /procedure`.

## Budget Boundaries

Budgets should exist at several levels:

- per run,
- per state,
- per day,
- per provider,
- per procedure version.

The auditor should optimize for reliable cheap success. High-tier models should be used for audits, contradiction resolution, and procedure changes, not for every routine loop.

## Mutation Boundaries

Procedure mutations should be:

- versioned,
- diffed,
- reversible,
- attributed to a run and model,
- linked to evidence,
- visible in the UI.

## Data Boundaries

Memory stores may contain private data. The default should be local-first. Remote model providers should receive only the minimum context required for the state, with source ids and summaries preferred over full raw archives.

Structured run output should point to artifacts and sources instead of copying every large object into the run row. That preserves auditability while keeping context and storage pressure visible.
