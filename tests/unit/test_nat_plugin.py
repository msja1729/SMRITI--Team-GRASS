"""NeMo Agent Toolkit plugin: registration, key parsing, step tracking (no model)."""
import pytest

pytest.importorskip("nat")

from fakes import FakeEmbedder, FakeJudge, FakePii, FakeSummarizer, ScriptedChat  # noqa: E402
from smriti.bench.episodes import Components  # noqa: E402
from smriti.gateway.gateway import Gateway  # noqa: E402
from smriti.integrations.nat_plugin import (  # noqa: E402
    GATEWAY_OPS, SmritiEpisodeConfig, instrument, parse_key)
from smriti.store.fake_store import FakeStore  # noqa: E402


def test_parse_key():
    s = parse_key(" finance/indirect/A2/3 ")
    assert (s.persona, s.attack, s.arm, s.seed) == ("finance", "indirect", "A2", 3)


def test_config_registered_under_its_type_name():
    assert SmritiEpisodeConfig.static_type() == "smriti_episode"


def test_instrument_wraps_gateway_and_clients_without_changing_behaviour(monkeypatch):
    for op in GATEWAY_OPS:                       # restored by monkeypatch after the test
        monkeypatch.setattr(Gateway, op, getattr(Gateway, op))
    parts = Components(ScriptedChat([]), FakeSummarizer(), FakeEmbedder(), FakeJudge(), FakePii())
    instrument(parts)
    instrument(parts)                            # idempotent for the gateway class
    assert all(getattr(Gateway, op)._smriti_tracked for op in GATEWAY_OPS)
    gw = Gateway(FakeStore(), parts.embedder, parts.pii, parts.judge, parts.summarizer)
    assert gw.write("Invoices go out on the 1st.", "user", "a", "s").status == "stored"
    assert "Invoices" in gw.read("invoices", "a").instructions


def _ev(kind, name, fid, parent, t, uuid, data=None):
    return {"function_ancestry": {"function_name": name, "function_id": fid, "parent_id": parent},
            "payload": {"event_type": kind, "event_timestamp": str(t), "UUID": uuid,
                        "data": data}}


def test_overhead_analysis_subtracts_nested_model_time():
    from smriti.scorecard.nat_profile import analyse
    steps = [
        _ev("WORKFLOW_START", "root", "root", None, 0.0, "w", "{'input': 'finance/direct/A2/0'}"),
        _ev("FUNCTION_START", "gateway.write", "g1", "root", 1.000, "u1"),
        _ev("FUNCTION_START", "nvidia.embed", "e1", "g1", 1.010, "u2"),
        _ev("FUNCTION_END", "nvidia.embed", "e1", "g1", 1.310, "u2"),     # 300 ms model
        _ev("FUNCTION_END", "gateway.write", "g1", "root", 1.350, "u1"),  # 350 ms total
        _ev("FUNCTION_START", "agent_llm.chat", "c1", "root", 2.0, "u3"),
        _ev("FUNCTION_END", "agent_llm.chat", "c1", "root", 3.5, "u3"),   # 1500 ms
    ]
    r = analyse([{"request_number": 0, "intermediate_steps": steps}])["A2"]
    assert r["write_total_ms"]["p50"] == pytest.approx(350, abs=1)
    assert r["write_own_ms"]["p50"] == pytest.approx(50, abs=1)
    assert r["agent_llm_ms"]["p50"] == pytest.approx(1500, abs=1)
