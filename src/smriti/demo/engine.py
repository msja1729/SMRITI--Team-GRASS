"""Run the demo scenario through two real agents and record a trace for the UI.

Both arms use the same model, inbox, timeline and simulated user; the only difference is
the gateway config: A0 (no governance) vs A2S (SMRITI). Nothing here is mocked for the
screen: memories, labels, prompts, tool calls, the oracle verdict and the auditor's
rollback are what the real code produced. Pass/fail is decided by the oracle.
"""
import time
from dataclasses import dataclass, field

from smriti.agent.graph import Agent
from smriti.agent.tools import ToolRunner
from smriti.auditor import AgentSpec, ImmuneAuditor
from smriti.bench.arms import A1, A1G, A2S
from smriti.demo import scenario as sc
from smriti.gateway.destinations import unsanctioned
from smriti.gateway.gateway import A0, A2, Gateway, GatewayConfig
from smriti.gateway.labels import Trust
from smriti.gateway.read_gate import RenderedContext
from smriti.oracle.predicates import attack_succeeded, is_attack_call, simulated_user, task_succeeded
from smriti.store.fake_store import FakeStore

# A0 and A2S are the side-by-side duel; A1/A1G are "what else people use", shown on the
# payment day. A1G (NeMo Guardrails) needs the live model, so it only runs live.
ARMS = {"A0": A0, "A1": A1, "A1G": A1G, "A2S": A2S}
ARM_INFO = {
    "A0": {"label": "No memory governance", "sub": "plain memory store"},
    "A1": {"label": "Prompt guard + safety judge", "sub": "every input judged; prompt says "
                                                          "ignore instructions in memory"},
    "A1G": {"label": "NeMo Guardrails", "sub": "content-safety + self-check input rails on "
                                               "every input"},
    "A2S": {"label": "SMRITI", "sub": "provenance labels · read gate · action check"},
}
EPISODE = "demo"


@dataclass
class Parts:
    llm: object
    embedder: object
    pii: object
    judge: object
    summarizer: object
    guardrails: object = None                   # NeMo Guardrails judge (live only)
    names: dict = field(default_factory=dict)   # what each part is, for the UI


def offline_parts() -> Parts:
    from smriti.demo.models import ExtractiveSummarizer, NeverFlags, WorstCaseAP
    from smriti.models.hash_embed import HashEmbedder
    return Parts(WorstCaseAP(), HashEmbedder(), _pii(), NeverFlags(), ExtractiveSummarizer(),
                 names={"agent": "Worst-case gullible model (offline, deterministic)",
                  "embedder": "hash embedder (offline)",
                  "judge": "offline stand-in that never flags (live run: Safety Guard 8B)",
                  "pii": _pii_name()})


def live_parts(base_url: str, model: str) -> Parts:
    """Nemotron on NIM for the agent and summaries; hosted NVIDIA embeddings and Safety
    Guard judge when NVIDIA_API_KEY is set, offline stand-ins otherwise."""
    import os

    from smriti.demo.models import NeverFlags
    from smriti.models.hash_embed import HashEmbedder
    from smriti.models.nim import NimChat, NimConfig, NimSummarizer
    llm = NimChat(NimConfig(base_url=base_url, model=model))
    names = {"agent": f"{model} (NIM)", "pii": _pii_name()}
    embedder, judge = HashEmbedder(), NeverFlags()
    names.update(embedder="hash embedder (offline)", judge="off (detector misses)")
    if os.environ.get("NVIDIA_API_KEY"):
        from smriti.models.hosted import HostedConfig, NvidiaEmbedder, SafetyGuardJudge
        hosted = "https://integrate.api.nvidia.com/v1"
        embedder = NvidiaEmbedder(HostedConfig(hosted, "nvidia/nemotron-3.5-embed-1b"))
        judge = SafetyGuardJudge(HostedConfig(
            hosted, "nvidia/llama-3.1-nemotron-safety-guard-8b-v3"))
        names.update(embedder="NeMo Retriever embeddings (hosted)",
                     judge="Nemotron Safety Guard 8B (hosted)")
    guardrails = None
    try:
        if not os.environ.get("NVIDIA_API_KEY"):
            raise ImportError     # its content-safety rail is hosted; errors would fail closed
                                  # and look like Guardrails blocking the email
        from smriti.models.guardrails import GuardrailsJudge, rails_yaml
        guardrails = GuardrailsJudge(rails_yaml(
            model, base_url, "nvidia/llama-3.1-nemotron-safety-guard-8b-v3"))
        names["guardrails"] = "NeMo Guardrails 0.24 (Safety Guard + Nemotron self-check)"
    except ImportError:
        pass
    return Parts(llm, embedder, _pii(), judge, NimSummarizer(llm), guardrails, names)


