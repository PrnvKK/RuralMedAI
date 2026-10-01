export interface Vitals {
    temperature?: string;
    blood_pressure?: string;
    pulse?: string;
    spo2?: string;
}

export interface SchemeEligibilitySnapshot {
    pmjay: {
        eligible: boolean;
        reasons: string[];
        confidence: number;
    };
    state_scheme: {
        eligible: boolean;
        reasons: string[];
    };
}

export interface PatientData {
    id?: number;
    name?: string;
    age?: string;
    gender?: string;
    chief_complaint?: string;
    symptoms?: string[];
    vitals?: Vitals;
    medical_history?: string[];
    family_history?: string[];
    allergies?: string[];
    tentative_doctor_diagnosis?: string;
    initial_llm_diagnosis?: string;
    medications?: string[];
    procedures?: string[];
    // Eligibility Fields
    ration_card_type?: string;
    income?: string;
    // Legacy key kept only so old localStorage / DB snapshots do not break.
    income_bracket?: string;
    occupation?: string;
    caste_category?: string;
    housing_type?: string;
    location?: string;
    scheme_eligibility?: SchemeEligibilitySnapshot;
    // Billing & ICD Coding (auto-populated after EHR commit)
    icd10_codes?: ICDCodeEntry[];
    procedure_codes?: ICDCodeEntry[];
    billing_summary?: BillingSummary;
}

export interface ICDCodeEntry {
    code: string;
    description: string;
    confidence: number;
    source: 'semantic' | 'entity' | 'tfidf' | 'exact';
}

export interface BillingSummary {
    patient_id: number;
    encounter_date: string;
    principal_diagnosis_code: string;
    principal_diagnosis_description: string;
    diagnosis_codes: ICDCodeEntry[];
    procedure_codes: ICDCodeEntry[];
    billing_notes: string;
    coding_status: 'auto_coded' | 'confirmed' | 'partial';
}


export interface TranscriptItem {
    id: string;
    type: 'text' | 'tool';
    content?: string;
    timestamp: string;
    toolInfo?: {
        field: string;
        value: unknown;
    };
}

export interface ScribeSessionSnapshot {
    patientData: PatientData;
    transcript: TranscriptItem[];
    activePatientId?: number | null;
    entryMode?: 'create' | 'update';
    updatedAt: string;
}

// Messages streamed by the backend over the /ws/live-consultation socket.
export interface LiveScribeMessage {
    type: 'update' | 'content' | 'session_complete';
    field?: string;
    value?: unknown;
    text?: string;
}
