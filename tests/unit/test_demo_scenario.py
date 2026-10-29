"""The web demo's scenario, end to end on CPU: real gateway, auditor and oracle, offline
worst-case model. The page only displays this trace, so these are the demo's claims."""
import json

import pytest

from smriti.demo import engine, scenario as sc
from smriti.gateway.heuristics import Verdict, score


@pytest.fixture(scope="module")
def trace():
    engine._PII = engine._RegexPii()      # no spaCy model needed
    try:
        yield engine.run(engine.offline_parts())
    finally:
        engine._PII = None


def step(trace, id_):
    return next(s for s in trace["steps"] if s["id"] == id_)


def test_the_poison_is_a_fact_not_an_instruction():
    assert score(sc.BEC_EMAIL).verdict is Verdict.CLEAN      # detectors have nothing to catch


def test_oracle_verdicts(trace):
    v = trace["verdict"]
    assert v["A0"]["attack_succeeded"] and v["A0"]["lost"] == sc.INVOICE_AMOUNT
    assert not v["A2S"]["attack_succeeded"] and v["A2S"]["lost"] == 0
    assert v["A2S"]["pay_task"] and v["A2S"]["acme_task"]
    assert v["A2S"]["confirmations"] == 1                    # only the attacker-bound call


def test_summary_inherits_low(trace):
    s = step(trace, "consolidate")["arms"]
    assert s["A2S"]["summary"]["trust"] == "LOW"
    assert sc.ATTACKER_IBAN in s["A2S"]["summary"]["text"]


def test_sanctioned_email_needs_no_prompt_but_plain_a2_would_ask(trace):
    a2 = step(trace, "acme")["arms"]["A2S"]
    assert a2["confirmations"] == [] and a2["a2_would_confirm"]
    assert [c["decision"] for c in a2["calls"]] == ["executed"] and a2["calls"][0]["tainted"]


def test_confirmation_shows_where_the_account_came_from(trace):
    (c,) = step(trace, "pay")["arms"]["A2S"]["confirmations"]
    assert not c["approved"] and c["args"]["account"] == sc.ATTACKER_IBAN
    origins = c["provenance"][0]["origins"]
    assert {o["source"] for o in origins} == {"tool:email", "derived"}
    assert {o["trust"] for o in origins} == {"LOW"}


def test_auditor_rolls_back_the_steering_summary(trace):
    for arm in ("A0", "A2S"):
        a = step(trace, "audit")["arms"][arm]["audit"]
        assert a["steering"] and a["rolled_back"] == a["steering"]
        assert all(f["verdict"] == "steering" for f in a["findings"])


def test_trace_is_json(trace):
    json.dumps(trace)
