"""Where a risky call sends things, and whether the user sanctioned it.

A destination is *sanctioned* when it appears, as a whole token, in text the user wrote:
the request itself or HIGH-trust (user-written) memory. Shared by the action check
(Tier 2: confirm only unsanctioned destinations) and the Immune Auditor (chase only
unsanctioned destinations). Decided by string comparison in code, never by an LLM.
"""
import re

# Fields that decide *where* a risky action goes; body/subject wording is ignored.
SIGNATURE_FIELDS = ("to", "cc", "bcc", "repo", "ref", "account", "payee")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def destinations(args: dict) -> set[str]:
    """Where a call sends things: recipients, account, payee, repo, ref (normalized)."""
    out: set[str] = set()
    for f in SIGNATURE_FIELDS:
        values = args.get(f)
        for v in values if isinstance(values, (list, tuple)) else [values]:
            if v in (None, ""):
                continue
            text = " ".join(str(v).strip().lower().split())
            m = _EMAIL.search(text)
            out.add(m.group(0) if m else text)
    return out


def is_sanctioned(destination: str, sanctioned_text: str) -> bool:
    """`destination` occurs in `sanctioned_text` as a whole token: `acme.com` does not
    sanction `acme.com.evil.io`, and `x@acme.com` does not sanction `ax@acme.com`."""
    text = " ".join(sanctioned_text.lower().split())
    d = re.escape(destination)
    return re.search(rf"(?<![\w.@+-]){d}(?![\w@+-]|\.[\w-])", text) is not None


def unsanctioned(args: dict, sanctioned_text: str) -> frozenset[str]:
    return frozenset(d for d in destinations(args) if not is_sanctioned(d, sanctioned_text))
