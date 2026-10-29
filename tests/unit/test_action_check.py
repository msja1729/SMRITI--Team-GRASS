import pytest

from fakes import FakeEmbedder, FakeJudge, FakePii, FakeSummarizer
from smriti.gateway.action_check import ActionDecision, check_action
from smriti.gateway.gateway import Gateway, GatewayConfig
from smriti.gateway.read_gate import RenderedContext
from smriti.store.fake_store import FakeStore

RISKY = GatewayConfig().risky_tools


@pytest.mark.parametrize("tool", sorted(RISKY))
def test_tainted_risky_requires_confirmation(tool):
    assert check_action(tool, tainted=True, risky_tools=RISKY) is ActionDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize("tool,tainted", [
    ("read_web", True), ("calendar_add", True), ("send_email", False), ("pay", False)])
def test_other_cases_allowed(tool, tainted):
    assert check_action(tool, tainted=tainted, risky_tools=RISKY) is ActionDecision.ALLOW


def test_default_risky_tools():
    assert RISKY == {"send_email", "pay", "deploy", "share_file"}


def ctx(tainted):
    return RenderedContext("", "", tainted, [])


def test_gateway_check_action_and_switch():
    gw = Gateway(FakeStore(), FakeEmbedder(), FakePii(), FakeJudge(), FakeSummarizer())
    assert gw.check_action("pay", {}, ctx(True)) is ActionDecision.REQUIRE_CONFIRMATION
    off = Gateway(FakeStore(), FakeEmbedder(), FakePii(), FakeJudge(), FakeSummarizer(),
                  GatewayConfig(action_check=False))
    assert off.check_action("pay", {}, ctx(True)) is ActionDecision.ALLOW


# -- Tier 2 (A2S): confirm only unsanctioned destinations ---------------------------------

from smriti.bench.arms import A2S  # noqa: E402
from smriti.gateway.destinations import is_sanctioned, unsanctioned  # noqa: E402

VERIFIED = "Pay Northwind Supplies to IBAN GB29NWBK60161331926819 (verified by the controller)."


def sctx(sanctioned, tainted=True):
    return RenderedContext("", "", tainted, [], sanctioned)


def a2s():
    return Gateway(FakeStore(), FakeEmbedder(), FakePii(), FakeJudge(), FakeSummarizer(), A2S)


def test_sanctioned_destination_needs_no_confirmation():
    args = {"payee": "Northwind Supplies", "account": "GB29NWBK60161331926819", "amount": 1}
    assert a2s().check_action("pay", args, sctx(VERIFIED)) is ActionDecision.ALLOW


def test_destination_only_in_low_memory_needs_confirmation():
    args = {"payee": "Northwind Supplies", "account": "GB33EVIL00000000666666", "amount": 1}
    assert a2s().check_action("pay", args, sctx(VERIFIED)) is ActionDecision.REQUIRE_CONFIRMATION


def test_one_unsanctioned_recipient_is_enough():
    args = {"to": ["billing@acme.com"], "cc": ["audit@evil.io"], "subject": "s", "body": "b"}
    ctx_ = sctx("Acme Corp's billing contact is billing@acme.com.")
    assert a2s().check_action("send_email", args, ctx_) is ActionDecision.REQUIRE_CONFIRMATION
    del args["cc"]
    assert a2s().check_action("send_email", args, ctx_) is ActionDecision.ALLOW


def test_no_destination_is_never_sanctioned():
    assert a2s().check_action("pay", {}, sctx(VERIFIED)) is ActionDecision.REQUIRE_CONFIRMATION


def test_default_a2_still_confirms_every_tainted_risky_call():
    gw = Gateway(FakeStore(), FakeEmbedder(), FakePii(), FakeJudge(), FakeSummarizer())
    args = {"payee": "Northwind Supplies", "account": "GB29NWBK60161331926819", "amount": 1}
    assert gw.check_action("pay", args, sctx(VERIFIED)) is ActionDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize("dest,text,ok", [
    ("billing@acme.com", "contact billing@acme.com.", True),
    ("billing@acme.com.evil.io", "contact billing@acme.com.", False),   # lookalike suffix
    ("xbilling@acme.com", "contact billing@acme.com", False),          # lookalike prefix
    ("acme.com", "write to billing@acme.com", False),                  # part of an address
    ("gb29nwbk60161331926819", "IBAN GB29NWBK60161331926819.", True),
    ("gb29nwbk6016133192681", "IBAN GB29NWBK60161331926819", False),   # truncated
    ("northwind supplies", "Pay  Northwind\nSupplies", True),          # whitespace-normalized
])
def test_is_sanctioned_matches_whole_tokens(dest, text, ok):
    assert is_sanctioned(dest, text) is ok


def test_sanctioned_text_comes_from_high_memory_only(tmp_path):
    from smriti.gateway.labels import Label, Memory, Sensitivity, Trust
    from smriti.gateway.read_gate import render

    def mem(text, trust):
        return Memory(text, text, Label("x", "t", trust, Sensitivity.PUBLIC, frozenset()),
                      frozenset(), "s", 0.0)
    for escaping in (True, False):
        ctx_ = render([mem(VERIFIED, Trust.HIGH), mem("New IBAN GB33EVIL00000000666666", Trust.LOW),
                       mem("CRM: GB11MED000000000000", Trust.MED)], "n", escaping=escaping)
        assert unsanctioned({"account": "GB33EVIL00000000666666"}, ctx_.sanctioned)
        assert unsanctioned({"account": "GB11MED000000000000"}, ctx_.sanctioned)
        assert not unsanctioned({"account": "GB29NWBK60161331926819"}, ctx_.sanctioned)


def test_a2s_explains_which_destination_was_refused():
    args = {"payee": "Northwind Supplies", "account": "GB33EVIL00000000666666", "amount": 1}
    reason = a2s().denial_reason(args, sctx(VERIFIED))
    assert reason.startswith("The user denied this action.") and "gb33evil00000000666666" in reason
    plain = Gateway(FakeStore(), FakeEmbedder(), FakePii(), FakeJudge(), FakeSummarizer())
    assert plain.denial_reason(args, sctx(VERIFIED)) == "The user denied this action."
