import os
from typing import Union, List

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_PATH = os.environ["QWEN_EMBED_MODEL_PATH"]
BATCH_SIZE = int(os.environ.get("QWEN_EMBED_BATCH", "8"))

device = "cuda" if torch.cuda.is_available() else "cpu"
model = SentenceTransformer(MODEL_PATH, device=device)

app = FastAPI()

class EmbeddingRequest(BaseModel):
    model: str | None = None
    input: Union[str, List[str]]

@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest):
    texts = [req.input] if isinstance(req.input, str) else req.input
    vectors = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    return {
        "object": "list",
        "model": req.model or "qwen3-embedding-0.6b",
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": vector.tolist(),
            }
            for i, vector in enumerate(vectors)
        ],
    }

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8081)
