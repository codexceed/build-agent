# build-agent

Agent-driven client-intake and evidence-production system for real estate / construction (site work).

It turns inbound client email into the right kind of work — a **desktop constraints screen** for a site, with a human approving every route and every release — while keeping a reproducible, auditable evidence trail.

## What it does

Inbound mail is classified into one of: a **new deliverable**, an **existing-deliverable follow-up**, or a generic **response**. New deliverables are routed by their spatial input:

| Deliverable | Question | Input |
|---|---|---|
| **Due diligence** (MVP: desktop constraints screen) | Is *this* site viable, and what are the power / zoning / environmental risks? | a site polygon |
| **Site sourcing** *(post-MVP)* | Which sites across a market meet a brief? | a search area + criteria |
| **Test fit** *(post-MVP)* | What can plausibly be built on *this* site? | a site boundary + programme |

## Design principles

- **End-to-end reliability over local cleverness** — every stage validated, versioned, reproducible.
- **Bias against false classification** — the model *proposes*; a human confirms every route. No autonomous confidence-threshold routing until calibrated on real mail.
- **Human authority on release** — automate the analysis, gate the send.

See [`AGENT_DRIVEN_REAL_ESTATE_WORKFLOW.md`](AGENT_DRIVEN_REAL_ESTATE_WORKFLOW.md) for the full design and [`docs/adr/`](docs/adr/) for the architecture decision records (classification strategy, evidence caching).

## Status

Early build. **Slice 1 — authorised intake foundation** is implemented: the `sender → client → engagement → case` authorisation gate, an append-only audit trail, and a human triage queue. Denials disclose no case metadata.

Remaining MVP slices: classify→confirm, validated evidence retrieval (planning/zoning, environmental, utility/RTO adapters), and the findings ledger with snapshot-only follow-up.

## Development

Python 3.14, managed with [uv](https://docs.astral.sh/uv/). The `Makefile` is the canonical command interface.

```bash
make install     # sync the venv with project + dev deps
make check       # lint (ruff + pylint) + typecheck (pyright) + tests (pytest)
make test        # run the test suite
make help        # list all targets
```

All code passes ruff, pylint (Google-style docstrings enforced), and strict pyright. Changes follow a tests-first (TDD) workflow; see [`CLAUDE.md`](CLAUDE.md) for the working agreements.
