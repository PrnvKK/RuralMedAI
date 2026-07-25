import asyncio
import json
import os
import urllib.error
import urllib.request

LLAMA_CPP_BASE_URL = os.getenv("LLAMA_CPP_BASE_URL", "http://localhost:8085").rstrip("/")
LLAMA_CPP_MODEL_NAME = os.getenv("LLAMA_CPP_MODEL_NAME", "gemma-4")
LLAMA_CPP_TIMEOUT_SECONDS = int(os.getenv("LLAMA_CPP_TIMEOUT_SECONDS", "180"))
SUMMARY_MAX_TOKENS = int(os.getenv("LLAMA_CPP_SUMMARY_MAX_TOKENS", "384"))


def _post_chat(prompt: str) -> str:
    payload = {
        "model": LLAMA_CPP_MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "reasoning_format": "none",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        f"{LLAMA_CPP_BASE_URL}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=LLAMA_CPP_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


async def generate_consultation_summary_async(transcript_history: list[str]) -> str:
    """Generate a concise consultation summary with local Gemma 4 through llama.cpp."""
    if not transcript_history:
        return ""

    transcript_text = "\n".join(transcript_history[-120:])
    prompt = f"""
You are Parchee Edge, an offline clinical documentation assistant running locally.
Summarize the doctor-patient transcript into concise important points.

Rules:
- Return Markdown bullets only.
- Focus on clinical facts, symptoms, vitals, diagnosis stated by the doctor, procedures, medication, plan, and follow-up.
- Include welfare or claim-readiness facts if present, such as ration card, income, occupation, caste category, housing, and location.
- Do not provide autonomous medical advice.
- Do not output chain-of-thought or <think> blocks.

TRANSCRIPT:
{transcript_text}
""".strip()

    try:
        return await asyncio.to_thread(_post_chat, prompt)
    except (urllib.error.URLError, KeyError, IndexError, TimeoutError, Exception) as exc:
        print(f"Gemma 4 summary generation failed: {str(exc)[:160]}")
        return "Error generating summary."


async def generate_clinical_note_async(patient_payload: dict) -> str:
    """Draft a clinician-reviewable note with local Gemma 4 through llama.cpp."""
    prompt = f"""
You are Parchee Edge, an offline clinical documentation assistant running locally.
Draft a concise clinical note from this structured encounter.

Rules:
- Return plain text only.
- Use clear sections: Patient, Chief Complaint, Vitals, Symptoms, History, Assessment, Medications, Procedures, Claim Readiness.
- Do not invent missing facts.
- Label assessment as clinician-review documentation support, not autonomous medical advice.
- Do not output chain-of-thought or <think> blocks.

STRUCTURED ENCOUNTER JSON:
{json.dumps(patient_payload, ensure_ascii=False)}
""".strip()

    try:
        return await asyncio.to_thread(_post_chat, prompt)
    except (urllib.error.URLError, KeyError, IndexError, TimeoutError, Exception) as exc:
        print(f"Gemma 4 clinical note generation failed: {str(exc)[:160]}")
        return ""
