"""NemoGuard JailbreakDetect NIM client (Tier 2: write-gate stage 1).

API as used by NeMo Guardrails' own client: POST {base_url}/classify with {"input": text};
the reply carries a boolean "jailbreak". No hosted endpoint exists, so the NIM runs
locally on the A100. Same interface as the judge (`is_unsafe`), so it reuses CachedJudge.
Any error or malformed reply raises, and the gateway fails closed.
"""
import os


class JailbreakDetect:
    def __init__(self, base_url: str, api_key_env: str | None = None, timeout_s: float = 30.0,
                 client=None):
        import httpx

        self.url = base_url.rstrip("/") + "/classify"
        key = os.environ.get(api_key_env) if api_key_env else None
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.client = client or httpx.Client(timeout=timeout_s)

    def is_unsafe(self, text: str) -> bool:
        r = self.client.post(self.url, json={"input": text}, headers=self.headers)
        r.raise_for_status()
        verdict = r.json()["jailbreak"]
        if not isinstance(verdict, bool):
            raise ValueError(f"unexpected jailbreak verdict {verdict!r}")
        return verdict
