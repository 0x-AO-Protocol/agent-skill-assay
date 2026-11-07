"""Bundle tests of the synthetic bundle; the framework tests read them as source only.

They use what the test-module rule set allows: a child process for the hash-seed
run, a copy of the environment for it, a clock read and reads of bundle files.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .skill import WordTallySkill

BUNDLE = Path(__file__).resolve().parent
CHILD = "from skills.sample.word_tally.skill import WordTallySkill as S; print(S())"


def test_counts_words():
    with open(BUNDLE / "fixtures" / "example_params.json", "r") as handle:
        params = json.load(handle)
    start = time.perf_counter()
    assert WordTallySkill().execute(params)["total"] == 9
    assert time.perf_counter() - start < 5


def test_child_runs_under_two_hash_seeds():
    for seed in ("0", "4242"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(BUNDLE.parents[2]))
        subprocess.run([sys.executable, "-c", CHILD], env=env, check=True)
