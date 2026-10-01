# backend/app/database.py
import base64
import json
import logging
import os
import secrets

import psycopg2
import psycopg2.extras
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg2 import sql

from app.core.schema import PatientData

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ruralmed")


def _get_aes_key() -> bytes:
    key_b64 = os.getenv("AES_256_KEY")
    if not key_b64:
        raise ValueError("AES_256_KEY environment variable is required (base64-encoded 32-byte key)")

    try:
        key = base64.b64decode(key_b64)
    except Exception as exc:
        raise ValueError("AES_256_KEY must be valid base64") from exc

    if len(key) != 32:
        raise ValueError("AES_256_KEY must decode to exactly 32 bytes for AES-256-GCM")
    return key


def encrypt_text(plain_text: str | None) -> str | None:
    if plain_text is None:
        return None

    key = _get_aes_key()
    aesgcm = AESGCM(key)
    iv = secrets.token_bytes(12)
    ciphertext = aesgcm.encrypt(iv, plain_text.encode("utf-8"), None)
    return base64.b64encode(iv + ciphertext).decode("utf-8")


def decrypt_text(cipher_text_b64: str | None) -> str | None:
    if cipher_text_b64 is None:
        return None

    key = _get_aes_key()
    raw = base64.b64decode(cipher_text_b64)
    iv, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    plain = aesgcm.decrypt(iv, ciphertext, None)
    return plain.decode("utf-8")


def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id SERIAL PRIMARY KEY,
            name TEXT,
            age TEXT,
            gender TEXT,
            chief_complaint TEXT,
            symptoms TEXT, -- JSON
            temp TEXT,
            bp TEXT,
            pulse TEXT,
            spo2 TEXT,
            medical_history TEXT, -- JSON
            family_history TEXT, -- JSON
            allergies TEXT, -- JSON
            tentative_doctor_diagnosis TEXT,
            initial_llm_diagnosis TEXT,
            medications TEXT, -- JSON
            transcript_summary TEXT,
            ration_card_type TEXT,
            income_bracket TEXT,
            occupation TEXT,
            caste_category TEXT,
            housing_type TEXT,
            location TEXT,
            scheme_eligibility TEXT, -- JSON
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Forward-compatible column migrations for existing PostgreSQL tables
    migration_columns = [
        "tentative_doctor_diagnosis",
        "initial_llm_diagnosis",
        "family_history",
        "ration_card_type",
        "income_bracket",
        "occupation",
        "caste_category",
        "housing_type",
        "scheme_eligibility",
        "location",
        "transcript_summary",
        # Billing & ICD coding columns
        "procedures",
        "icd10_codes",
        "procedure_codes",
        "billing_summary",
    ]
    for column in migration_columns:
        cursor.execute(sql.SQL("ALTER TABLE patients ADD COLUMN IF NOT EXISTS {} TEXT").format(sql.Identifier(column)))

    conn.commit()
    conn.close()


def save_patient(data: PatientData):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # helper for json list dumps
    def to_json(val):
        return json.dumps(val) if val else "[]"

    def to_json_obj(val):
        return json.dumps(val) if val is not None else None

    # Safely get vitals from Pydantic model
    v = data.vitals
    logger.debug("save_patient received data.vitals: %s", v)
    logger.debug("save_patient full data: %s", data.model_dump_json())

    cursor.execute(
        """
        INSERT INTO patients (
            name, age, gender, chief_complaint, symptoms,
            temp, bp, pulse, spo2,
            medical_history, family_history, allergies,
            tentative_doctor_diagnosis, initial_llm_diagnosis,
            medications, transcript_summary,
            ration_card_type, income_bracket, occupation, caste_category,
            housing_type, location, scheme_eligibility,
            procedures, icd10_codes, procedure_codes, billing_summary
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """,
        (
            encrypt_text(data.name),
            encrypt_text(data.age),
            encrypt_text(data.gender),
            encrypt_text(data.chief_complaint),
            to_json(data.symptoms),
            encrypt_text(v.temperature if v else None),
            encrypt_text(v.blood_pressure if v else None),
            encrypt_text(v.pulse if v else None),
            encrypt_text(v.spo2 if v else None),
            to_json(data.medical_history),
            to_json(data.family_history),
            to_json(data.allergies),
            encrypt_text(data.tentative_doctor_diagnosis),
            encrypt_text(data.initial_llm_diagnosis),
            to_json(data.medications),
            encrypt_text(data.transcript_summary),
            encrypt_text(data.ration_card_type),
            encrypt_text(data.income),
            encrypt_text(data.occupation),
            encrypt_text(data.caste_category),
            encrypt_text(data.housing_type),
            encrypt_text(data.location),
            to_json_obj(data.scheme_eligibility),
            to_json(data.procedures),
            to_json_obj(data.icd10_codes),
            to_json_obj(data.procedure_codes),
            to_json_obj(data.billing_summary),
        ),
    )

    cursor.execute("SELECT currval(pg_get_serial_sequence('patients','id')) AS id")
    patient_id = cursor.fetchone()["id"]
    conn.commit()
    conn.close()
    return patient_id


