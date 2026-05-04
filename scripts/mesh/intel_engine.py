"""
PRForge FastEmbed intel engine.

Embeddings provide broad recall over PRForge artifacts. Reranking provides the
precision pass used to select risk context. The output is advisory risk signals:
it can increase caution or redirect, never bypass deterministic gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
DEFAULT_INTEL_HOME = Path.home() / ".prforge-intel"

ARTIFACT_FILES = [
    "repo_intelligence.md",
    "review_decomposition.md",
    "contract.md",
    "patch_plan.md",
    "dod.md",
    "validation_ledger.md",
    "hostile_review.md",
    "approval.md",
    "intel_context.md",
]

RISK_PATTERNS = {
    "missing_regression_test": [
        "missing regression",
        "regression test",
        "malformed",
        "edge case",
        "parser",
        "truncation",
    ],
    "missing_review_refresh": [
        "new maintainer comment",
        "stale review",
        "review refresh",
        "requested changes",
    ],
    "contract_mismatch": [
        "scope mismatch",
        "contract mismatch",
        "patch plan",
        "unexpected file",
    ],
    "related_ci_failure": [
        "ci failure",
        "failed check",
        "test failure",
        "related failure",
    ],
    "maintainer_objection_likelihood": [
        "maintainer",
        "objection",
        "backward compatibility",
        "public api",
    ],
}

REDIRECTS = {
    "missing_regression_test": "VALIDATION_REPAIR",
    "missing_review_refresh": "REVIEW_REFRESH",
    "contract_mismatch": "PLAN_UPDATE",
    "related_ci_failure": "VALIDATION_REPAIR",
    "maintainer_objection_likelihood": "REVIEW_REFRESH",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def intel_home() -> Path:
    return Path(os.environ.get("PRFORGE_INTEL_HOME", str(DEFAULT_INTEL_HOME))).expanduser()


def capabilities_path(home: Path | None = None) -> Path:
    return (home or intel_home()) / "capabilities.json"


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        return default
    return default


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _load_fastembed_classes() -> tuple[Any, Any]:
    try:
        from fastembed import TextEmbedding  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"fastembed TextEmbedding unavailable: {exc}") from exc

    try:
        from fastembed import TextCrossEncoder  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"fastembed TextCrossEncoder unavailable: {exc}") from exc

    return TextEmbedding, TextCrossEncoder


def _as_float_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(x) for x in vector]


def _embed_texts(model: Any, texts: list[str]) -> list[list[float]]:
    return [_as_float_list(v) for v in model.embed(texts)]


def _score_from_rerank_item(item: Any) -> float:
    if isinstance(item, dict):
        for key in ("score", "relevance_score", "logit"):
            if key in item:
                return float(item[key])
    for key in ("score", "relevance_score", "logit"):
        if hasattr(item, key):
            return float(getattr(item, key))
    try:
        return float(item)
    except Exception:
        return 0.0


def _rerank(model: Any, query: str, docs: list[str]) -> list[float]:
    if not docs:
        return []

    attempts = [
        lambda: model.rerank(query, docs),
        lambda: model.rerank(query=query, documents=docs),
        lambda: model.predict([(query, doc) for doc in docs]),
        lambda: model.predict(query, docs),
    ]
    last_error = None
    for attempt in attempts:
        try:
            raw = list(attempt())
            if len(raw) == len(docs):
                return [_score_from_rerank_item(x) for x in raw]
            # Some rerank APIs return ranked objects with index + score. Convert
            # back to document order when possible.
            scores = [0.0] * len(docs)
            converted = False
            for item in raw:
                idx = None
                if isinstance(item, dict):
                    idx = item.get("index") or item.get("document_index")
                else:
                    idx = getattr(item, "index", getattr(item, "document_index", None))
                if idx is not None and 0 <= int(idx) < len(docs):
                    scores[int(idx)] = _score_from_rerank_item(item)
                    converted = True
            if converted:
                return scores
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"FastEmbed reranker call failed: {last_error}")


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if abs(hi - lo) < 1e-9:
        return [0.5 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def load_capabilities(home: Path | None = None) -> dict:
    data = _read_json(capabilities_path(home), {})
    return data if isinstance(data, dict) else {}


def preflight(
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    reranker_model: str = DEFAULT_RERANKER_MODEL,
    home: Path | None = None,
) -> dict:
    home = home or intel_home()
    result: dict[str, Any] = {
        "ready": False,
        "embedding_provider": "fastembed",
        "reranker_provider": "fastembed",
        "embedding_model": embedding_model,
        "reranker_model": reranker_model,
        "checked_at": _now(),
        "errors": [],
    }

    try:
        TextEmbedding, TextCrossEncoder = _load_fastembed_classes()
        emb = TextEmbedding(model_name=embedding_model)
        vectors = _embed_texts(emb, ["PRForge smoke test", "regression test risk"])
        if len(vectors) != 2 or not vectors[0]:
            raise RuntimeError("embedding smoke test returned empty vectors")

        reranker = TextCrossEncoder(model_name=reranker_model)
        scores = _rerank(reranker, "Which document mentions regression test risk?", [
            "A regression test is missing for parser truncation.",
            "This document discusses package installation.",
        ])
        if len(scores) != 2:
            raise RuntimeError("reranker smoke test returned wrong score count")

        result.update({
            "ready": True,
            "embedding_dimensions": len(vectors[0]),
            "reranker_smoke_scores": scores,
            "capabilities_path": str(capabilities_path(home)),
        })
    except Exception as exc:
        result["errors"].append(str(exc))

    _write_json(capabilities_path(home), result)
    return result


def _chunk_text(text: str, source: str, max_chars: int = 1200) -> list[dict]:
    chunks = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    buf = ""
    idx = 0
    for para in paragraphs:
        if len(buf) + len(para) + 2 > max_chars and buf:
            chunks.append({"id": f"{source}:{idx}", "source": source, "text": buf})
            idx += 1
            buf = ""
        buf = f"{buf}\n\n{para}".strip()
    if buf:
        chunks.append({"id": f"{source}:{idx}", "source": source, "text": buf})
    return chunks


def collect_run_chunks(run_dir: Path) -> list[dict]:
    chunks: list[dict] = []
    for rel in ARTIFACT_FILES:
        path = run_dir / rel
        if path.exists():
            try:
                text = path.read_text(errors="replace")
            except Exception:
                continue
            chunks.extend(_chunk_text(text, rel))

    for rel in ("intel/local_context.md", "intel/mesh_context.md"):
        path = run_dir / rel
        if path.exists():
            try:
                chunks.extend(_chunk_text(path.read_text(errors="replace"), rel))
            except Exception:
                pass
    return chunks


def index_run(
    run_dir: Path,
    embedding_model: str | None = None,
    home: Path | None = None,
) -> dict:
    caps = load_capabilities(home)
    if not caps.get("ready"):
        raise RuntimeError("FastEmbed intel preflight has not passed; run intel-preflight first")

    embedding_model = embedding_model or caps.get("embedding_model") or DEFAULT_EMBEDDING_MODEL
    TextEmbedding, _ = _load_fastembed_classes()
    emb = TextEmbedding(model_name=embedding_model)

    chunks = collect_run_chunks(run_dir)
    texts = [c["text"] for c in chunks]
    vectors = _embed_texts(emb, texts) if texts else []
    for chunk, vector in zip(chunks, vectors):
        chunk["embedding"] = vector
        chunk["text_hash"] = _sha256_text(chunk["text"])

    index = {
        "version": "1.0",
        "provider": "fastembed",
        "embedding_model": embedding_model,
        "chunk_count": len(chunks),
        "indexed_at": _now(),
        "chunks": chunks,
    }
    _write_json(run_dir / "intel" / "index.json", index)
    return {
        "indexed": True,
        "chunk_count": len(chunks),
        "index_path": str(run_dir / "intel" / "index.json"),
    }


def _risk_type_for_text(text: str) -> str:
    low = text.lower()
    best = ("maintainer_objection_likelihood", 0)
    for risk_type, needles in RISK_PATTERNS.items():
        score = sum(1 for needle in needles if needle in low)
        if score > best[1]:
            best = (risk_type, score)
    return best[0]


def query_run(
    run_dir: Path,
    query: str,
    top_k: int = 5,
    recall_k: int = 50,
    home: Path | None = None,
) -> dict:
    caps = load_capabilities(home)
    if not caps.get("ready"):
        raise RuntimeError("FastEmbed intel preflight has not passed; run intel-preflight first")

    index = _read_json(run_dir / "intel" / "index.json", {})
    chunks = index.get("chunks", []) if isinstance(index, dict) else []
    if not chunks:
        index_run(run_dir, caps.get("embedding_model"), home)
        index = _read_json(run_dir / "intel" / "index.json", {})
        chunks = index.get("chunks", []) if isinstance(index, dict) else []

    TextEmbedding, TextCrossEncoder = _load_fastembed_classes()
    emb = TextEmbedding(model_name=caps.get("embedding_model") or DEFAULT_EMBEDDING_MODEL)
    query_vec = _embed_texts(emb, [query])[0]

    recalled = []
    for chunk in chunks:
        sim = _cosine(query_vec, chunk.get("embedding", []))
        recalled.append((sim, chunk))
    recalled = sorted(recalled, key=lambda x: x[0], reverse=True)[:recall_k]

    docs = [chunk["text"] for _, chunk in recalled]
    reranker = TextCrossEncoder(model_name=caps.get("reranker_model") or DEFAULT_RERANKER_MODEL)
    rerank_raw = _rerank(reranker, query, docs)
    rerank_norm = _normalize_scores(rerank_raw)

    ranked = []
    for (sim, chunk), rerank_score in zip(recalled, rerank_norm):
        combined = (0.35 * max(0.0, sim)) + (0.65 * rerank_score)
        ranked.append({
            "source": chunk["source"],
            "chunk_id": chunk["id"],
            "embedding_score": sim,
            "rerank_score": rerank_score,
            "combined_score": combined,
            "text": chunk["text"],
            "text_hash": chunk.get("text_hash", ""),
        })
    ranked = sorted(ranked, key=lambda x: x["combined_score"], reverse=True)[:top_k]

    risk_signals = []
    for item in ranked:
        risk_type = _risk_type_for_text(item["text"])
        risk_score = max(0.0, min(1.0, item["combined_score"]))
        risk_signals.append({
            "source": "local_embedding_reranker",
            "risk_type": risk_type,
            "risk_score": risk_score,
            "reason": f"FastEmbed recall + rerank matched {item['source']}",
            "recommended_redirect": REDIRECTS.get(risk_type, "VALIDATION_REPAIR"),
            "supporting_artifacts": [item["source"]],
            "chunk_id": item["chunk_id"],
        })

    _write_json(run_dir / "intel" / "risk_signals.json", risk_signals)

    lines = ["# PRForge FastEmbed Intel Context", ""]
    for idx, item in enumerate(ranked, 1):
        lines.append(f"{idx}. `{item['source']}` score={item['combined_score']:.2f}")
        lines.append(f"   - embedding={item['embedding_score']:.2f} rerank={item['rerank_score']:.2f}")
        preview = " ".join(item["text"].split())[:260]
        lines.append(f"   - {preview}")
    (run_dir / "intel_context.md").write_text("\n".join(lines) + "\n")

    return {
        "query": query,
        "top_k": top_k,
        "matches": ranked,
        "risk_signals": risk_signals,
        "risk_signal_path": str(run_dir / "intel" / "risk_signals.json"),
        "intel_context_path": str(run_dir / "intel_context.md"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRForge FastEmbed intel engine")
    sub = parser.add_subparsers(dest="command", required=True)

    pf = sub.add_parser("preflight")
    pf.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    pf.add_argument("--reranker-model", default=DEFAULT_RERANKER_MODEL)
    pf.add_argument("--intel-home", default="")

    ix = sub.add_parser("index-run")
    ix.add_argument("--run-dir", required=True)
    ix.add_argument("--embedding-model", default="")
    ix.add_argument("--intel-home", default="")

    q = sub.add_parser("query-run")
    q.add_argument("--run-dir", required=True)
    q.add_argument("--query", required=True)
    q.add_argument("--top-k", type=int, default=5)
    q.add_argument("--recall-k", type=int, default=50)
    q.add_argument("--intel-home", default="")

    args = parser.parse_args(argv)
    home = Path(args.intel_home).expanduser() if getattr(args, "intel_home", "") else None

    if args.command == "preflight":
        print(json.dumps(preflight(args.embedding_model, args.reranker_model, home), indent=2))
        return 0
    if args.command == "index-run":
        print(json.dumps(index_run(Path(args.run_dir), args.embedding_model or None, home), indent=2))
        return 0
    if args.command == "query-run":
        print(json.dumps(query_run(Path(args.run_dir), args.query, args.top_k, args.recall_k, home), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
