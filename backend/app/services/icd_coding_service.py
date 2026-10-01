# backend/app/services/icd_coding_service.py
"""
ICD-10-CM (diagnosis) auto-coding service.
3-tier offline NLP pipeline:
  Tier 1: sentence-transformers semantic search via ChromaDB (persistent HNSW index)
  Tier 2: scispacy clinical NER -> entity-level semantic matching
  Tier 3: TF-IDF cosine similarity fallback

ChromaDB collection is auto-populated on first startup (~2-3 min) and then
persists to disk — subsequent starts are instant.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CHROMA_CM_DIR = str(_DATA_DIR / "chroma" / "icd_cm")
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
_COLLECTION_NAME = "icd10_cm_v2"


class ICDSuggestion(BaseModel):
    code: str
    description: str
    confidence: float
    source: str  # "semantic" | "entity" | "tfidf" | "exact"


class ICDCodingService:
    """Singleton. Call ICDCodingService() anywhere — same instance reused."""

    _instance: Optional["ICDCodingService"] = None
    _ready: bool = False

    def __new__(cls) -> "ICDCodingService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._ready:
            return
        self._ready = True
        self._initialize()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(_CHROMA_CM_DIR).mkdir(parents=True, exist_ok=True)

        logger.info("ICDCodingService: attaching shared embedding model …")
        from app.services.shared_embedder import get_embedder

        self._embedder = get_embedder()

        import chromadb

        self._chroma = chromadb.PersistentClient(path=_CHROMA_CM_DIR)
        self._col = self._chroma.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        import simple_icd_10_cm as cm

        self._cm = cm

        if self._col.count() == 0:
            self._populate(cm)
        else:
            logger.info(
                "ICDCodingService: ChromaDB collection ready (%d ICD-10-CM codes)",
                self._col.count(),
            )

        # Preload all leaf codes for TF-IDF
        self._codes: list[str] = []
        self._descs: list[str] = []
        for code in cm.get_all_codes(with_dots=True):
            if cm.is_leaf(code):
                self._codes.append(code)
                self._descs.append(cm.get_description(code))

        logger.info("ICDCodingService: building TF-IDF index (%d codes) …", len(self._codes))
        import joblib
        from sklearn.feature_extraction.text import TfidfVectorizer

        _cache_prefix = _DATA_DIR / "tfidf_cache" / f"icd_cm_{len(self._codes)}"
        _cache_prefix.parent.mkdir(parents=True, exist_ok=True)
        _word_path = str(_cache_prefix) + "_word.joblib"
        _char_path = str(_cache_prefix) + "_char.joblib"

        if Path(_word_path).exists() and Path(_char_path).exists():
            logger.info("ICDCodingService: loading TF-IDF indexes from cache …")
            self._tfidf, self._tfidf_matrix = joblib.load(_word_path)
            self._char_tfidf, self._char_tfidf_matrix = joblib.load(_char_path)
        else:
            # Word-level TF-IDF (word bigrams) — exact/near-exact term matching
            self._tfidf = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
            self._tfidf_matrix = self._tfidf.fit_transform(self._descs)
            # Character-level n-gram TF-IDF — enables partial word / typo matching
            self._char_tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), min_df=1, sublinear_tf=True)
            self._char_tfidf_matrix = self._char_tfidf.fit_transform(self._descs)
            joblib.dump((self._tfidf, self._tfidf_matrix), _word_path)
            joblib.dump((self._char_tfidf, self._char_tfidf_matrix), _char_path)
            logger.info("ICDCodingService: TF-IDF indexes saved to disk.")

        # Optional: scispacy NER
        self._nlp = None
        try:
            import spacy

            self._nlp = spacy.load("en_core_sci_md")
            logger.info("ICDCodingService: scispacy en_core_sci_md loaded")
        except Exception as exc:
            logger.warning("ICDCodingService: scispacy unavailable (%s) — entity tier skipped", exc)

        logger.info("ICDCodingService: ready")

    def _populate(self, cm) -> None:
        logger.info("ICDCodingService: first-run — populating ChromaDB from ICD-10-CM …")
        unique_data: dict[str, str] = {}
        for code in cm.get_all_codes(with_dots=True):
            if cm.is_leaf(code):
                unique_data[code] = cm.get_description(code)

        codes = list(unique_data.keys())
        descs = list(unique_data.values())
        total = len(codes)

        logger.info(
            "ICDCodingService: computing embeddings for %d codes (this may take a few minutes on first run) …", total
        )
        from app.services.shared_embedder import encode_with_progress

        embeddings = encode_with_progress(descs, batch_size=512, label="ICD-CM embeddings")

        logger.info("ICDCodingService: embeddings complete, upserting to ChromaDB …")

        batch_size = 2048
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_codes = codes[start:end]
            batch_descs = descs[start:end]
            batch_embs = embeddings[start:end].tolist()

            self._col.upsert(
                ids=batch_codes,
                documents=batch_descs,
                embeddings=batch_embs,
                metadatas=[{"code": c, "description": d} for c, d in zip(batch_codes, batch_descs)],
            )
            # More frequent logging (every 5 batches)
            if (start // batch_size) % 5 == 0 or end == total:
                logger.info("  … %d / %d codes indexed", end, total)

        logger.info("ICDCodingService: ChromaDB populated with %d codes", total)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def suggest(
        self,
        chief_complaint: Optional[str] = None,
        symptoms: Optional[list[str]] = None,
        diagnosis_text: Optional[str] = None,
        top_k: int = 5,
    ) -> list[ICDSuggestion]:
        """Return top-k ICD-10-CM suggestions for a clinical presentation."""
        parts = []
        if chief_complaint:
            parts.append(chief_complaint.strip())
        if symptoms:
            parts.extend(s.strip() for s in symptoms if s.strip())
        if diagnosis_text:
            parts.append(diagnosis_text.strip())

        if not parts:
            return []

        text = ". ".join(parts)
        results: dict[str, ICDSuggestion] = {}

        self._tier1_semantic(text, top_k * 3, results)
        if self._nlp is not None:
            self._tier2_entity(text, results)
        self._tier3_tfidf(text, top_k * 3, results)

        ranked = sorted(results.values(), key=lambda s: s.confidence, reverse=True)
        return ranked[:top_k]

    def search(self, query: str, top_k: int = 10) -> list[ICDSuggestion]:
        """
        Keyword-dominant hybrid search for the code browser.
        Scoring: 70% TF-IDF keyword + 30% semantic similarity.
        Exact code prefix and substring description matches get a priority boost.
        """
        import numpy as np

        query = query.strip()
        if not query:
            return []

        # ── 1. Exact / prefix code match (e.g. "J06", "R50") ──────────
        normalised = query.upper().replace(" ", "").replace(".", "")

        # Exact single-code lookup — return immediately if user typed a full code
        for code in self._codes:
            if code.upper().replace(".", "") == normalised:
                return [
                    ICDSuggestion(
                        code=code,
                        description=self._cm.get_description(code),
                        confidence=1.0,
                        source="exact",
                    )
                ]

        prefix_hits: list[ICDSuggestion] = []
        for code in self._codes:
            if code.upper().replace(".", "").startswith(normalised):
                prefix_hits.append(
                    ICDSuggestion(
                        code=code,
                        description=self._cm.get_description(code),
                        confidence=1.0,
                        source="exact",
                    )
                )
                if len(prefix_hits) >= top_k:
                    break
        if prefix_hits:
            return prefix_hits

        # ── 2. TF-IDF keyword scores (word bigrams, 40% weight) ────────
        kw_scores: dict[str, float] = {}
        char_scores: dict[str, float] = {}
        try:
            query_vec = self._tfidf.transform([query])
            raw = (self._tfidf_matrix @ query_vec.T).toarray().flatten()
            candidates = int(min(top_k * 10, len(self._codes)))
            top_idx = np.argpartition(raw, -candidates)[-candidates:]
            max_kw = float(raw[top_idx].max()) or 1.0
            for idx in top_idx:
                if raw[idx] > 0:
                    kw_scores[self._codes[idx]] = float(raw[idx]) / max_kw
        except Exception as exc:
            logger.warning("ICDCodingService.search TF-IDF: %s", exc)

        # ── 2b. Character n-gram scores (30% weight — partial word matching) ─
        try:
            char_vec = self._char_tfidf.transform([query])
            char_raw = (self._char_tfidf_matrix @ char_vec.T).toarray().flatten()
            candidates = int(min(top_k * 10, len(self._codes)))
            top_idx = np.argpartition(char_raw, -candidates)[-candidates:]
            max_char = float(char_raw[top_idx].max()) or 1.0
            for idx in top_idx:
                if char_raw[idx] > 0:
                    char_scores[self._codes[idx]] = float(char_raw[idx]) / max_char
        except Exception as exc:
            logger.warning("ICDCodingService.search char TF-IDF: %s", exc)

        # ── 3. Semantic scores (30% weight) ────────────────────────────
        sem_scores: dict[str, tuple[float, str]] = {}  # code -> (score, description)
        try:
            emb = self._embedder.encode([query], show_progress_bar=False).tolist()[0]
            n = min(top_k * 10, self._col.count())
            if n > 0:
                qr = self._col.query(
                    query_embeddings=[emb],
                    n_results=n,
                    include=["documents", "metadatas", "distances"],
                )
                for meta, distance in zip(qr["metadatas"][0], qr["distances"][0]):
                    score = max(0.0, 1.0 - distance / 2.0)
                    sem_scores[meta["code"]] = (score, meta["description"])
        except Exception as exc:
            logger.warning("ICDCodingService.search semantic: %s", exc)

        # ── 4. Merge: 40% word-kw + 30% char-kw + 30% semantic ────────
        ql = query.lower()
        all_codes = set(kw_scores) | set(char_scores) | set(sem_scores)
        merged: list[ICDSuggestion] = []
        for code in all_codes:
            kw = kw_scores.get(code, 0.0)
            ch = char_scores.get(code, 0.0)
            sem, desc = sem_scores.get(code, (0.0, ""))
            if not desc:
                try:
                    desc = self._cm.get_description(code)
                except Exception:
                    continue

            substr_boost = 0.15 if ql in desc.lower() else 0.0
            hybrid = round(min(0.4 * kw + 0.3 * ch + 0.3 * sem + substr_boost, 1.0), 4)
            if hybrid > 0:
                merged.append(
                    ICDSuggestion(
                        code=code,
                        description=desc,
                        confidence=hybrid,
                        source="hybrid",
                    )
                )

        merged.sort(key=lambda s: s.confidence, reverse=True)
        return merged[:top_k]

    # ------------------------------------------------------------------
    # Internal tiers
    # ------------------------------------------------------------------

    def _tier1_semantic(self, text: str, top_k: int, results: dict[str, ICDSuggestion]) -> None:
        embedding = self._embedder.encode([text], show_progress_bar=False).tolist()[0]
        n = min(top_k, self._col.count())
        if n == 0:
            return
        qr = self._col.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        for meta, distance in zip(qr["metadatas"][0], qr["distances"][0]):
            # ChromaDB cosine distance: 0 = identical, 2 = opposite
            confidence = round(max(0.0, 1.0 - distance / 2.0), 4)
            code = meta["code"]
            if code not in results or results[code].confidence < confidence:
                results[code] = ICDSuggestion(
                    code=code,
                    description=meta["description"],
                    confidence=confidence,
                    source="semantic",
                )

    def _tier2_entity(self, text: str, results: dict[str, ICDSuggestion]) -> None:
        """Extract clinical entities with scispacy and search each one separately."""
        try:
            doc = self._nlp(text[:512])
            seen_ents: set[str] = set()
            for ent in doc.ents:
                ent_text = ent.text.strip()
                if not ent_text or ent_text.lower() in seen_ents:
                    continue
                seen_ents.add(ent_text.lower())

                emb = self._embedder.encode([ent_text], show_progress_bar=False).tolist()[0]
                qr = self._col.query(
                    query_embeddings=[emb],
                    n_results=3,
                    include=["metadatas", "distances"],
                )
                for meta, distance in zip(qr["metadatas"][0], qr["distances"][0]):
                    # Slight penalty vs direct semantic so entity tier doesn't dominate
                    confidence = round(max(0.0, (1.0 - distance / 2.0) * 0.92), 4)
                    code = meta["code"]
                    if code not in results or results[code].confidence < confidence:
                        results[code] = ICDSuggestion(
                            code=code,
                            description=meta["description"],
                            confidence=confidence,
                            source="entity",
                        )
        except Exception as exc:
            logger.warning("ICDCodingService._tier2_entity: %s", exc)

    def _tier3_tfidf(self, text: str, top_k: int, results: dict[str, ICDSuggestion]) -> None:

        try:
            query_vec = self._tfidf.transform([text])
            scores = (self._tfidf_matrix @ query_vec.T).toarray().flatten()
            top_indices = scores.argsort()[-top_k:][::-1]
            for idx in top_indices:
                raw_score = float(scores[idx])
                if raw_score < 0.01:
                    continue
                code = self._codes[idx]
                # Scale TF-IDF score to confidence range (typically 0-0.3 raw)
                confidence = round(min(raw_score * 2.5, 0.85), 4)
                if code not in results:
                    results[code] = ICDSuggestion(
                        code=code,
                        description=self._descs[idx],
                        confidence=confidence,
                        source="tfidf",
                    )
        except Exception as exc:
            logger.warning("ICDCodingService._tier3_tfidf: %s", exc)
