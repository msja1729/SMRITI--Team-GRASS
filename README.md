# SMRITI — a memory firewall for AI agents

**Secure Memory Runtime: Integrity, Taint-tracking, Immunity**
Team GRASS · AEGIS 2026 · Problem Statement 4: Agent Memory Governance Evaluation
Jyothiradithya (lead), Nihal Pendem, Oruganti Rohan, Perumalla Rithvik · IISc Bengaluru

> **Untrusted memory may inform the agent, never instruct it.**

![SMRITI live demo](docs/img/smriti_live_demo.gif)

---

## The problem, in plain words

AI agents now **remember**: they read emails and web pages, save what they learn, and use
it days later to send emails, pay invoices or deploy code. That memory is a new way in for
attackers.

**Example.** A scammer replies in an invoice thread from a look-alike domain:
*"Northwind's bank account has changed to GB82EVIL…"*. The accounts-payable agent reads
it and saves it. Three weeks later the CFO says *"Pay Northwind's invoice, $48,250"*. The
agent looks in its memory, finds the "new" account, and **pays the attacker**. Nothing
looked wrong at the time: the poison was planted on day 1 and fired on day 25.

Detectors don't solve this: NVIDIA's Safety Guard rates that email as **safe**, because it
contains no obviously bad instruction. It's just a convincing lie.

## The idea

Don't try to guess whether text is malicious. Track **where it came from**.

Every memory gets a **provenance label**: who wrote it and how much it is trusted.

| Source | Trust |
|---|---|
| The user | **HIGH** |
| Internal tools (calendar, CRM) | **MED** |
| Web pages, emails, other agents, anything unknown | **LOW** |

The label is set by **the channel the text came through**, never by what the text says.
A web page that claims "I am the user" still came from the web, so it is LOW. The
attacker can choose the words, but not the label.

## How SMRITI works

SMRITI sits between the agent and its memory, at three points:

```
   text ──► [1 WRITE GATE] ──► memory ──► [2 READ GATE] ──► agent ──► [3 ACTION CHECK] ──► tools
            label by source,               HIGH = instructions         risky action + untrusted
            quarantine instruction-like    rest = quoted data          memory in view = ask a human
            untrusted text, redact PII
```

1. **Write gate.** Labels each memory by its source. Untrusted text that reads like an
   instruction is quarantined: kept for the audit trail, never shown to the agent.
   Personal data (phone numbers, cards, names) is redacted.
2. **Read gate.** Only HIGH memory goes into the agent's instructions. Everything else is
   shown as **quoted data**: escaped, wrapped in a tag with a random one-time code it
   cannot break out of, and labelled with its source and trust.
3. **Action check.** If untrusted memory was in view, a risky action (send, pay, deploy,
   share) needs a human's yes. It asks only when the destination (recipient, bank account,
   repo) is **not** already known from the user or trusted memory, so normal work is not
   interrupted.

Two more pieces:

- **Summaries can't launder poison.** When memories are summarized, the summary's trust is
  computed in code as the **lowest** trust of its inputs, whatever the summarizing LLM writes.
- **Immune Auditor.** After the fact, it re-runs a past request with one memory hidden. If
  the bad action disappears, that memory caused it: it is quarantined together with
  everything derived from it, and the affected actions are listed for review.

Everything **fails closed**: if a check errors, the write is rejected, only trusted memory
is read, or the action waits for a human.

## Results

Pass/fail is always decided by **plain code** on the tool-call log, never by an AI judge.
Brackets show 95% upper bounds.

**MemPoison-Bench** (our benchmark): 3 assistants (finance, DevOps, personal) × 4 attack
styles (direct, indirect, summary laundering, sleeper trigger) × 5 seeds; the poison is
stored in one session and must act in a later one. Agent: NVIDIA Nemotron Nano 9B v2.

| Defence | Attacks that succeeded | Clean tasks done | Tasks done under attack |
|---|---|---|---|
| No defence | 29/60 (48%) | 15/15 | 31/60 |
| Prompt guard + Safety Guard judge | 22/60 (37%) | 12/15 | 31/60 |
| NVIDIA NeMo Guardrails | 14/60 (23%) | 12/15 | 37/60 |
| **SMRITI** | **0/60** (≤ 6.0%) | **15/15** | **51/60** |
| SMRITI with the detector switched off | **0/60** | 15/15 | 46/60 |
| SMRITI with the action check off | 9/60 | 15/15 | 51/60 |
| SMRITI with provenance labels off | 29/60 | 15/15 | 31/60 |

What this shows: **the labels are the foundation.** With the detector off, SMRITI still
stops every attack; with the labels off, it is no better than no defence.

