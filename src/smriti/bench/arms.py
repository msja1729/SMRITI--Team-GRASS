"""Arms and ablations (SPEC §10) as gateway configs."""
from dataclasses import replace

from smriti.gateway.gateway import A0, A2, GatewayConfig

# A1: plain store + "ignore instructions in memory" + the judge on every input.
A1 = replace(A0, use_judge=True, judge_all_inputs=True, prompt_guard=True)

# A1G (Tier 2): the same baseline with NVIDIA NeMo Guardrails input rails as the judge.
A1G = replace(A1, judge_backend="guardrails")

# A2J (Tier 2): SMRITI with NemoGuard JailbreakDetect as write-gate stage 1.
A2J = replace(A2, jailbreak_detect=True)

# A2S (Tier 2): SMRITI that asks for confirmation only when a risky call's destination is
# not sanctioned by the user (request or HIGH memory), instead of on every tainted call,
# and tells the model which destination was refused so it can finish the task.
A2S = replace(A2, confirm_unsanctioned_only=True, explain_denials=True)

# A3 ablations: A2 minus one layer. no_write_flagging is the "detector off" run (§1).
ABLATIONS: dict[str, GatewayConfig] = {
    "no_write_flagging": replace(A2, write_flagging=False, use_judge=False),
    "no_read_escaping": replace(A2, read_escaping=False),
    "no_action_check": replace(A2, action_check=False),
    "no_provenance": replace(A2, label_provenance=False),
}

ARMS: dict[str, GatewayConfig] = {"A0": A0, "A1": A1, "A1G": A1G, "A2": A2, "A2J": A2J,
                                  "A2S": A2S,
                                  **ABLATIONS}


def arm_config(name: str, quota_low_writes_per_session: int) -> GatewayConfig:
    config = ARMS[name]
    if name in ("A0", "A1", "A1G"):    # plain store: no quota
        return config
    return replace(config, quota_low_writes_per_session=quota_low_writes_per_session)
