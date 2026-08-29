from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

FHIR_BASE_URL = "https://parchee.local/fhir"
ICD10_CM_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10_PCS_SYSTEM = "http://www.cms.gov/Medicare/Coding/ICD10"
LOINC_SYSTEM = "http://loinc.org"


def build_patient_bundle(patient: dict[str, Any]) -> dict[str, Any]:
    """Build an MVP FHIR R4 Bundle for a stored Parchee patient record."""
    patient_id = _id(patient.get("id") or "draft")
    encounter_id = f"encounter-{patient_id}"
    now = datetime.now(timezone.utc).isoformat()
    entries: list[dict[str, Any]] = []

    entries.append(_entry("Patient", patient_id, _patient_resource(patient, patient_id)))
    entries.append(_entry("Encounter", encounter_id, _encounter_resource(patient, encounter_id, patient_id)))

    for observation in _vital_observations(patient, patient_id, encounter_id):
        entries.append(_entry("Observation", observation["id"], observation))

    for condition in _conditions(patient, patient_id, encounter_id):
        entries.append(_entry("Condition", condition["id"], condition))

    for medication in _medication_statements(patient, patient_id, encounter_id):
        entries.append(_entry("MedicationStatement", medication["id"], medication))

    for procedure in _procedures(patient, patient_id, encounter_id):
        entries.append(_entry("Procedure", procedure["id"], procedure))

    composition = _composition(patient, patient_id, encounter_id, now)
    if composition:
        entries.append(_entry("Composition", composition["id"], composition))

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "id": f"parchee-export-{patient_id}",
        "timestamp": now,
        "entry": entries,
    }


def _entry(resource_type: str, resource_id: str, resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "fullUrl": f"{FHIR_BASE_URL}/{resource_type}/{resource_id}",
        "resource": resource,
    }


def _patient_resource(patient: dict[str, Any], patient_id: str) -> dict[str, Any]:
    name = str(patient.get("name") or "Unknown").strip()
    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient_id,
        "identifier": [{"system": "https://parchee.local/patient-id", "value": patient_id}],
    }

    if name and name != "Unknown":
        resource["name"] = [{"text": name}]

    gender = _gender(patient.get("gender"))
    if gender:
        resource["gender"] = gender

    age = _age(patient.get("age"))
    if age is not None:
        resource["extension"] = [
            {
                "url": "https://parchee.local/fhir/StructureDefinition/recorded-age",
                "valueAge": {"value": age, "unit": "years", "system": "http://unitsofmeasure.org", "code": "a"},
            }
        ]

    if patient.get("location"):
        resource["address"] = [{"text": str(patient["location"])}]

    return resource


def _encounter_resource(patient: dict[str, Any], encounter_id: str, patient_id: str) -> dict[str, Any]:
    created_at = patient.get("created_at")
    resource: dict[str, Any] = {
        "resourceType": "Encounter",
        "id": encounter_id,
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    if created_at:
        resource["period"] = {"start": str(created_at)}
    if patient.get("chief_complaint"):
        resource["reasonCode"] = [{"text": str(patient["chief_complaint"])}]
    return resource


def _vital_observations(patient: dict[str, Any], patient_id: str, encounter_id: str) -> list[dict[str, Any]]:
    vitals = patient.get("vitals") or {}
    specs = [
        ("blood_pressure", "85354-9", "Blood pressure panel", "mmHg"),
        ("pulse", "8867-4", "Heart rate", "/min"),
        ("temperature", "8310-5", "Body temperature", "Cel"),
        ("spo2", "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", "%"),
    ]
    resources = []
    for key, loinc, display, unit in specs:
        value = vitals.get(key)
        if value in (None, ""):
            continue
        resources.append(
            {
                "resourceType": "Observation",
                "id": f"obs-{_id(patient_id)}-{key.replace('_', '-')}",
                "status": "final",
                "category": [
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                "code": "vital-signs",
                                "display": "Vital Signs",
                            }
                        ]
                    }
                ],
                "code": {"coding": [{"system": LOINC_SYSTEM, "code": loinc, "display": display}], "text": display},
                "subject": {"reference": f"Patient/{patient_id}"},
                "encounter": {"reference": f"Encounter/{encounter_id}"},
                "valueString": f"{value} {unit}".strip(),
            }
        )
    return resources


