# backend/app/api/ehr.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.schema import PatientData
from app.database import (
    delete_patient,
    get_all_patients,
    get_patient_by_id,
    save_patient,
    update_patient,
    update_patient_billing,
    update_patient_summary,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models for billing endpoints
# ---------------------------------------------------------------------------


class ICDSuggestRequest(BaseModel):
    chief_complaint: Optional[str] = None
    symptoms: Optional[List[str]] = None
    diagnosis_text: Optional[str] = None
    top_k: int = 5


class ProcedureSuggestRequest(BaseModel):
    procedures: Optional[List[str]] = None
    medications: Optional[List[str]] = None
    top_k: int = 5


class CodeSearchRequest(BaseModel):
    query: str
    code_type: str = "diagnosis"  # "diagnosis" | "procedure"
    top_k: int = 10
    min_confidence: float = 0.35  # reject results below 35% — avoids nonsensical matches


class BillingCodesPatch(BaseModel):
    icd10_codes: Optional[List[Dict[str, Any]]] = None
    procedure_codes: Optional[List[Dict[str, Any]]] = None
    billing_summary: Optional[Dict[str, Any]] = None


class FHIRExportRequest(PatientData):
    pass


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------


async def _generate_and_save_summary(patient_id: int, transcript_history: list[str]) -> None:
    """Background task: generates transcript summary with retries and saves to DB."""
    from app.services.summarizer import generate_consultation_summary_async

    try:
        summary = await generate_consultation_summary_async(transcript_history)
        if summary and summary != "Error generating summary.":
            update_patient_summary(patient_id, summary)
            logger.info("Background summary saved for patient %d", patient_id)
        else:
            logger.warning("Background summary generation failed for patient %d", patient_id)
    except Exception as exc:
        logger.error("Background summary error for patient %d: %s", patient_id, exc)


async def _run_billing_automation(patient_id: int, data: PatientData) -> None:
    """
    Background task: runs ICD-10-CM + ICD-10-PCS coding and saves the billing claim.
    Mirrors _generate_and_save_summary — fires after every new EHR commit.
    """
    try:
        from app.services.billing_service import BillingService
        from app.services.icd_coding_service import ICDCodingService
        from app.services.procedure_coding_service import ProcedureCodingService

        dx_text = " ".join(filter(None, [data.tentative_doctor_diagnosis, data.initial_llm_diagnosis]))

        dx_service = ICDCodingService()
        px_service = ProcedureCodingService()

        dx_codes = dx_service.suggest(
            chief_complaint=data.chief_complaint,
            symptoms=data.symptoms or [],
            diagnosis_text=dx_text or None,
            top_k=5,
        )

        px_codes = px_service.suggest(
            procedures=data.procedures or [],
            medications=data.medications or [],
            top_k=5,
        )

        claim = BillingService().assemble(
            patient_id=patient_id,
            patient_name=data.name,
            diagnosis_codes=dx_codes,
            procedure_codes=px_codes,
            chief_complaint=data.chief_complaint,
            symptoms=data.symptoms or [],
            medications=data.medications or [],
            procedures_performed=data.procedures or [],
        )

        update_patient_billing(
            patient_id=patient_id,
            icd10_codes=[s.model_dump() for s in dx_codes],
            procedure_codes=[s.model_dump() for s in px_codes],
            billing_summary=claim.model_dump(),
        )
        logger.info("Billing automation complete for patient %d", patient_id)

    except Exception as exc:
        logger.error("Billing automation failed for patient %d: %s", patient_id, exc)


# ---------------------------------------------------------------------------
# Core EHR endpoints
# ---------------------------------------------------------------------------


@router.post("/commit")
async def commit_to_ehr(data: PatientData, background_tasks: BackgroundTasks):
    logger.debug("API /commit received: %s", data.name)
    try:
        if data.id is not None:
            updated = update_patient(data.id, data)
            if not updated:
                raise HTTPException(status_code=404, detail=f"Patient {data.id} not found")
            return {
                "status": "success",
                "message": "Patient data updated in EHR",
                "patient_id": data.id,
                "mode": "updated",
            }

        patient_id = save_patient(data)

        # Generate transcript summary in background (non-blocking)
        if data.transcript_history:
            logger.info("Scheduling background summary for patient %d …", patient_id)
            background_tasks.add_task(_generate_and_save_summary, patient_id, data.transcript_history)

        # Auto-code billing in background (non-blocking)
        logger.info("Scheduling billing automation for patient %d …", patient_id)
        background_tasks.add_task(_run_billing_automation, patient_id, data)

        return {
            "status": "success",
            "message": "Patient data committed to EHR",
            "patient_id": patient_id,
            "mode": "created",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error in commit_to_ehr: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/patients")
async def get_patients():
    try:
        return get_all_patients()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/patients/{patient_id}")
async def get_single_patient(patient_id: int):
    try:
        patient = get_patient_by_id(patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        return patient
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/patients/{patient_id}")
async def update_patient_endpoint(patient_id: int, data: PatientData):
    try:
        updated = update_patient(patient_id, data)
        if not updated:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        return {"status": "success", "message": f"Patient {patient_id} updated", "patient_id": patient_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/patients/{patient_id}")
async def delete_patient_endpoint(patient_id: int):
    try:
        delete_patient(patient_id)
        return {"status": "success", "message": f"Patient {patient_id} deleted"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/patients/{patient_id}/fhir")
async def export_patient_fhir(patient_id: int):
    """Return an MVP FHIR R4 Bundle for a stored patient record."""
    try:
        patient = get_patient_by_id(patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        from app.services.fhir_export_service import build_patient_bundle

        return build_patient_bundle(patient)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("FHIR export error for patient %d: %s", patient_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/fhir/export")
async def export_current_fhir(data: FHIRExportRequest):
    """Return an MVP FHIR R4 Bundle for the current unsaved scribe state."""
    try:
        from app.services.fhir_export_service import build_patient_bundle

        return build_patient_bundle(data.model_dump())
    except Exception as exc:
        logger.error("FHIR draft export error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Billing automation endpoints
# ---------------------------------------------------------------------------


@router.post("/icd-suggest")
async def suggest_icd_codes(req: ICDSuggestRequest):
    """
    On-demand ICD-10-CM diagnosis code suggestion.
    Fully offline — no internet required after initial model download.
    """
    try:
        from app.services.icd_coding_service import ICDCodingService

        suggestions = ICDCodingService().suggest(
            chief_complaint=req.chief_complaint,
            symptoms=req.symptoms or [],
            diagnosis_text=req.diagnosis_text,
            top_k=req.top_k,
        )
        return {"suggestions": [s.model_dump() for s in suggestions]}
    except Exception as exc:
        logger.error("icd-suggest error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/procedure-suggest")
async def suggest_procedure_codes(req: ProcedureSuggestRequest):
    """
    On-demand ICD-10-PCS procedure code suggestion.
    Fully offline — no internet required after initial model download.
    """
    try:
        from app.services.procedure_coding_service import ProcedureCodingService

        suggestions = ProcedureCodingService().suggest(
            procedures=req.procedures or [],
            medications=req.medications or [],
            top_k=req.top_k,
        )
        return {"suggestions": [s.model_dump() for s in suggestions]}
    except Exception as exc:
        logger.error("procedure-suggest error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/code-search")
async def search_codes(req: CodeSearchRequest):
    """
    Offline code browser search — used by the Diagnostics & Billing page.
    Searches both ICD-10-CM (diagnosis) and ICD-10-PCS (procedure) collections.
    """
    try:
        if req.code_type == "procedure":
            from app.services.procedure_coding_service import ProcedureCodingService

            results = ProcedureCodingService().search(query=req.query, top_k=req.top_k)
        else:
            from app.services.icd_coding_service import ICDCodingService

            results = ICDCodingService().search(query=req.query, top_k=req.top_k)
        # Filter out low-confidence results (e.g. "fever" in procedure search)
        filtered = [r for r in results if r.confidence >= req.min_confidence]
        return {"results": [r.model_dump() for r in filtered], "code_type": req.code_type}
    except Exception as exc:
        logger.error("code-search error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/patients/{patient_id}/billing")
async def get_patient_billing(patient_id: int):
    """Return the full billing claim for a patient."""
    try:
        patient = get_patient_by_id(patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        return {
            "patient_id": patient_id,
            "icd10_codes": patient.get("icd10_codes") or [],
            "procedure_codes": patient.get("procedure_codes") or [],
            "billing_summary": patient.get("billing_summary"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/patients/{patient_id}/billing")
async def patch_patient_billing(patient_id: int, payload: BillingCodesPatch):
    """
    Persist confirmed/edited billing codes for a patient.
    Called by the frontend Diagnostics page after clinician review.
    """
    try:
        patient = get_patient_by_id(patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Merge with existing data where fields not provided
        icd10 = payload.icd10_codes if payload.icd10_codes is not None else (patient.get("icd10_codes") or [])
        pcs = payload.procedure_codes if payload.procedure_codes is not None else (patient.get("procedure_codes") or [])

        existing_billing = patient.get("billing_summary") or {}
        billing = payload.billing_summary if payload.billing_summary is not None else existing_billing
        # Mark as confirmed if clinician is patching
        if isinstance(billing, dict):
            billing["coding_status"] = "confirmed"

        update_patient_billing(
            patient_id=patient_id,
            icd10_codes=icd10,
            procedure_codes=pcs,
            billing_summary=billing,
        )
        return {"status": "success", "patient_id": patient_id, "coding_status": "confirmed"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/analytics/trends")
async def get_clinical_trends():
    """
    Aggregate diagnosis and procedure code trends across all patients.
    Used by the Diagnostics & Billing Center page.
    """
    try:
        from collections import Counter

        patients = get_all_patients()

        dx_counter: Counter = Counter()
        px_counter: Counter = Counter()
        symptom_counter: Counter = Counter()

        for p in patients:
            for code_entry in p.get("icd10_codes") or []:
                if isinstance(code_entry, dict) and code_entry.get("code"):
                    dx_counter[f"{code_entry['code']} — {code_entry.get('description', '')}"] += 1
            for code_entry in p.get("procedure_codes") or []:
                if isinstance(code_entry, dict) and code_entry.get("code"):
                    px_counter[f"{code_entry['code']} — {code_entry.get('description', '')}"] += 1
            for symptom in p.get("symptoms") or []:
                if symptom:
                    symptom_counter[symptom.lower()] += 1

        return {
            "top_diagnoses": [{"label": k, "count": v} for k, v in dx_counter.most_common(10)],
            "top_procedures": [{"label": k, "count": v} for k, v in px_counter.most_common(10)],
            "top_symptoms": [{"label": k, "count": v} for k, v in symptom_counter.most_common(10)],
            "total_patients": len(patients),
        }
    except Exception as exc:
        logger.error("analytics/trends error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