def update_patient(patient_id: int, data: PatientData):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def to_json(val):
        return json.dumps(val) if val else "[]"

    def to_json_obj(val):
        return json.dumps(val) if val is not None else None

    v = data.vitals

    cursor.execute(
        """
        UPDATE patients SET
            name = %s,
            age = %s,
            gender = %s,
            chief_complaint = %s,
            symptoms = %s,
            temp = %s,
            bp = %s,
            pulse = %s,
            spo2 = %s,
            medical_history = %s,
            family_history = %s,
            allergies = %s,
            tentative_doctor_diagnosis = %s,
            initial_llm_diagnosis = %s,
            medications = %s,
            ration_card_type = %s,
            income_bracket = %s,
            occupation = %s,
            caste_category = %s,
            housing_type = %s,
            location = %s,
            scheme_eligibility = %s,
            procedures = %s
        WHERE id = %s
    """,
        (
            encrypt_text(data.name),
            encrypt_text(data.age),
            encrypt_text(data.gender),
            encrypt_text(data.chief_complaint),
            to_json(data.symptoms),
            encrypt_text(v.temperature if v else None),
            encrypt_text(v.blood_pressure if v else None),
            encrypt_text(v.pulse if v else None),
            encrypt_text(v.spo2 if v else None),
            to_json(data.medical_history),
            to_json(data.family_history),
            to_json(data.allergies),
            encrypt_text(data.tentative_doctor_diagnosis),
            encrypt_text(data.initial_llm_diagnosis),
            to_json(data.medications),
            encrypt_text(data.ration_card_type),
            encrypt_text(data.income),
            encrypt_text(data.occupation),
            encrypt_text(data.caste_category),
            encrypt_text(data.housing_type),
            encrypt_text(data.location),
            to_json_obj(data.scheme_eligibility),
            to_json(data.procedures),
            patient_id,
        ),
    )

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def delete_patient(patient_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM patients WHERE id = %s", (patient_id,))
    conn.commit()
    conn.close()


def get_all_patients():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM patients ORDER BY created_at DESC")
    rows = cursor.fetchall()

    # Convert Row objects to dicts and parse JSON strings
    patients = []
    for row in rows:
        p = dict(row)
        for enc_field in [
            "name",
            "age",
            "gender",
            "chief_complaint",
            "temp",
            "bp",
            "pulse",
            "spo2",
            "tentative_doctor_diagnosis",
            "initial_llm_diagnosis",
            "transcript_summary",
            "ration_card_type",
            "income_bracket",
            "occupation",
            "caste_category",
            "housing_type",
            "location",
        ]:
            if p.get(enc_field):
                try:
                    p[enc_field] = decrypt_text(p[enc_field])
                except Exception:
                    p[enc_field] = None
        for json_field in [
            "symptoms",
            "medical_history",
            "family_history",
            "allergies",
            "medications",
            "scheme_eligibility",
            "procedures",
            "icd10_codes",
            "procedure_codes",
            "billing_summary",
        ]:
            if p.get(json_field):
                try:
                    p[json_field] = json.loads(p[json_field])
                except Exception:
                    p[json_field] = (
                        []
                        if json_field not in ("scheme_eligibility", "billing_summary", "icd10_codes", "procedure_codes")
                        else None
                    )

        p["vitals"] = {
            "temperature": p.pop("temp", None),
            "blood_pressure": p.pop("bp", None),
            "pulse": p.pop("pulse", None),
            "spo2": p.pop("spo2", None),
        }
        p["income"] = p.pop("income_bracket", None)
        patients.append(p)

    conn.close()
    return patients


def get_patient_by_id(patient_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        p = dict(row)
        for enc_field in [
            "name",
            "age",
            "gender",
            "chief_complaint",
            "temp",
            "bp",
            "pulse",
            "spo2",
            "tentative_doctor_diagnosis",
            "initial_llm_diagnosis",
            "transcript_summary",
            "ration_card_type",
            "income_bracket",
            "occupation",
            "caste_category",
            "housing_type",
            "location",
        ]:
            if p.get(enc_field):
                try:
                    p[enc_field] = decrypt_text(p[enc_field])
                except Exception:
                    p[enc_field] = None
        for json_field in [
            "symptoms",
            "medical_history",
            "family_history",
            "allergies",
            "medications",
            "scheme_eligibility",
            "procedures",
            "icd10_codes",
            "procedure_codes",
            "billing_summary",
        ]:
            if p.get(json_field):
                try:
                    p[json_field] = json.loads(p[json_field])
                except Exception:
                    p[json_field] = (
                        []
                        if json_field not in ("scheme_eligibility", "billing_summary", "icd10_codes", "procedure_codes")
                        else None
                    )

        p["vitals"] = {
            "temperature": p.pop("temp", None),
            "blood_pressure": p.pop("bp", None),
            "pulse": p.pop("pulse", None),
            "spo2": p.pop("spo2", None),
        }
        p["income"] = p.pop("income_bracket", None)
        return p
    return None


def update_patient_billing(
    patient_id: int,
    icd10_codes: list,
    procedure_codes: list,
    billing_summary: dict,
) -> None:
    """Atomically save the auto-coded billing data for a patient."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE patients
        SET icd10_codes = %s, procedure_codes = %s, billing_summary = %s
        WHERE id = %s
        """,
        (
            json.dumps(icd10_codes),
            json.dumps(procedure_codes),
            json.dumps(billing_summary),
            patient_id,
        ),
    )
    conn.commit()
    conn.close()
    logger.info("Billing data saved for patient %d", patient_id)


def update_patient_summary(patient_id: int, summary: str):
    """Update only the transcript_summary for an existing patient."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE patients SET transcript_summary = %s WHERE id = %s", (encrypt_text(summary), patient_id))
    conn.commit()
    conn.close()
    logger.info("Updated summary for patient %d", patient_id)
