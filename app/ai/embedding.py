from typing import Literal

from sentence_transformers import SentenceTransformer
import torch

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

model = SentenceTransformer(
    "intfloat/multilingual-e5-small",
    device=DEVICE,
)

def embedding(text: str, mode: Literal["query", "passage"] = "query") -> list[float]:
    return model.encode(
        f"{mode}: {text}",
        normalize_embeddings=True,
    ).tolist()

def embed_document(text: str) -> list[float]:
    """문서 저장용 임베딩."""
    return model.encode(
        f"passage: {text}",
        normalize_embeddings=True,
    ).tolist()

def embed_query(text: str) -> list[float]:
    """검색 질의 임베딩."""
    return model.encode(
        f"query: {text}",
        normalize_embeddings=True,
    ).tolist()