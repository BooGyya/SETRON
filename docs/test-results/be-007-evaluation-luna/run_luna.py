"""고정된 sol 기준선을 보존하며 현재 프로세스에서만 luna 모델을 선택한다."""

import argparse
import json
import os
from pathlib import Path
import sys

import benchmark as benchmark


def verify_shared_inputs():
    here = Path(__file__).resolve().parent
    setup = json.loads((here / "comparison-setup.json").read_text(encoding="utf-8"))
    baseline = (here / setup["baseline_bundle"]).resolve()
    for name, expected in setup["baseline_file_sha256"].items():
        if benchmark.digest(baseline / name) != expected:
            raise SystemExit("Baseline evidence changed: " + name)
    for name, expected in setup["shared_file_sha256"].items():
        if benchmark.digest(here / name) != expected:
            raise SystemExit("Shared evaluation input changed: " + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", required=True, choices=("Memory", "Foundry"))
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    verify_shared_inputs()
    # uv가 읽은 .env 파일은 수정하지 않고 이 Python 프로세스의 모델만 선택한다.
    os.environ["SETRON_AI_MODEL"] = "gpt-6-luna"
    benchmark.run_live(args.sheet, args.timeout)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