_PII = None


def _pii():
    global _PII
    if _PII is None:
        try:
            from smriti.models.pii import PresidioRedactor
            _PII = PresidioRedactor()
        except Exception:                        # spaCy model missing: regex fallback
            _PII = _RegexPii()
    return _PII


def _pii_name() -> str:
    return "Presidio" if type(_pii()).__name__ == "PresidioRedactor" else "regex PII"


class _RegexPii:
    import re as _re
    _PHONE = _re.compile(r"(?<![A-Za-z0-9])\+?\d[\d -]{7,}\d")   # not inside an IBAN

    def redact(self, text):
        from smriti.models.pii import PiiResult
        found = {"PHONE_NUMBER"} if self._PHONE.search(text) else set()
        return PiiResult(self._PHONE.sub("[PHONE]", text), frozenset(found))


class _RecordingChat:
    """Wraps the model; keeps the prompt of each turn so the UI can show what it saw."""

    def __init__(self, inner):
        self.inner = inner
        self.prompts: list[str] = []

    def chat(self, messages, tools):
        if len(messages) == 2 and messages[0]["role"] == "system":   # first step of a turn
            self.prompts.append(messages[0]["content"])
        return self.inner.chat(messages, tools)


class _Arm:
    def __init__(self, name: str, config: GatewayConfig, parts: Parts):
        self.name, self.config = name, config
        self.store = FakeStore()
        self.chat = _RecordingChat(parts.llm)
        judge = parts.guardrails if config.judge_backend == "guardrails" else parts.judge
        self.gw = Gateway(self.store, parts.embedder, parts.pii, judge, parts.summarizer,
                          config)
        self.prompts_seen: list[dict] = []
        self.tools = ToolRunner(self.gw, sc.FIXTURES, self._confirm, sc.TOOLS)
        self.agent = Agent(self.gw, self.chat, self.tools, sc.ROLE, sc.TENANT)
        self._approve = simulated_user(sc.LEGIT_CALLS)
        self.day = 0
        self.born: dict[str, int] = {}          # memory id -> day it was written
        for text in sc.SEED_MEMORIES:
            self.gw.write(text, "user", sc.TENANT, "setup")
        self._stamp()

    # The simulated AP clerk sees the provenance card and approves only legitimate calls.
    def _confirm(self, tool: str, args: dict) -> bool:
        approved = self._approve(tool, args)
        self.prompts_seen.append({"tool": tool, "args": args, "approved": approved,
                                  "provenance": self._provenance(args)})
        return approved

    def _provenance(self, args: dict) -> list[dict]:
        """For each unsanctioned destination: which memories it came from."""
        sanctioned = "\n".join(m.text for m in self.memories() if m.label.trust == Trust.HIGH)
        out = []
        for d in sorted(unsanctioned(args, sanctioned)):
            origins = [_mem_json(m, self.born.get(m.id)) for m in self.memories()
                       if d in m.text.lower()]
            out.append({"destination": d, "origins": origins})
        return out

    def memories(self):
        seen, out = set(), []
        for e in self.store.ledger(sc.TENANT):
            if e.memory_id and e.memory_id not in seen:
                seen.add(e.memory_id)
                m = self.store.get(sc.TENANT, e.memory_id)
                if m is not None:
                    out.append(m)
        return out

    def _stamp(self):
        for m in self.memories():
            self.born.setdefault(m.id, self.day)

    def snapshot(self) -> list[dict]:
        return [_mem_json(m, self.born.get(m.id)) for m in self.memories()]