def _conditions(patient: dict[str, Any], patient_id: str, encounter_id: str) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for idx, code_entry in enumerate(patient.get("icd10_codes") or []):
        code = str(code_entry.get("code") or "").strip()
        description = str(code_entry.get("description") or "").strip()
        if not code and not description:
            continue
        conditions.append(
            _condition(
                f"condition-{patient_id}-{idx + 1}",
                patient_id,
                encounter_id,
                description or code,
                code=code or None,
            )
        )

    if not conditions:
        for idx, text in enumerate(
            filter(
                None,
                [
                    patient.get("tentative_doctor_diagnosis"),
                    patient.get("initial_llm_diagnosis"),
                    patient.get("chief_complaint"),
                ],
            )
        ):
            conditions.append(_condition(f"condition-{patient_id}-text-{idx + 1}", patient_id, encounter_id, str(text)))
    return conditions


def _condition(
    condition_id: str,
    patient_id: str,
    encounter_id: str,
    text: str,
    code: str | None = None,
) -> dict[str, Any]:
    coding = [{"system": ICD10_CM_SYSTEM, "code": code, "display": text}] if code else []
    return {
        "resourceType": "Condition",
        "id": condition_id,
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                }
            ]
        },
        "code": {"coding": coding, "text": text},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{encounter_id}"},
    }


def _medication_statements(patient: dict[str, Any], patient_id: str, encounter_id: str) -> list[dict[str, Any]]:
    resources = []
    for idx, medication in enumerate(patient.get("medications") or []):
        text = str(medication).strip()
        if not text:
            continue
        resources.append(
            {
                "resourceType": "MedicationStatement",
                "id": f"medication-{patient_id}-{idx + 1}",
                "status": "active",
                "medicationCodeableConcept": {"text": text},
                "subject": {"reference": f"Patient/{patient_id}"},
                "context": {"reference": f"Encounter/{encounter_id}"},
            }
        )
    return resources


def _procedures(patient: dict[str, Any], patient_id: str, encounter_id: str) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    for idx, code_entry in enumerate(patient.get("procedure_codes") or []):
        code = str(code_entry.get("code") or "").strip()
        description = str(code_entry.get("description") or "").strip()
        text = description or code
        if not text:
            continue
        seen_texts.add(text.lower())
        resources.append(
            _procedure(f"procedure-{patient_id}-{idx + 1}", patient_id, encounter_id, text, code=code or None)
        )

    for procedure in patient.get("procedures") or []:
        text = str(procedure).strip()
        if not text or text.lower() in seen_texts:
            continue
        seen_texts.add(text.lower())
        resources.append(
            _procedure(f"procedure-{patient_id}-text-{len(resources) + 1}", patient_id, encounter_id, text)
        )

    return resources


def _procedure(
    procedure_id: str,
    patient_id: str,
    encounter_id: str,
    text: str,
    code: str | None = None,
) -> dict[str, Any]:
    coding = [{"system": ICD10_PCS_SYSTEM, "code": code, "display": text}] if code else []
    return {
        "resourceType": "Procedure",
        "id": procedure_id,
        "status": "completed",
        "code": {"coding": coding, "text": text},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{encounter_id}"},
    }


def _composition(
    patient: dict[str, Any],
    patient_id: str,
    encounter_id: str,
    timestamp: str,
) -> dict[str, Any] | None:
    summary = patient.get("transcript_summary") or patient.get("chief_complaint")
    if not summary:
        return None

    sections = [
        ("Chief Complaint", patient.get("chief_complaint")),
        ("Summary", patient.get("transcript_summary")),
        ("Symptoms", ", ".join(patient.get("symptoms") or [])),
        ("Medications", ", ".join(patient.get("medications") or [])),
        ("Claim Readiness", _claim_text(patient)),
    ]

    return {
        "resourceType": "Composition",
        "id": f"composition-{patient_id}",
        "status": "final",
        "type": {"text": "Parchee Edge clinical note"},
        "subject": {"reference": f"Patient/{patient_id}"},
        "encounter": {"reference": f"Encounter/{encounter_id}"},
        "date": timestamp,
        "title": "Parchee Edge Clinical Documentation Export",
        "section": [
            {"title": title, "text": {"status": "generated", "div": f"<div>{_escape(text)}</div>"}}
            for title, text in sections
            if text
        ],
    }


def _claim_text(patient: dict[str, Any]) -> str:
    parts = []
    for label, key in [
        ("Ration card", "ration_card_type"),
        ("Income", "income"),
        ("Occupation", "occupation"),
        ("Caste category", "caste_category"),
        ("Housing", "housing_type"),
        ("Location", "location"),
    ]:
        value = patient.get(key) or patient.get("income_bracket")
        if value:
            parts.append(f"{label}: {value}")
    return "; ".join(parts)


def _id(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\-\.]", "-", str(value))
    return cleaned.strip("-") or "unknown"


def _age(value: Any) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return int(match.group(0))


def _gender(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("m"):
        return "male"
    if normalized.startswith("f"):
        return "female"
    if normalized in {"other", "unknown"}:
        return normalized
    return None


def _escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
