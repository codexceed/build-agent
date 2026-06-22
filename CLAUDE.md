# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

Agent-driven client-intake and evidence-production system for real estate / construction (site work). The design is specified in:

- [`AGENT_DRIVEN_REAL_ESTATE_WORKFLOW.md`](AGENT_DRIVEN_REAL_ESTATE_WORKFLOW.md) — the workflow design doc.
- [`docs/adr/`](docs/adr/) — architecture decision records (ADR-0001 classification strategy, ADR-0002 evidence caching).

Read the design doc and relevant ADRs before changing behaviour; keep code and docs consistent, and update the ADRs when a decision changes.

Python 3.14 project managed with uv. Slice 1 (authorised intake foundation) is implemented under `src/intake`; the conventions below apply to every change.

## Working agreements (must follow)

These three rules are mandatory for every code change in this repo.

### 1. Consult Codex before designing

**Before proposing any code change or major feature**, consult Codex (high reasoning effort) to pressure-test the design. Aim for a solution that is **minimal, elegant, human-readable, and optimal, with ample observability** (structured logging, metrics, and traceability — consistent with the design doc's reproducibility/provenance principles).

- Invoke via the `codex` skill, or run `codex exec` directly with high effort:

  ```bash
  codex exec -c model_reasoning_effort=high "Review this design for a minimal, elegant, observable implementation: <design summary + constraints>"
  ```

- Summarise Codex's recommendation to the user and fold the agreed points into the plan before writing code.

### 2. TDD guardrails — tests first, user-approved

**For each code-change proposal**, before implementing:

1. Generate a **list of unit tests** with a **simple one-line description** for each (what behaviour it pins down, including edge/failure cases).
2. **Prompt the user to check that list** and wait for approval / edits.
3. Once approved, **write those tests first as failing ("breaking") tests** — they must fail for the right reason against the unimplemented code.
4. Only then implement, until the approved tests pass. Do not add behaviour that isn't covered by an approved test without going back to step 1.

These tests are the guardrails — never skip them or weaken them to make code pass.

### 3. Lint and type-check everything

All Python must pass **ruff**, **pylint**, and **pyright** before a change is considered done. Pylint enforces **Google-style docstrings** (presence + documented params/returns/raises). Docstrings are required on every module, public class, and public function.

## Commands

The **`Makefile` is the canonical command interface** — use these targets rather than invoking tools directly. Tool configuration lives in `pyproject.toml`, but you should not run the tools by hand; run the make target.

```bash
make install     # sync the venv with project + dev deps (uv)
make lint        # ruff + pylint (pylint enforces Google docstrings)
make format      # ruff format + ruff --fix
make typecheck   # pyright (strict)
make test        # pytest
make check       # lint + typecheck + test — run before finishing any change
make run         # run the application entrypoint
make help        # list all targets
```

When you add a recurring command, add it as a Makefile target and reference it here — keep this list and the Makefile in sync.

The environment is managed by **uv** (the venv at `.venv` has no `pip`); the make targets wrap `uv run`/`uv sync`.