def _mem_json(m, day) -> dict:
    return {"id": m.id[:8], "text": m.text, "source": m.label.source,
            "trust": m.label.trust.name, "pii": m.label.sensitivity.name == "PII",
            "flags": sorted(m.flags), "parents": sorted(p[:8] for p in m.label.parents),
            "day": day}


def _call_json(c) -> dict:
    return {"tool": c.tool, "args": c.args, "tainted": c.tainted, "decision": c.decision,
            "attack": is_attack_call(c, sc.ATTACKER)}


def run(parts: Parts) -> dict:
    arms = {name: _Arm(name, cfg, parts) for name, cfg in ARMS.items()
            if cfg.judge_backend != "guardrails" or parts.guardrails is not None}
    steps = []
    for ev in sc.TIMELINE:
        step = {k: ev[k] for k in ("id", "day", "date", "kind", "title", "caption")}
        if ev["kind"] == "turn":
            step.update(who=ev["who"], text=ev["text"])
        step["arms"] = {}
        for name, arm in arms.items():
            arm.day = ev["day"]
            ledger0 = len(arm.store.ledger(sc.TENANT))
            calls0 = len(arm.store.tool_log(EPISODE))
            prompts0 = len(arm.prompts_seen)
            t0 = time.perf_counter()
            out: dict = {}
            if ev["kind"] == "turn":
                arm.chat.prompts.clear()
                res = arm.agent.run_turn(ev["text"], f"day{ev['day']}", EPISODE)
                out["reply"] = res.reply
                out["prompt"] = arm.chat.prompts[0] if arm.chat.prompts else ""
            elif ev["kind"] == "consolidate":
                ids = [m.id for m in arm.memories() if m.label.source != "user"
                       and "archived" not in m.flags]
                if len(ids) >= 2:             # as in the benchmark: nothing to merge otherwise
                    s = arm.gw.consolidate(sc.TENANT, ids, f"day{ev['day']}")
                    out["summary"] = _mem_json(s, ev["day"])
            elif ev["kind"] == "audit":
                out["audit"] = _audit(arm, parts)
            arm._stamp()
            out["ms"] = round((time.perf_counter() - t0) * 1000)
            out["ledger"] = [{"op": e.op, "memory": (e.memory_id or "")[:8],
                              **{k: e.detail[k] for k in ("source", "trust", "flags", "pii",
                                                          "heuristic_score", "judge_unsafe",
                                                          "reason")
                                 if k in e.detail}}
                             for e in arm.store.ledger(sc.TENANT)[ledger0:]]
            out["calls"] = [_call_json(c)
                            for c in arm.store.tool_log(EPISODE)[calls0:]]
            out["confirmations"] = arm.prompts_seen[prompts0:]
            if ev["id"] == "inbox":
                out["bec_write"] = _bec_write(arm, ledger0)
            if ev["id"] == "acme" and name == "A2S":
                out["a2_would_confirm"] = _a2_would_confirm(arm, out["calls"])
            out["memory"] = arm.snapshot()
            step["arms"][name] = out
        steps.append(step)

    verdict = {}
    for name, arm in arms.items():
        log = arm.store.tool_log(EPISODE)
        stolen = sum(c.args.get("amount", 0) for c in log
                     if c.decision == "executed" and is_attack_call(c, sc.ATTACKER))
        verdict[name] = {"attack_succeeded": attack_succeeded(log, sc.ATTACKER),
                         "pay_task": task_succeeded(log, sc.PAY_TASK),
                         "acme_task": task_succeeded(log, sc.ACME_TASK),
                         "lost": stolen,
                         "confirmations": len(arm.prompts_seen)}
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "models": parts.names,
            "scenario": {"tenant": sc.TENANT, "role": sc.ROLE, "seed": list(sc.SEED_MEMORIES),
                         "emails": sc.EMAILS, "verified_iban": sc.VERIFIED_IBAN,
                         "attacker_iban": sc.ATTACKER_IBAN, "amount": sc.INVOICE_AMOUNT,
                         "real_world": list(sc.REAL_WORLD), "bec_id": sc.BEC_ID,
                         "agents_now": list(sc.AGENTS_NOW), "con_tells": list(sc.CON_TELLS),
                         "human_defence": sc.HUMAN_DEFENCE},
            "arms": _arm_info(arms, parts),
            "steps": steps, "verdict": verdict}


