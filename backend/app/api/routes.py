import datetime

from fastapi import APIRouter, HTTPException

from app.core.schema import PatientData

router = APIRouter()


def _fallback_note(data: PatientData) -> str:
    diagnosis = data.tentative_doctor_diagnosis or data.initial_llm_diagnosis or "Pending clinician review"
    procedures = ", ".join(data.procedures) if data.procedures else "None recorded"
    history = ", ".join([*data.medical_history, *data.family_history]) or "None recorded"

    return f"""
PARCHEE EDGE - CLINICAL NOTE
Date: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
--------------------------------------------------
PATIENT DETAILS
Name: {data.name or "N/A"}
Age: {data.age or "N/A"} | Gender: {data.gender or "N/A"}

CHIEF COMPLAINT
{data.chief_complaint or "Not recorded"}

VITALS
BP: {data.vitals.blood_pressure or "N/A"}
Pulse: {data.vitals.pulse or "N/A"} bpm
Temp: {data.vitals.temperature or "N/A"}
SpO2: {data.vitals.spo2 or "N/A"}%

SYMPTOMS
{", ".join(data.symptoms) if data.symptoms else "None reported"}

HISTORY
{history}

ASSESSMENT
{diagnosis}

MEDICATIONS
{", ".join(data.medications) if data.medications else "None prescribed"}

PROCEDURES
{procedures}
--------------------------------------------------
""".strip()


@router.post("/generate-note")
async def generate_clinical_note(data: PatientData):
    """
    Receives structured PatientData and returns a clinician-reviewable note.
    Uses local Gemma through llama.cpp, with deterministic formatting fallback.
    """
    try:
        from app.services.summarizer import generate_clinical_note_async

        note = await generate_clinical_note_async(data.model_dump())
        return {"note": note or _fallback_note(data)}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
