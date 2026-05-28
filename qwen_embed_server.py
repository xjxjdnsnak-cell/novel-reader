import os
import math
import traceback
from typing import List, Optional, Union

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


MODEL_PATH = os.environ.get("QWEN_EMBED_MODEL_PATH", "")


def infer_model_name(model_path: str) -> str:
    leaf = os.path.basename(os.path.normpath(model_path)).lower()
    if "4b" in leaf:
        return "qwen3-embedding-4b"
    if "0.6b" in leaf:
        return "qwen3-embedding-0.6b"
    return "qwen3-embedding-0.6b"


MODEL_NAME = os.environ.get("QWEN_EMBED_MODEL_NAME") or infer_model_name(MODEL_PATH)
BACKEND = os.environ.get("QWEN_EMBED_BACKEND", "auto").lower()
BATCH_SIZE = int(os.environ.get("QWEN_EMBED_BATCH", "8"))
HOST = os.environ.get("QWEN_EMBED_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_EMBED_PORT", "8081"))
DEVICE_MODE = os.environ.get("QWEN_EMBED_DEVICE", "auto").lower()
CUDA_MEMORY_FRACTION = float(os.environ.get("QWEN_EMBED_CUDA_MEMORY_FRACTION", "0") or "0")
MAX_SEQ_LENGTH = int(os.environ.get("QWEN_EMBED_MAX_SEQ_LENGTH", "0") or "0")
N_GPU_LAYERS = int(os.environ.get("QWEN_EMBED_N_GPU_LAYERS", "99"))
N_CTX = int(os.environ.get("QWEN_EMBED_N_CTX", "2048"))
N_BATCH = int(os.environ.get("QWEN_EMBED_N_BATCH", "512"))

app = FastAPI()
model: Optional[Union[SentenceTransformer, object]] = None
device = "unknown"
load_error: Optional[str] = None
probe_done = False
probe_error: Optional[str] = None


def resolve_backend() -> str:
    if BACKEND in {"sentence-transformers", "llama-cpp"}:
        return BACKEND
    if MODEL_PATH.lower().endswith(".gguf"):
        return "llama-cpp"
    return "sentence-transformers"


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
        backend = resolve_backend()
        device = "llama-cpp" if backend == "llama-cpp" else choose_device()
        print("Qwen embedding server")
        print(f"  backend: {backend}")
        print(f"  model path: {MODEL_PATH}")
        print(f"  model name: {MODEL_NAME}")
        print(f"  device: {device}")
        print(f"  batch size: {BATCH_SIZE}")
        if CUDA_MEMORY_FRACTION > 0:
            print(f"  cuda memory fraction: {CUDA_MEMORY_FRACTION}")
        if MAX_SEQ_LENGTH > 0:
            print(f"  max sequence length: {MAX_SEQ_LENGTH}")
        print(f"  listen: http://{HOST}:{PORT}")
        if backend == "llama-cpp":
            print(f"  n_gpu_layers: {N_GPU_LAYERS}")
            print(f"  n_ctx: {N_CTX}")
            print(f"  n_batch: {N_BATCH}")
            try:
                from llama_cpp import Llama
            except ImportError as exc:
                raise RuntimeError(
                    "llama-cpp-python is required for GGUF embedding. Install it with: pip install llama-cpp-python"
                ) from exc
            model = Llama(
                model_path=MODEL_PATH,
                embedding=True,
                n_gpu_layers=N_GPU_LAYERS,
                n_ctx=N_CTX,
                n_batch=N_BATCH,
                verbose=False,
            )
            load_error = None
            return

        if device == "cuda":
            print(f"  cuda device: {torch.cuda.get_device_name(0)}")
            print(f"  cuda capability: {torch.cuda.get_device_capability(0)}")
            if CUDA_MEMORY_FRACTION > 0:
                torch.cuda.set_per_process_memory_fraction(CUDA_MEMORY_FRACTION, 0)
        model = SentenceTransformer(MODEL_PATH, device=device)
        if MAX_SEQ_LENGTH > 0:
            model.max_seq_length = MAX_SEQ_LENGTH
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
        encode_texts(["health"])
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
        "backend": resolve_backend(),
        "model": MODEL_NAME,
        "model_path": MODEL_PATH,
        "device": device,
        "batch_size": BATCH_SIZE,
        "cuda_memory_fraction": CUDA_MEMORY_FRACTION or None,
        "max_seq_length": getattr(model, "max_seq_length", None) if model is not None else None,
        "n_gpu_layers": N_GPU_LAYERS if resolve_backend() == "llama-cpp" else None,
        "n_ctx": N_CTX if resolve_backend() == "llama-cpp" else None,
        "n_batch": N_BATCH if resolve_backend() == "llama-cpp" else None,
        "error": error,
    }


def normalize_vector(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


def llama_cpp_embeddings(texts: List[str]) -> List[List[float]]:
    if model is None:
        raise RuntimeError(load_error or "Model is not loaded.")
    vectors: List[List[float]] = []
    try:
        response = model.create_embedding(texts)
        data = response.get("data", [])
        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item.get("index", 0))]
    except Exception:
        vectors = []
        for text in texts:
            response = model.create_embedding(text)
            vectors.append(response["data"][0]["embedding"])
    return [normalize_vector(vector) for vector in vectors]


def encode_texts(texts: List[str]) -> List[List[float]]:
    if resolve_backend() == "llama-cpp":
        return llama_cpp_embeddings(texts)
    vectors = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest):
    if model is None:
        raise HTTPException(status_code=503, detail=load_error or "Model is not loaded.")

    texts = [req.input] if isinstance(req.input, str) else req.input
    try:
        vectors = encode_texts(texts)
    except Exception:
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    return {
        "object": "list",
        "model": req.model or MODEL_NAME,
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": vector,
            }
            for i, vector in enumerate(vectors)
        ],
    }


load_model()


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