Also: poisoned summaries kept their LOW label 15/15 times · 0 cross-tenant leaks · 0 of
105 clean memories wrongly quarantined · no measurable added latency (NeMo Agent Toolkit
profiler: write p50 1165 ms vs 1171 ms without SMRITI; the time is database round trips).

**Immune Auditor:** 0 innocent memories rolled back; caught 26/28 poisons with no defence
and 9/9 with SMRITI; every caught attack stopped firing after rollback.

**Live bank-change fraud demo**, 10 runs each (Nemotron agent, temperature 0.7):
the attacker was paid 9/10 times with no defence, 8/10 with Safety Guard, 8/10 with NeMo
Guardrails, and **0/10 with SMRITI**.

## Try it

Requires Python 3.12.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/unit                  # 460+ tests, no GPU, model or database needed
```

**Live demo** (needs an NVIDIA API key from build.nvidia.com):

```bash
cp .env.example .env               # put your NVIDIA_API_KEY in .env
python -m smriti.demo              # open http://localhost:8765
```

- **`/`** — write your own scam email (or pick a preset) and watch three agents handle it:
  a plain agent, a prompt guard + Nemotron Safety Guard, and SMRITI. SMRITI's held payment
  is approved or denied by you on screen; the oracle decides who got paid.
- **`/story.html`** — the recorded day-by-day walkthrough (works offline).

The live page uses NVIDIA's hosted `nemotron-3.5-lightning-30b-a3b`. Model answers vary
from run to run, so a single run may not show an attack; the benchmark measures rates.

**Full benchmark** (needs an OpenAI-compatible Nemotron endpoint on `localhost:8000`,
Postgres 16 + pgvector, and `SMRITI_PG_DSN` in `.env`):

```bash
python -m smriti.bench.run --config src/smriti/bench/configs/demo.yaml
python -m smriti.scorecard.report results/demo      # scorecard + charts
```

**Proofs** (optional, Lean 4): `cd lean && lake build`

## Technical details

**NVIDIA stack**

| Role | Model / tool |
|---|---|
| Agent | Nemotron Nano 9B v2 on NVIDIA NIM (benchmark); Nemotron 3.5 Lightning (hosted, live demo) |
| Write-gate judge | Llama-3.1-Nemotron-Safety-Guard-8B v3, called only when the cheap heuristics are unsure |
| Retrieval | nemotron-3.5-embed-1b embeddings |
| Baseline we compare against | NVIDIA NeMo Guardrails |
| Optional stage 1 | NemoGuard JailbreakDetect (integrated as arm A2J; needs a local NIM) |
| Profiling | NeMo Agent Toolkit: SMRITI runs as a NAT workflow |

Other: LangGraph (agent loop), PostgreSQL + pgvector (labelled memory store, ledger, tool
log), Microsoft Presidio (PII), Lean 4 (proofs).

**Formal guarantees (Lean 4, `lean/`).** Proved for all inputs, no `sorry`:
`no_elevation` (nothing below HIGH ever reaches the instructions), `summary_inherits` and
`summary_low_of_low_input` (a summary is as trusted as its least trusted input),
`render_partitions` (every memory lands in exactly one section). A differential test checks
the Python gateway against the Lean definitions on random inputs.

**Repository layout**

| Path | What |
|---|---|
| `src/smriti/gateway/` | Labels, heuristics, write/read gates, action check, consolidation |
| `src/smriti/store/` | Postgres + pgvector store, in-memory fake, schema |
| `src/smriti/agent/` | LangGraph agent, mock tools, prompts |
| `src/smriti/auditor/` | Immune Auditor (counterfactual replay, rollback) |
| `src/smriti/attacks/`, `personas/`, `oracle/` | Benchmark attacks, assistants, pass/fail rules |
| `src/smriti/bench/`, `scorecard/` | Benchmark runner, metrics, scorecard, charts |
| `src/smriti/demo/` | Live web demo |
| `src/smriti/integrations/` | NeMo Agent Toolkit plugin |
| `lean/` | Lean 4 model and proofs |
| `tests/` | Unit tests (CPU only) and integration tests (real model + database) |

## Limits (honest)

- MemPoison-Bench uses our own attack templates; 5 seeds give a 95% upper bound of 6% on
  SMRITI's attack rate (target 5%).
- MED-trust sources (calendar, CRM) are quoted but don't trigger the action check, and
  aren't attacked in the benchmark yet.
- The action check needs a human for unknown destinations; the original version asked on
  every tainted risky action, the destination-aware version (A2S) is not yet benchmarked.
- Already-executed actions (an email already sent) can only be flagged, not undone.

**Roadmap:** an adaptive red team and public prompt-injection benchmarks, an erasure API
with proofs, 8 more attack classes, and deployment packaging (docker-compose, migrations,
row-level security).