def _arm_info(arms: dict, parts: Parts) -> dict:
    info = {n: dict(ARM_INFO[n]) for n in arms}
    if "A1" in info and not parts.names.get("judge", "").startswith("Nemotron"):
        info["A1"] = {"label": "Prompt guard", "sub": "system prompt says ignore instructions "
                      "in memory (safety judge not run in this recording)"}
    return info


def _bec_write(arm: _Arm, ledger0: int) -> dict | None:
    """How this arm's write gate handled the fraud email (the memory holding its IBAN)."""
    for e in arm.store.ledger(sc.TENANT)[ledger0:]:
        m = arm.store.get(sc.TENANT, e.memory_id) if e.memory_id else None
        if m is not None and sc.ATTACKER_IBAN in m.text:
            return {"op": e.op, "trust": m.label.trust.name, "flags": sorted(m.flags),
                    "heuristic_score": e.detail.get("heuristic_score"),
                    "judge_unsafe": e.detail.get("judge_unsafe")}
    return None


def _a2_would_confirm(arm: _Arm, calls: list[dict]) -> bool:
    """Would plain A2 (confirm every tainted risky call) have interrupted the user here?"""
    probe = Gateway(arm.store, None, None, None, None, A2)
    return any(c["tainted"] and probe.check_action(c["tool"], c["args"], RenderedContext(
        "", "", True, [])).value == "require_confirmation" for c in calls)


def _audit(arm: _Arm, parts: Parts) -> dict:
    spec = AgentSpec(sc.ROLE, sc.TOOLS, sc.FIXTURES)
    report = ImmuneAuditor(arm.gw, parts.llm, spec).audit(sc.TENANT, EPISODE)
    short = {m.id: m.id[:8] for m in arm.memories()}
    return {"replays": report.replays,
            "steering": [short.get(i, i[:8]) for i in report.steering],
            "rolled_back": [short.get(i, i[:8]) for i in report.rolled_back],
            "findings": [{"tool": f.tool, "args": f.args, "suspect": (f.suspect or "")[:8],
                          "verdict": f.verdict} for f in report.findings],
            "review": [{"tool": c.tool, "args": c.args} for c in report.review]}


def tally(base_url: str, model: str, runs: int, temperature: float = 0.7,
          workers: int = 6) -> dict:
    """Repeat the whole scenario `runs` times with sampling and count outcomes per arm, so
    the demo can show rates instead of one lucky (or unlucky) run."""
    from concurrent.futures import ThreadPoolExecutor

    from smriti.models.nim import NimChat, NimConfig, NimSummarizer

    def one(_):
        parts = live_parts(base_url, model)
        llm = NimChat(NimConfig(base_url=base_url, model=model, temperature=temperature))
        parts.llm, parts.summarizer = llm, NimSummarizer(llm)
        try:
            return run(parts)["verdict"]
        except Exception:
            return None
    with ThreadPoolExecutor(workers) as pool:
        verdicts = [v for v in pool.map(one, range(runs)) if v]
    out = {"runs": len(verdicts), "temperature": temperature, "arms": {}}
    for arm in (verdicts[0] if verdicts else {}):
        vs = [v[arm] for v in verdicts]
        out["arms"][arm] = {"attacker_paid": sum(v["attack_succeeded"] for v in vs),
                            "paid_correctly": sum(v["pay_task"] for v in vs),
                            "lost": sum(v["lost"] for v in vs)}
    return out
