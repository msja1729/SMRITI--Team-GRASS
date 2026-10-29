"""SMRITI overhead from NeMo Agent Toolkit profiler traces (Tier 2).

Reads `all_requests_profiler_traces.json` written by `nat eval` with the smriti_episode
workflow. For each gateway operation it reports total time and *own* time (total minus the
model calls nested inside it: NVIDIA embeddings/judge, and the Nemotron summarizer inside
consolidation), next to the agent's LLM time, per arm.

    python -m smriti.scorecard.nat_profile results/nat_profile
"""
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

GATEWAY = "gateway."
MODEL = "nvidia."
AGENT_LLM = "agent_llm.chat"


def _pctl(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _input_of(step: dict) -> str | None:
    data = step["payload"].get("data")
    try:
        data = ast.literal_eval(data) if isinstance(data, str) else data
    except (ValueError, SyntaxError):
        return None
    return (data or {}).get("input")


def spans(steps: list[dict]) -> list[dict]:
    """Pair START/END events into spans with name, id, parent id and duration (ms)."""
    starts: dict[str, dict] = {}
    out = []
    for step in steps:
        p, anc = step["payload"], step["function_ancestry"]
        kind = p["event_type"]
        if kind == "FUNCTION_START":
            starts[p["UUID"]] = {"name": anc["function_name"], "id": anc["function_id"],
                                 "parent": anc.get("parent_id"),
                                 "t0": float(p["event_timestamp"])}
        elif kind == "FUNCTION_END" and p["UUID"] in starts:
            s = starts.pop(p["UUID"])
            s["ms"] = (float(p["event_timestamp"]) - s["t0"]) * 1000
            out.append(s)
    return out


def analyse(requests: list[dict]) -> dict:
    by_arm: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for req in requests:
        steps = req["intermediate_steps"]
        key = next((_input_of(s) for s in steps
                    if s["payload"]["event_type"] == "WORKFLOW_START"), None)
        arm = key.split("/")[2] if key and key.count("/") == 3 else "unknown"
        ss = spans(steps)
        model_ms: dict[str, float] = defaultdict(float)
        for s in ss:
            if s["name"].startswith(MODEL) or s["name"] == AGENT_LLM:
                model_ms[s["parent"]] += s["ms"]
        for s in ss:
            if s["name"].startswith(GATEWAY):
                op = s["name"][len(GATEWAY):]
                by_arm[arm][f"{op}_total_ms"].append(s["ms"])
                by_arm[arm][f"{op}_own_ms"].append(s["ms"] - model_ms.get(s["id"], 0.0))
            elif s["name"] == AGENT_LLM:
                by_arm[arm]["agent_llm_ms"].append(s["ms"])
    return {arm: {name: {"n": len(xs), "p50": _pctl(xs, 0.5), "p95": _pctl(xs, 0.95),
                         "sum": sum(xs)} for name, xs in sorted(series.items())}
            for arm, series in sorted(by_arm.items())}


def render(result: dict) -> str:
    lines = ["# SMRITI overhead (NeMo Agent Toolkit profiler)", "",
             "Own = gateway time minus nested model calls (NVIDIA embeddings/judge, the "
             "summarizer's LLM call). "
             "Times in ms; store round trips included (paid by every arm).", "",
             "| Arm | Step | n | p50 | p95 |", "|---|---|---|---|---|"]
    for arm, series in result.items():
        for name, st in series.items():
            lines.append(f"| {arm} | {name} | {st['n']} | {st['p50']:.1f} | {st['p95']:.1f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    run_dir = Path((argv or sys.argv[1:])[0])
    result = analyse(json.loads((run_dir / "all_requests_profiler_traces.json").read_text()))
    (run_dir / "smriti_overhead.json").write_text(json.dumps(result, indent=2))
    (run_dir / "smriti_overhead.md").write_text(render(result))
    print(render(result))


if __name__ == "__main__":
    main()
