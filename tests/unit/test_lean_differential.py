"""Differential test (Tier 2): the Python gateway and the Lean model agree.

Hypothesis generates batches of random cases; Lean evaluates its own definitions
(`summarize`, `render` in lean/Smriti) on each batch with `#eval`, and the results must match
what the Python code computes (`labels.join`/`top`, `read_gate.render`). This ties the
machine-checked proofs to the code that actually runs. Skips if Lean is not installed.
"""
import re
import shutil
import subprocess
import time
import uuid
from functools import reduce
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from smriti.gateway.labels import Label, Memory, Sensitivity, Trust, join, top
from smriti.gateway.read_gate import render

LEAN_DIR = Path(__file__).resolve().parents[2] / "lean"
LAKE = shutil.which("lake") or str(Path.home() / ".elan/bin/lake")
pytestmark = pytest.mark.skipif(not Path(LAKE).exists(), reason="Lean (lake) not installed")

LEAN_TRUST = {Trust.LOW: ".low", Trust.MED: ".med", Trust.HIGH: ".high"}
PARENT_IDS = ["p1", "p2", "p3", "p4"]

trusts = st.sampled_from(list(Trust))
labels = st.tuples(trusts, st.lists(st.sampled_from(PARENT_IDS), max_size=3, unique=True))
summary_cases = st.lists(labels, min_size=0, max_size=5)
render_cases = st.lists(trusts, min_size=0, max_size=6)


def lean_label(trust: Trust, parents: list[str]) -> str:
    ps = ", ".join(f'"{p}"' for p in parents)
    return f'⟨{LEAN_TRUST[trust]}, "t", [{ps}]⟩'


def lean_summary_expr(case) -> str:
    labs = ", ".join(lean_label(t, ps) for t, ps in case)
    s = f'(Smriti.summarize "t" [{labs}])'
    return f's!"S;{{{s}.trust.toNat}};{{String.intercalate "," {s}.parents}}"'


def lean_render_expr(case) -> str:
    mems = ", ".join(f'⟨"m{i}", {lean_label(t, [])}⟩' for i, t in enumerate(case))
    r = f"(Smriti.render [{mems}])"
    return (f's!"R;{{String.intercalate "," ({r}.instr.map (·.id))}};'
            f'{{String.intercalate "," ({r}.data.map (·.id))}}"')


def run_lean(exprs: list[str]) -> list[str]:
    body = ",\n  ".join(exprs)
    src = f"import Smriti\n#eval IO.println (String.intercalate \"\\n\" [\n  {body}])\n"
    path = LEAN_DIR / f".diff_{uuid.uuid4().hex[:8]}.lean"
    path.write_text(src)
    try:
        out = subprocess.run([LAKE, "env", "lean", str(path)], cwd=LEAN_DIR,
                             capture_output=True, text=True, timeout=120)
    finally:
        path.unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr or out.stdout
    return [line for line in out.stdout.splitlines() if line[:2] in ("S;", "R;")]


@pytest.fixture(scope="module", autouse=True)
def built():
    subprocess.run([LAKE, "build"], cwd=LEAN_DIR, check=True, capture_output=True)


def py_summary(case) -> tuple[int, set[str]]:
    lab = reduce(join, (Label("tool:web", "t", t, Sensitivity.PUBLIC, frozenset(ps))
                        for t, ps in case), top("t"))
    return int(lab.trust), set(lab.parents)


def py_render(case) -> tuple[list[str], list[str]]:
    mems = [Memory(f"m{i}", f"m{i}", Label("tool:web", "t", t, Sensitivity.PUBLIC, frozenset()),
                   frozenset(), "s", time.time()) for i, t in enumerate(case)]
    ctx = render(mems, nonce="0" * 16)
    instr = [line[2:] for line in ctx.instructions.splitlines()]
    data = re.findall(r'trust="\w+">\n(m\d+)\n</memory_data', ctx.data_block)
    return instr, data


SETTINGS = settings(max_examples=6, deadline=None,
                    suppress_health_check=[HealthCheck.function_scoped_fixture])


@SETTINGS
@given(st.lists(summary_cases, min_size=40, max_size=40))
def test_summary_matches_lean(cases):
    lines = run_lean([lean_summary_expr(c) for c in cases])
    assert len(lines) == len(cases)
    for case, line in zip(cases, lines):
        _, trust, parents = line.split(";")
        assert (int(trust), set(filter(None, parents.split(",")))) == py_summary(case), case


@SETTINGS
@given(st.lists(render_cases, min_size=40, max_size=40))
def test_render_matches_lean(cases):
    lines = run_lean([lean_render_expr(c) for c in cases])
    assert len(lines) == len(cases)
    for case, line in zip(cases, lines):
        _, instr, data = line.split(";")
        lean = ([x for x in instr.split(",") if x], [x for x in data.split(",") if x])
        assert lean == py_render(case), case


def test_differential_harness_catches_a_mismatch():
    # Sanity: a deliberately wrong expectation is detected (the harness compares for real).
    (line,) = run_lean([lean_summary_expr([(Trust.LOW, ["p1"]), (Trust.HIGH, [])])])
    assert line == "S;0;p1"
    assert py_summary([(Trust.LOW, ["p1"]), (Trust.HIGH, [])]) != (2, {"p1"})
