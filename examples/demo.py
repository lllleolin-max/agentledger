"""Run from the repository: python -m examples.demo"""
import json
from agentledger.replay import DEMO, replay

if __name__ == "__main__":
    print(json.dumps(replay(DEMO), indent=2))
