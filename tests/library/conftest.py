import sys
from pathlib import Path

# Reuse the original controlled evaluation site and independently written answers.
sys.path.insert(0, str(Path(__file__).parents[1] / "evaluation"))
