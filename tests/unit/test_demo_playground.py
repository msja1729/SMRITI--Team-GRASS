"""The playground's oracle: what counts as the attacker's destination (no model needed)."""
from smriti.demo import playground as pg
from smriti.demo import scenario as sc


def test_goal_is_what_the_email_adds_not_what_contoso_has():
    p = pg.PRESETS[0]
    goal = pg.attack_goal(pg.email_text(p["from"], p["subject"], p["body"]))
    assert goal.accounts == {sc.ATTACKER_IBAN}
    assert goal.domains == {"northwlnd.example"}


def test_known_accounts_and_spaced_ibans():
    email = pg.email_text("x <a@vendor.example>", "s",
                          f"Old: {sc.VERIFIED_IBAN}. New: GB71 MONZ 0443 5112 3456 78. "
                          "Questions: billing@acme.example")
    goal = pg.attack_goal(email)
    assert goal.accounts == {"GB71MONZ04435112345678"}
    assert goal.domains == {"vendor.example"}          # acme.example is on file


def test_presets_are_complete():
    for p in pg.PRESETS:
        assert {"id", "label", "from", "subject", "body", "request"} <= p.keys()
        assert p["request"]
