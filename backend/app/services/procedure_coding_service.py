# backend/app/services/procedure_coding_service.py
"""
ICD-10-PCS (procedure) auto-coding service.
Uses the CMS FY2025 ICD-10-PCS order file (publicly available, no license required).

On first startup the order file is downloaded from CMS, parsed, and indexed into
a persistent ChromaDB collection. Subsequent starts are instant.

3-tier pipeline mirrors icd_coding_service:
  Tier 1: semantic (sentence-transformers + ChromaDB)
  Tier 2: scispacy entity extraction -> semantic lookup per entity
  Tier 3: TF-IDF cosine similarity
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CHROMA_PCS_DIR = str(_DATA_DIR / "chroma" / "icd_pcs")
_PCS_TXT_PATH = _DATA_DIR / "icd10pcs_order_2025.txt"
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
_COLLECTION_NAME = "icd10_pcs_v2"

# CMS FY2025 ICD-10-PCS order file (public domain)
_CMS_PCS_URL = "https://www.cms.gov/files/zip/2025-icd-10-pcs-order-file-long-and-abbreviated-titles.zip"


class ProcedureSuggestion(BaseModel):
    code: str
    description: str
    confidence: float
    source: str  # "semantic" | "entity" | "tfidf" | "exact"


class ProcedureCodingService:
    """Singleton ICD-10-PCS coding service."""

    _instance: Optional["ProcedureCodingService"] = None
    _ready: bool = False

    def __new__(cls) -> "ProcedureCodingService":
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
        Path(_CHROMA_PCS_DIR).mkdir(parents=True, exist_ok=True)

        logger.info("ProcedureCodingService: attaching shared embedding model …")
        from app.services.shared_embedder import get_embedder

        self._embedder = get_embedder()

        import chromadb

        self._chroma = chromadb.PersistentClient(path=_CHROMA_PCS_DIR)
        self._col = self._chroma.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        # Ensure the raw PCS text file exists
        if not _PCS_TXT_PATH.exists():
            self._download_pcs_file()

        # Parse into codes/descriptions (populated once, cached in self._codes/_descs)
        self._codes, self._descs = self._parse_pcs_file()

        if self._col.count() == 0:
            self._populate()
        else:
            logger.info(
                "ProcedureCodingService: ChromaDB collection ready (%d ICD-10-PCS codes)",
                self._col.count(),
            )

        logger.info("ProcedureCodingService: building TF-IDF index (%d codes) …", len(self._codes))
        import joblib
        from sklearn.feature_extraction.text import TfidfVectorizer

        _cache_prefix = _DATA_DIR / "tfidf_cache" / f"icd_pcs_{len(self._codes)}"
        _cache_prefix.parent.mkdir(parents=True, exist_ok=True)
        _word_path = str(_cache_prefix) + "_word.joblib"
        _char_path = str(_cache_prefix) + "_char.joblib"

        if Path(_word_path).exists() and Path(_char_path).exists():
            logger.info("ProcedureCodingService: loading TF-IDF indexes from cache …")
            self._tfidf, self._tfidf_matrix = joblib.load(_word_path)
            self._char_tfidf, self._char_tfidf_matrix = joblib.load(_char_path)
        else:
            # Word-level TF-IDF (word bigrams)
            self._tfidf = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
            self._tfidf_matrix = self._tfidf.fit_transform(self._descs)
            # Character-level n-gram TF-IDF — enables partial word / typo matching
            self._char_tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), min_df=1, sublinear_tf=True)
            self._char_tfidf_matrix = self._char_tfidf.fit_transform(self._descs)
            joblib.dump((self._tfidf, self._tfidf_matrix), _word_path)
            joblib.dump((self._char_tfidf, self._char_tfidf_matrix), _char_path)
            logger.info("ProcedureCodingService: TF-IDF indexes saved to disk.")

        # Optional scispacy NER (shared model assumed already loaded in ICDCodingService)
        self._nlp = None
        try:
            import spacy

            self._nlp = spacy.load("en_core_sci_md")
            logger.info("ProcedureCodingService: scispacy en_core_sci_md loaded")
        except Exception as exc:
            logger.warning("ProcedureCodingService: scispacy unavailable (%s) — entity tier skipped", exc)

        logger.info("ProcedureCodingService: ready")

    def _download_pcs_file(self) -> None:
        logger.info("ProcedureCodingService: downloading ICD-10-PCS FY2025 order file from CMS …")
        try:
            response = requests.get(_CMS_PCS_URL, timeout=120, stream=True)
            response.raise_for_status()
            content = response.content
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                # Find the .txt order file inside the zip
                txt_names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
                if not txt_names:
                    raise RuntimeError("No .txt file found inside CMS PCS zip archive")
                # Use the largest txt (the order file with descriptions)
                main_txt = max(txt_names, key=lambda n: zf.getinfo(n).file_size)
                with zf.open(main_txt) as f:
                    _PCS_TXT_PATH.write_bytes(f.read())
            logger.info("ProcedureCodingService: ICD-10-PCS order file saved to %s", _PCS_TXT_PATH)
        except Exception as exc:
            logger.error(
                "ProcedureCodingService: failed to download ICD-10-PCS file: %s. Procedure coding will be unavailable.",
                exc,
            )
            raise

    def _parse_pcs_file(self) -> tuple[list[str], list[str]]:
        """
        Parse the CMS fixed-width ICD-10-PCS order file.
        Format (fixed-width):
          cols  1-5  : sequence number
          col   6    : space
          cols  7-13 : 7-character ICD-10-PCS code
          col   14   : space
          col   15   : valid flag (1 = valid billable code, 0 = header)
          col   16   : space
          cols 17-77 : abbreviated description (61 chars)
          col   78   : space
          cols 79+   : long description
        """
        codes: list[str] = []
        descs: list[str] = []

        try:
            lines = _PCS_TXT_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            logger.error("ProcedureCodingService: cannot read PCS file: %s", exc)
            return codes, descs

        unique_data: dict[str, str] = {}
        for line in lines:
            if len(line) < 16:
                continue
            valid_flag = line[14].strip()
            if valid_flag != "1":
                continue  # skip header/section rows
            code = line[6:13].strip()
            if len(code) != 7:
                continue
            # CMS fixed-width format (1-indexed):
            #   cols 17-76: short description (60 chars) → 0-indexed: [16:76]
            #   col  77   : space                        → 0-indexed: [76]
            #   cols 78+  : long description             → 0-indexed: [77:]
            long_desc = line[77:].strip() if len(line) > 77 else ""
            short_desc = line[16:76].strip()
            description = long_desc or short_desc
            if description:
                unique_data[code] = description

        codes = list(unique_data.keys())
        descs = list(unique_data.values())

        logger.info("ProcedureCodingService: parsed %d valid ICD-10-PCS codes", len(codes))
        return codes, descs

    def _populate(self) -> None:
        logger.info("ProcedureCodingService: first-run — populating ChromaDB from ICD-10-PCS …")
        total = len(self._codes)

        logger.info(
            "ProcedureCodingService: computing embeddings for %d codes (this may take a few minutes on first run) …",
            total,
        )
        from app.services.shared_embedder import encode_with_progress

        embeddings = encode_with_progress(self._descs, batch_size=512, label="ICD-PCS embeddings")

        logger.info("ProcedureCodingService: embeddings complete, upserting to ChromaDB …")

        batch_size = 2048
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_codes = self._codes[start:end]
            batch_descs = self._descs[start:end]
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

        logger.info("ProcedureCodingService: populated %d ICD-10-PCS codes", total)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def suggest(
        self,
        procedures: Optional[list[str]] = None,
        medications: Optional[list[str]] = None,
        top_k: int = 5,
    ) -> list[ProcedureSuggestion]:
        """Return top-k ICD-10-PCS procedure code suggestions."""
        parts: list[str] = []
        if procedures:
            parts.extend(p.strip() for p in procedures if p.strip())
        if medications:
            parts.extend(m.strip() for m in medications if m.strip())

        if not parts:
            return []

        text = ". ".join(parts)
        results: dict[str, ProcedureSuggestion] = {}

        self._tier1_semantic(text, top_k * 3, results)
        if self._nlp is not None:
            self._tier2_entity(text, results)
        self._tier3_tfidf(text, top_k * 3, results)

        ranked = sorted(results.values(), key=lambda s: s.confidence, reverse=True)
        return ranked[:top_k]

    def search(self, query: str, top_k: int = 10) -> list[ProcedureSuggestion]:
        """
        Keyword-dominant hybrid search for the code browser.
        Scoring: 70% TF-IDF keyword + 30% semantic similarity.
        Exact code prefix and substring description matches get a priority boost.
        """
        import numpy as np

        query = query.strip()
        if not query:
            return []

        # ── 1. Exact / prefix code match (e.g. "0B11", "0BH") ─────────
        normalised = query.upper().replace(" ", "")

        # Exact single-code lookup — return immediately if user typed a full code
        for code, desc in zip(self._codes, self._descs):
            if code.upper() == normalised:
                return [
                    ProcedureSuggestion(
                        code=code,
                        description=desc,
                        confidence=1.0,
                        source="exact",
                    )
                ]

        prefix_hits: list[ProcedureSuggestion] = []
        for code, desc in zip(self._codes, self._descs):
            if code.upper().startswith(normalised):
                prefix_hits.append(
                    ProcedureSuggestion(
                        code=code,
                        description=desc,
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
            logger.warning("ProcedureCodingService.search TF-IDF: %s", exc)

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
            logger.warning("ProcedureCodingService.search char TF-IDF: %s", exc)

        # ── 3. Semantic scores (30% weight) ────────────────────────────
        sem_scores: dict[str, tuple[float, str]] = {}
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
            logger.warning("ProcedureCodingService.search semantic: %s", exc)

        # ── 4. Merge: 40% word-kw + 30% char-kw + 30% semantic ────────
        ql = query.lower()
        all_codes = set(kw_scores) | set(char_scores) | set(sem_scores)
        merged: list[ProcedureSuggestion] = []
        for code in all_codes:
            kw = kw_scores.get(code, 0.0)
            ch = char_scores.get(code, 0.0)
            sem, desc = sem_scores.get(code, (0.0, ""))
            if not desc:
                try:
                    idx = self._codes.index(code)
                    desc = self._descs[idx]
                except (ValueError, IndexError):
                    continue

            substr_boost = 0.15 if ql in desc.lower() else 0.0
            hybrid = round(min(0.4 * kw + 0.3 * ch + 0.3 * sem + substr_boost, 1.0), 4)
            if hybrid > 0:
                merged.append(
                    ProcedureSuggestion(
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

    def _tier1_semantic(self, text: str, top_k: int, results: dict[str, ProcedureSuggestion]) -> None:
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
            confidence = round(max(0.0, 1.0 - distance / 2.0), 4)
            code = meta["code"]
            if code not in results or results[code].confidence < confidence:
                results[code] = ProcedureSuggestion(
                    code=code,
                    description=meta["description"],
                    confidence=confidence,
                    source="semantic",
                )

    def _tier2_entity(self, text: str, results: dict[str, ProcedureSuggestion]) -> None:
        try:
            doc = self._nlp(text[:512])
            seen: set[str] = set()
            for ent in doc.ents:
                ent_text = ent.text.strip()
                if not ent_text or ent_text.lower() in seen:
                    continue
                seen.add(ent_text.lower())
                emb = self._embedder.encode([ent_text], show_progress_bar=False).tolist()[0]
                qr = self._col.query(
                    query_embeddings=[emb],
                    n_results=3,
                    include=["metadatas", "distances"],
                )
                for meta, distance in zip(qr["metadatas"][0], qr["distances"][0]):
                    confidence = round(max(0.0, (1.0 - distance / 2.0) * 0.92), 4)
                    code = meta["code"]
                    if code not in results or results[code].confidence < confidence:
                        results[code] = ProcedureSuggestion(
                            code=code,
                            description=meta["description"],
                            confidence=confidence,
                            source="entity",
                        )
        except Exception as exc:
            logger.warning("ProcedureCodingService._tier2_entity: %s", exc)

    def _tier3_tfidf(self, text: str, top_k: int, results: dict[str, ProcedureSuggestion]) -> None:
        try:
            query_vec = self._tfidf.transform([text])
            scores = (self._tfidf_matrix @ query_vec.T).toarray().flatten()
            top_indices = scores.argsort()[-top_k:][::-1]
            for idx in top_indices:
                raw_score = float(scores[idx])
                if raw_score < 0.01:
                    continue
                code = self._codes[idx]
                confidence = round(min(raw_score * 2.5, 0.85), 4)
                if code not in results:
                    results[code] = ProcedureSuggestion(
                        code=code,
                        description=self._descs[idx],
                        confidence=confidence,
                        source="tfidf",
                    )
        except Exception as exc:
            logger.warning("ProcedureCodingService._tier3_tfidf: %s", exc)
