import os

def load_prompt(filename: str) -> str:
    """Load prompt content from a file in the skills directory."""
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()
