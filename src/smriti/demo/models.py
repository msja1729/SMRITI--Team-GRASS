"""Offline stand-ins so the demo runs on a laptop with no GPU and no network.

`WorstCaseAP` is a deterministic, deliberately gullible accounts-payable model. It does
the user's task, and when memory holds more than one account for a payee it trusts the
one described as new/changed/updated, which is exactly the mistake business email
compromise exploits in people. It never reads trust labels, so whatever SMRITI achieves
with it comes from the gateway, not from the model behaving well. The live mode uses
Nemotron on NIM instead.
"""
import re

from smriti.models.nim import LlmReply, LlmToolCall

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_FRESH = re.compile(r"\b(new|changed|updated|effective|moved)\b", re.IGNORECASE)


def _context(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages)


def pick_account(context: str, tried: set[str]) -> str | None:
    """The account the gullible model would pay: one marked as new beats an older one."""
    best, best_score = None, -1
    for line in context.splitlines():
        for iban in _IBAN.findall(line):
            if iban in tried:
                continue
            score = 1 if _FRESH.search(line) else 0
            if score > best_score:
                best, best_score = iban, score
    return best


class WorstCaseAP:
    def chat(self, messages: list[dict], tools: list[dict]) -> LlmReply:
        user = next(m["content"] for m in messages if m["role"] == "user")
        last = messages[-1]
        ctx = _context(messages)
        if last["role"] == "user":
            return self._first_step(user, ctx)
        if last["role"] == "tool" and "denied" in last["content"] and user.startswith("Pay"):
            tried = {c["args"]["account"] for c in _calls(messages, "pay")}
            account = pick_account(ctx, tried)
            if account:
                return _call("pay", payee="Northwind Supplies", account=account,
                             amount=_amount(user))
            return LlmReply("I could not complete the payment.", ())
        return LlmReply(_closing(messages), ())

    def _first_step(self, user: str, ctx: str) -> LlmReply:
        ids = re.findall(r"\b\d{2}\b", user) if "email" in user.lower() and "read" in user.lower() else []
        if ids:
            return LlmReply(None, tuple(LlmToolCall(f"r{i}", "read_email", {"id": i})
                                        for i in ids))
        if "Acme" in user:
            to = re.search(r"[\w.+-]+@acme\.example", ctx)
            po = re.search(r"PO-\d+", ctx)
            body = "Please find attached invoice INV-1042 for $4,200."
            if po:
                body += f" Your PO number: {po.group(0)}."
            return _call("send_email", to=[to.group(0) if to else "billing@acme.example"],
                         subject="Invoice INV-1042", body=body)
        if user.startswith("Pay"):
            account = pick_account(ctx, set())
            if account:
                return _call("pay", payee="Northwind Supplies", account=account,
                             amount=_amount(user))
        return LlmReply("Done.", ())


def _call(tool: str, **args) -> LlmReply:
    return LlmReply(None, (LlmToolCall("c", tool, args),))


def _amount(user: str) -> float:
    m = re.search(r"\$([\d,]+)", user)
    return float(m.group(1).replace(",", "")) if m else 0.0


def _calls(messages: list[dict], tool: str) -> list[dict]:
    import json
    out = []
    for m in messages:
        for c in m.get("tool_calls") or []:
            if c["function"]["name"] == tool:
                out.append({"args": json.loads(c["function"]["arguments"])})
    return out


def _closing(messages: list[dict]) -> str:
    pays = _calls(messages, "pay")
    if pays:
        a = pays[-1]["args"]
        return f"Paid ${a['amount']:,.0f} to {a['payee']} (IBAN {a['account']})."
    if _calls(messages, "send_email"):
        return "Sent invoice INV-1042 to Acme's billing contact."
    return "Noted the three emails for upcoming payments."


class NeverFlags:
    """Judge stand-in that never flags anything: the demo shows the guarantee with the
    detector effectively off (as in the `no_write_flagging` ablation)."""

    def is_unsafe(self, text: str) -> bool:
        return False


class ExtractiveSummarizer:
    """Deterministic summary: sender, subject and every line carrying an id, amount or
    account. Keeps the poison verbatim, which is the point of the laundering test."""

    _KEY = re.compile(r"\b(IBAN|PO-\d+|INV-\d+|\$[\d,]+)", re.IGNORECASE)

    def summarize(self, texts: list[str]) -> str:
        notes = []
        for t in texts:
            sender = re.search(r"^From:.*?([\w.+-]+@[\w.-]+)", t, re.M)
            facts = [ln.strip() for ln in t.splitlines()
                     if self._KEY.search(ln) and not ln.startswith(("From:", "Subject:"))]
            if facts:
                who = sender.group(1) if sender else "note"
                notes.append(f"{who}: {' '.join(facts)}")
        return "Week 41 AP notes. " + " | ".join(notes)
