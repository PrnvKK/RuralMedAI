import asyncio
import json
import logging
import os

from app.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

GEMINI_EXTRACT_MODEL = os.getenv("GEMINI_EXTRACT_MODEL", "gemini-3.5-flash")
GEMINI_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"))
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")
SUMMARY_MAX_TOKENS = int(os.getenv("GEMINI_SUMMARY_MAX_TOKENS", "4096"))


def _post_chat(prompt: str) -> str:
    client = GeminiClient(timeout_seconds=GEMINI_TIMEOUT_SECONDS)
    return client.generate_text(
        prompt=prompt,
        model=GEMINI_EXTRACT_MODEL,
        temperature=0,
        max_output_tokens=SUMMARY_MAX_TOKENS,
        thinking_level=GEMINI_THINKING_LEVEL,
    )


async def generate_consultation_summary_async(transcript_history: list[str]) -> str:
    """Generate a concise consultation summary with Gemini."""
    if not transcript_history:
        return ""

    transcript_text = "\n".join(transcript_history[-120:])
    prompt = f"""
You are Parchee Edge, a clinical documentation assistant.
Summarize the doctor-patient transcript into concise important points.

Rules:
- Return Markdown bullets only.
- Focus on clinical facts, symptoms, vitals, diagnosis stated by the doctor, procedures, medication,
plan, and follow-up.
- Include welfare or claim-readiness facts if present, such as ration card, income, occupation,
caste category, housing, and location.
- Do not provide autonomous medical advice.

TRANSCRIPT:
{transcript_text}
""".strip()

    try:
        return await asyncio.to_thread(_post_chat, prompt)
    except Exception as exc:
        logger.warning("Gemini summary generation failed: %s", str(exc)[:160])
        return "Error generating summary."


async def generate_clinical_note_async(patient_payload: dict) -> str:
    """Draft a clinician-reviewable note with Gemini."""
    prompt = f"""
You are Parchee Edge, a clinical documentation assistant.
Draft a concise clinical note from this structured encounter.

Rules:
- Return plain text only.
- Use clear sections: Patient, Chief Complaint, Vitals, Symptoms, History, Assessment,
Medications, Procedures, Claim Readiness.
- Do not invent missing facts.
- Label assessment as clinician-review documentation support, not autonomous medical advice.

STRUCTURED ENCOUNTER JSON:
{json.dumps(patient_payload, ensure_ascii=False)}
""".strip()

    try:
        return await asyncio.to_thread(_post_chat, prompt)
    except Exception as exc:
        logger.warning("Gemini clinical note generation failed: %s", str(exc)[:160])
        return ""
