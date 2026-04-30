import os
import traceback
from typing import List, Optional, Union

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


MODEL_PATH = os.environ.get("QWEN_EMBED_MODEL_PATH", "")
MODEL_NAME = os.environ.get("QWEN_EMBED_MODEL_NAME", "qwen3-embedding-0.6b")
BATCH_SIZE = int(os.environ.get("QWEN_EMBED_BATCH", "8"))
HOST = os.environ.get("QWEN_EMBED_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_EMBED_PORT", "8081"))
DEVICE_MODE = os.environ.get("QWEN_EMBED_DEVICE", "auto").lower()

app = FastAPI()
model: Optional[SentenceTransformer] = None
device = "unknown"
load_error: Optional[str] = None
probe_done = False
probe_error: Optional[str] = None


class EmbeddingRequest(BaseModel):
    model: Optional[str] = None
    input: Union[str, List[str]]


def choose_device() -> str:
    if DEVICE_MODE in {"cpu", "cuda"}:
        return DEVICE_MODE
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_model() -> None:
    global model, device, load_error
    if not MODEL_PATH:
        load_error = "QWEN_EMBED_MODEL_PATH is not set."
        return
    if not os.path.exists(MODEL_PATH):
        load_error = f"QWEN_EMBED_MODEL_PATH does not exist: {MODEL_PATH}"
        return

    try:
        device = choose_device()
        print("Qwen embedding server")
        print(f"  model path: {MODEL_PATH}")
        print(f"  model name: {MODEL_NAME}")
        print(f"  device: {device}")
        print(f"  batch size: {BATCH_SIZE}")
        print(f"  listen: http://{HOST}:{PORT}")
        if device == "cuda":
            print(f"  cuda device: {torch.cuda.get_device_name(0)}")
            print(f"  cuda capability: {torch.cuda.get_device_capability(0)}")
        model = SentenceTransformer(MODEL_PATH, device=device)
        load_error = None
    except Exception:
        load_error = traceback.format_exc()
        model = None


def ensure_probe() -> None:
    global probe_done, probe_error
    if probe_done:
        if probe_error:
            raise RuntimeError(probe_error)
        return
    if model is None:
        raise RuntimeError(load_error or "Model is not loaded.")
    try:
        model.encode(
            ["health"],
            batch_size=1,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        probe_error = None
    except Exception:
        probe_error = traceback.format_exc()
    finally:
        probe_done = True
    if probe_error:
        raise RuntimeError(probe_error)


@app.get("/health")
def health():
    try:
        ensure_probe()
        ok = True
        error = None
    except Exception as exc:
        ok = False
        error = str(exc)
    return {
        "ok": ok,
        "model_loaded": model is not None,
        "model": MODEL_NAME,
        "model_path": MODEL_PATH,
        "device": device,
        "batch_size": BATCH_SIZE,
        "error": error,
    }


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest):
    if model is None:
        raise HTTPException(status_code=503, detail=load_error or "Model is not loaded.")

    texts = [req.input] if isinstance(req.input, str) else req.input
    try:
        vectors = model.encode(
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except Exception:
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    return {
        "object": "list",
        "model": req.model or MODEL_NAME,
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": vector.tolist(),
            }
            for i, vector in enumerate(vectors)
        ],
    }


load_model()


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
