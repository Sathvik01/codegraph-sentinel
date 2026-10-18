"""Constants, thresholds, and connection settings for CodeGraph Sentinel.

Ruling (2026-10-18): plan pinned falkordblite + ./data/sentinel_graph.rdb;
the user replaced it with the official `falkordb` client against a
Dockerized FalkorDB server. DB_PATH therefore becomes FALKOR_HOST/FALKOR_PORT;
everything else (graph name, thresholds, paths) is pinned verbatim.
"""

import os

from dotenv import load_dotenv

# .env at the repo root supplies NVIDIA_API_KEY / SENTINEL_MODEL (never committed)
load_dotenv()

# --- graph connection (Dockerized FalkorDB server) ---
FALKOR_HOST: str = os.environ.get("FALKOR_HOST", "127.0.0.1")
FALKOR_PORT: int = int(os.environ.get("FALKOR_PORT", "6379"))
GRAPH_NAME: str = "codesentinel"

# --- conversation checkpointer (cross-session message memory) ---
CHECKPOINT_PATH: str = "./data/checkpoints.sqlite"

# --- ingestion sources ---
SRC_PATH: str = "./mock_repo"

# --- decision policy ---
# Calibration (plan Review Focus #3): this FalkorDB server's pageRank
# normalization sums scores to 1.0 across all Function nodes, so the hub
# lands at ~0.248 (runner-up ~0.090, leaf ~0.063 — after ingest).
# The 0.85 pin assumed a [0,1]-by-max normalization falkordblite shipped;
# threshold is the sanctioned knob, the gate logic is not.
PAGERANK_BLOCK_THRESHOLD: float = 0.20
MAX_HOPS: int = 5

# --- model (NVIDIA API endpoints) ---
# Deviation from plan pin: provider swapped openai -> NVIDIA (user decision,
# .env carries NVIDIA_API_KEY + SENTINEL_MODEL).
MODEL: str = os.environ.get("SENTINEL_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
NVIDIA_API_KEY: str | None = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL: str = os.environ.get(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
)
# Governance decisions should be reproducible: temperature 0 by default;
# the NVIDIA docs example ships temperature=1 (example values, not for us).
LLM_TEMPERATURE: float = float(os.environ.get("SENTINEL_TEMPERATURE", "0"))


def build_model():
    """Construct the chat model for create_agent (called only when the agent
    node actually runs — gate-abort paths never need a provider or key)."""
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    if not NVIDIA_API_KEY:
        raise RuntimeError(
            "NVIDIA_API_KEY is not set — copy .env.example to .env and fill it in"
        )
    return ChatNVIDIA(
        base_url=NVIDIA_BASE_URL,
        model=MODEL,
        api_key=NVIDIA_API_KEY,
        temperature=LLM_TEMPERATURE,
    )