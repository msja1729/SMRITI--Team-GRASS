"""NeMo Agent Toolkit plugin (Tier 2): SMRITI episodes as a NAT workflow, profiled.

`nat eval` feeds episode keys ("finance/indirect/A2/0") from a dataset; each runs one
benchmark episode on the real models. Gateway operations, agent LLM calls and NVIDIA model
calls are wrapped with NAT's `track_function`, so the NAT profiler reports them as separate
steps and SMRITI's own overhead can be read apart from model time.
"""
import asyncio
import json

from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig

GATEWAY_OPS = ("write", "read", "check_action", "consolidate")


class SmritiEpisodeConfig(FunctionBaseConfig, name="smriti_episode"):
    bench_config: str                      # path to a bench YAML (models, settings)
    store: str = "postgres"                # "postgres" | "fake"


def instrument(parts) -> None:
    """Wrap the gateway class and the shared model clients with NAT step tracking."""
    from nat.plugins.profiler.decorators.function_tracking import track_unregistered_function

    from smriti.gateway.gateway import Gateway

    for op in GATEWAY_OPS:
        fn = getattr(Gateway, op)
        if not getattr(fn, "_smriti_tracked", False):
            wrapped = track_unregistered_function(fn, name=f"gateway.{op}",
                                                  metadata={"layer": "smriti"})
            wrapped._smriti_tracked = True
            setattr(Gateway, op, wrapped)
    for obj, method, name, layer in ((parts.llm, "chat", "agent_llm.chat", "model"),
                                     (parts.embedder, "embed", "nvidia.embed", "model"),
                                     (parts.judge, "is_unsafe", "nvidia.judge", "model")):
        setattr(obj, method, track_unregistered_function(getattr(obj, method), name=name,
                                                         metadata={"layer": layer}))


def parse_key(key: str):
    from smriti.bench.episodes import EpisodeSpec
    persona, attack, arm, seed = key.strip().split("/")
    return EpisodeSpec(persona, attack, arm, int(seed))


@register_function(config_type=SmritiEpisodeConfig)
async def smriti_episode(config: SmritiEpisodeConfig, builder: Builder):
    import os

    from smriti.bench.episodes import run_episode
    from smriti.bench.run import build_components, load_config, load_dotenv, pg_store_factory
    from smriti.store.fake_store import FakeStore

    load_dotenv()
    cfg = load_config(config.bench_config)
    parts = build_components(cfg)
    instrument(parts)
    if config.store == "fake":
        make_store = FakeStore
    else:
        dim = len(parts.embedder.embed(["dimension probe"], "query")[0])
        dsn = os.environ.get("SMRITI_PG_DSN",
                             "host=localhost port=5432 user=postgres dbname=postgres")
        make_store = pg_store_factory(dsn, "nat", dim)

    def run_one(key: str) -> str:
        store = make_store()
        try:
            r = run_episode(parse_key(key), cfg.settings, parts, store)
        finally:
            if hasattr(store, "drop_schema"):
                store.drop_schema()
                store.close()
        return json.dumps({"key": r.key, "attack_succeeded": r.attack_succeeded,
                           "task_succeeded": r.task_succeeded, "error": r.error})

    async def _episode(episode_key: str) -> str:
        # to_thread copies the context, so NAT's step tracking follows into the episode.
        return await asyncio.to_thread(run_one, episode_key)

    yield FunctionInfo.from_fn(_episode, description="Run one SMRITI benchmark episode.")
