"""Lance la validation walk-forward et écrit data/validation_report.json."""

import json
import sys

from betclic_agent.engine.validate import run


if __name__ == "__main__":
    report = run()
    json.dump(report["summary"], sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
