import os
from pathlib import Path


def get_data_dir() -> Path:
    """Resolve the data directory for storage. Priority:
    1. KNOWLEDGE_DATA_DIR env var
    2. data/ relative to this package's parent (the repo root)
    """
    env = os.environ.get("KNOWLEDGE_DATA_DIR")
    if env:
        return Path(env)
    # Default: <repo>/data/
    return Path(__file__).parent.parent / "data"


MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIM = 768
MAX_SEQ_LENGTH = 2048
MEMORIES_TABLE = "memories"
