# backend/app/core/schema.py
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class Vitals(BaseModel):
    temperature: Optional[str] = Field(None, description="Body Temperature (e.g., 98.6 F)")
    blood_pressure: Optional[str] = Field(None, description="Blood Pressure (e.g., 120/80 mmHg)")
    pulse: Optional[str] = Field(None, description="Heart Rate in BPM")
    spo2: Optional[str] = Field(None, description="Oxygen Saturation in %")


class PatientData(BaseModel):
    id: Optional[int] = Field(None, description="Existing patient record ID for updates")

    # Demographics
    name: Optional[str] = Field(None, description="Patient's full name")
    age: Optional[str] = Field(None, description="Patient's age in years")
    gender: Optional[str] = Field(None, description="Patient's gender (Male/Female/Other)")

    # Clinical Signs
    chief_complaint: Optional[str] = Field(None, description="Primary reason for visit")
    symptoms: List[str] = Field(default_factory=list, description="List of reported symptoms")

    # Vitals - Nested model for better organization
    vitals: Vitals = Field(default_factory=Vitals, description="Patient vitals")

    # History & Diagnosis
    medical_history: List[str] = Field(default_factory=list, description="Patient's past medical conditions")
    family_history: List[str] = Field(default_factory=list, description="Family medical history")
    allergies: List[str] = Field(default_factory=list, description="Known allergies")

    # Diagnosis (Split)
    tentative_doctor_diagnosis: Optional[str] = Field(
        None, description="Diagnosis explicitly inferred or stated by the doctor"
    )
    initial_llm_diagnosis: Optional[str] = Field(
        None, description="Diagnosis inferred by the AI based on symptoms/history"
    )

    medications: List[str] = Field(default_factory=list, description="Prescribed medications")

    # Procedures performed during visit (captured by scribe)
    procedures: List[str] = Field(
        default_factory=list, description="Clinical procedures performed during the encounter"
    )

    # Eligibility & Schemes
    ration_card_type: Optional[str] = Field(None, description="e.g., BPL, Antyodaya (AAY), PHH")
    income: Optional[str] = Field(None, description="Reported income (monthly or annual)")

    # Legacy key kept for backward compatibility with old DB records / snapshots
    income_bracket: Optional[str] = Field(None, description="Deprecated – use income")
    occupation: Optional[str] = Field(None, description="Primary occupation (e.g., Casual Labour, Farmer)")
    caste_category: Optional[str] = Field(None, description="SC/ST/General/OBC")
    housing_type: Optional[str] = Field(None, description="Kucha/Pucca house")
    location: Optional[str] = Field(None, description="Patient residence location/state")
    scheme_eligibility: Optional[Dict[str, Any]] = Field(None, description="Computed eligibility snapshot")

    # Billing & ICD Coding (auto-populated by background task after EHR commit)
    icd10_codes: Optional[List[Dict[str, Any]]] = Field(
        None, description="ICD-10-CM diagnosis codes auto-suggested for this encounter"
    )
    procedure_codes: Optional[List[Dict[str, Any]]] = Field(
        None, description="ICD-10-PCS procedure codes auto-suggested for this encounter"
    )
    billing_summary: Optional[Dict[str, Any]] = Field(
        None, description="Assembled billing claim object (insurer-agnostic)"
    )

    # Metadata (Useful for Phase 2 DB storage)
    consultation_id: Optional[str] = None
    timestamp: Optional[str] = None
    transcript_summary: Optional[str] = Field(None, description="Important points from the conversation transcript")
    transcript_history: Optional[List[str]] = Field(
        None, description="Full conversation history for summarization (not stored)"
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_income_field(cls, values: Any) -> Any:
        """Normalize legacy income_bracket into income."""
        if isinstance(values, dict):
            values = dict(values)
            if not values.get("income") and values.get("income_bracket"):
                values["income"] = values["income_bracket"]
        return values
