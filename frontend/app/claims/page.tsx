"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, CircleX, RefreshCw } from 'lucide-react';
import { PatientData, TranscriptItem } from '@/types';
import { loadScribeSession } from '@/lib/sessionStore';
import {
    buildEligibilityWorkspace,
    FieldMatchStatus,
    getFieldMatchStatus,
    SchemeEvaluation,
} from '@/lib/claimsEngine';
import { API } from '@/lib/api';

// EHR rows returned by the patients endpoint: PatientData plus the DB
// bookkeeping fields that are not part of the clinical payload.
interface ArchivedPatient extends PatientData {
    created_at?: string;
}

function cx(...values: Array<string | false | null | undefined>) {
    return values.filter(Boolean).join(' ');
}

export default function ClaimsPage() {
    const [patientData, setPatientData] = useState<PatientData>({});
    const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
    const [lastSyncedAt, setLastSyncedAt] = useState<string>('Not synced');
    const [selectedSchemeId, setSelectedSchemeId] = useState<string | null>(null);
    const [archivedPatients, setArchivedPatients] = useState<ArchivedPatient[]>([]);
    const [selectedPatientKey, setSelectedPatientKey] = useState<string>('live');
    const [documentChecks, setDocumentChecks] = useState<Record<string, Record<string, boolean>>>({});

    const lastSessionUpdateRef = useRef<string | null>(null);
    const selectedPatientKeyRef = useRef<string>('live');

    const hydrateFromSession = useCallback(() => {
        if (selectedPatientKeyRef.current !== 'live') return;
        const snapshot = loadScribeSession();
        if (!snapshot) return;
        if (snapshot.updatedAt === lastSessionUpdateRef.current) return;

        lastSessionUpdateRef.current = snapshot.updatedAt;
        setPatientData(snapshot.patientData || {});
        setTranscript(snapshot.transcript || []);

        const parsedDate = new Date(snapshot.updatedAt);
        setLastSyncedAt(
            Number.isNaN(parsedDate.getTime())
                ? 'Not synced'
                : parsedDate.toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false,
                })
        );
    }, []);

    const loadArchivedPatients = useCallback(async () => {
        try {
            const response = await fetch(`${API.EHR}/patients`);
            const data = await response.json();
            if (Array.isArray(data)) setArchivedPatients(data);
        } catch (error) {
            console.error('Failed to fetch archived patients:', error);
        }
    }, []);

    useEffect(() => {
        selectedPatientKeyRef.current = selectedPatientKey;
    }, [selectedPatientKey]);

    useEffect(() => {
        // Defer the initial hydrate so the effect body does not call setState
        // synchronously (react-hooks/set-state-in-effect); the interval and the
        // storage listener below keep the form fresh afterwards.
        const initial = window.setTimeout(() => {
            hydrateFromSession();
            loadArchivedPatients();
        }, 0);

        const interval = window.setInterval(hydrateFromSession, 2500);
        const onStorage = (event: StorageEvent) => {
            if (event.key === 'ruralmedai:sessions:live-scribe') {
                hydrateFromSession();
            }
        };

        window.addEventListener('storage', onStorage);

        return () => {
            window.clearTimeout(initial);
            window.clearInterval(interval);
            window.removeEventListener('storage', onStorage);
        };
    }, [hydrateFromSession, loadArchivedPatients]);

    useEffect(() => {
        if (selectedPatientKey === 'live') {
            // Deferred so the effect body does not call setState synchronously.
            const initial = window.setTimeout(hydrateFromSession, 0);
            return () => window.clearTimeout(initial);
        }
    }, [selectedPatientKey, hydrateFromSession]);

    // Resolve the selected archived record during render so switching between
    // the live session and an EHR archive does not need an effect that syncs
    // state (react-hooks/set-state-in-effect).
    const selectedArchivedRecord = useMemo(() => {
        if (selectedPatientKey === 'live') return null;
        const patientId = Number.parseInt(selectedPatientKey.replace('ehr-', ''), 10);
        if (Number.isNaN(patientId)) return null;
        return archivedPatients.find((item) => item.id === patientId) ?? null;
    }, [selectedPatientKey, archivedPatients]);

    const mappedArchivedPatient = useMemo<PatientData | null>(() => {
        if (!selectedArchivedRecord) return null;
        const record = selectedArchivedRecord;
        return {
            id: record.id,
            name: record.name,
            age: record.age,
            gender: record.gender,
            chief_complaint: record.chief_complaint,
            symptoms: Array.isArray(record.symptoms) ? record.symptoms : [],
            medical_history: Array.isArray(record.medical_history) ? record.medical_history : [],
            family_history: Array.isArray(record.family_history) ? record.family_history : [],
            allergies: Array.isArray(record.allergies) ? record.allergies : [],
            medications: Array.isArray(record.medications) ? record.medications : [],
            tentative_doctor_diagnosis: record.tentative_doctor_diagnosis,
            initial_llm_diagnosis: record.initial_llm_diagnosis,
            ration_card_type: record.ration_card_type,
            income: record.income || record.income_bracket,
            occupation: record.occupation,
            caste_category: record.caste_category,
            housing_type: record.housing_type,
            location: record.location,
            scheme_eligibility: record.scheme_eligibility,
            vitals: {
                blood_pressure: record.vitals?.blood_pressure,
                pulse: record.vitals?.pulse,
                temperature: record.vitals?.temperature,
                spo2: record.vitals?.spo2,
            },
        };
    }, [selectedArchivedRecord]);

    const effectivePatientData = mappedArchivedPatient ?? patientData;
    const effectiveTranscript = useMemo(
        () => (mappedArchivedPatient ? [] : transcript),
        [mappedArchivedPatient, transcript]
    );
    const effectiveLastSyncedAt = selectedArchivedRecord
        ? selectedArchivedRecord.created_at
            ? new Date(selectedArchivedRecord.created_at).toLocaleString()
            : `Archive ID ${selectedArchivedRecord.id}`
        : lastSyncedAt;

    const workspace = useMemo(
        () => buildEligibilityWorkspace(effectivePatientData, effectiveTranscript),
        [effectivePatientData, effectiveTranscript]
    );

    // Resolve the effective selection during render instead of syncing state in
    // an effect: fall back to the first scheme when the user's choice is no
    // longer present (or when no schemes exist yet).
    const effectiveSelectedSchemeId =
        workspace.schemes.length === 0
            ? null
            : workspace.schemes.some((scheme) => scheme.id === selectedSchemeId)
                ? selectedSchemeId
                : workspace.schemes[0].id;

    const selectedScheme = useMemo(
        () => workspace.schemes.find((scheme) => scheme.id === effectiveSelectedSchemeId) || null,
        [workspace.schemes, effectiveSelectedSchemeId]
    );

    const handleRefresh = async () => {
        await loadArchivedPatients();
        hydrateFromSession();
    };

    const getCheckedDocumentsCount = useCallback(
        (scheme: SchemeEvaluation) =>
            scheme.requiredDocuments.filter((document) => Boolean(documentChecks[scheme.id]?.[document.id])).length,
        [documentChecks]
    );

    const toggleDocumentCheck = useCallback((schemeId: string, documentId: string) => {
        setDocumentChecks((prev) => ({
            ...prev,
            [schemeId]: {
                ...(prev[schemeId] || {}),
                [documentId]: !prev[schemeId]?.[documentId],
            },
        }));
    }, []);

    return (
        <main className="h-screen bg-background text-foreground overflow-hidden flex flex-col">
            <div className="flex-none bg-white/50 backdrop-blur-xl border-b border-border px-6 py-4 flex items-center justify-between gap-4">
                <div className="flex items-center gap-6">
                    <h1 className="text-sm font-bold tracking-[0.1em] text-foreground uppercase">Insurance <span className="text-primary/20">/</span> Eligibility</h1>
                    <div className="flex items-center gap-3 px-4 py-2 bg-primary/5 rounded-2xl border border-primary/10">
                        <span className="text-[9px] font-bold text-primary/60 uppercase tracking-widest text-nowrap">Patient Selector</span>
                        <select
                            value={selectedPatientKey}
                            onChange={(e) => {
                                setSelectedPatientKey(e.target.value);
                                setDocumentChecks({});
                            }}
                            className="bg-transparent text-[11px] font-bold text-foreground focus:outline-none cursor-pointer"
                        >
                            <option value="live">Live Session</option>
                            {archivedPatients.map((patient) => (
                                <option key={patient.id} value={`ehr-${patient.id}`}>
                                    {patient.name || 'Unnamed'} • ID {patient.id}
                                </option>
                            ))}
                        </select>
                    </div>
                </div>

                <div className="flex items-center gap-4">
                    <div className="text-[9px] font-mono font-bold text-primary/40 uppercase tracking-widest bg-primary/5 px-3 py-1.5 rounded-xl border border-primary/10">
                        Sync: {effectiveLastSyncedAt}
                    </div>
                    <button
                        onClick={handleRefresh}
                        className="p-1 px-4 py-2 flex items-center gap-2 rounded-xl bg-primary text-white hover:bg-primary/90 transition-all text-[10px] font-bold uppercase tracking-widest shadow-lg shadow-primary/10"
                    >
                        <RefreshCw className="w-3.5 h-3.5" /> Refresh
                    </button>
                </div>
            </div>

            <div className="flex-1 p-3 h-full flex flex-col max-w-[1800px] mx-auto overflow-hidden w-full">
                <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-12 gap-3 overflow-hidden">
                    <section className="xl:col-span-3 min-h-0 border border-border rounded-[2rem] bg-white shadow-sm p-4 flex flex-col overflow-hidden">
                        <div className="mb-3">
                            <h2 className="text-sm font-bold text-foreground uppercase tracking-[0.1em]">Patient biodata</h2>
                            <p className="text-[10px] text-primary/60 font-medium uppercase tracking-wider mt-1">Fields sync with selected scheme</p>
                        </div>

                        <div className="space-y-2 overflow-y-auto pr-2 custom-scrollbar flex-1">
                            {workspace.patientFields.map((field) => {
                                const status = getFieldMatchStatus(field.key, selectedScheme);
                                return <BiodataRow key={field.key} label={field.label} value={field.value} status={status} />;
                            })}
                        </div>
                    </section>

                    <section className="xl:col-span-3 min-h-0 border border-border rounded-[2rem] bg-white shadow-sm p-4 flex flex-col overflow-hidden">
                        <div className="mb-3">
                            <h2 className="text-sm font-bold text-foreground uppercase tracking-[0.1em]">Schemes list</h2>
                            <p className="text-[10px] text-primary/60 font-medium uppercase tracking-wider mt-1">Select a scheme to inspect</p>
                        </div>

                        <div className="space-y-2 overflow-y-auto pr-2 custom-scrollbar flex-1">
                            {workspace.schemes.map((scheme) => (
                                <SchemeCard
                                    key={scheme.id}
                                    scheme={scheme}
                                    checkedDocumentCount={getCheckedDocumentsCount(scheme)}
                                    selected={scheme.id === effectiveSelectedSchemeId}
                                    onSelect={() => setSelectedSchemeId(scheme.id)}
                                />
                            ))}
                        </div>
                    </section>

                    <section className="xl:col-span-6 min-h-0 border border-border rounded-[2rem] bg-white shadow-sm p-4 flex flex-col overflow-hidden">
                        {!selectedScheme ? (
                            <div className="h-full flex items-center justify-center text-primary/40 text-[11px] font-bold uppercase tracking-widest font-mono">No scheme selected</div>
                        ) : (
                            <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar">
                                <SchemeDetails
                                    scheme={selectedScheme}
                                    checkedDocuments={documentChecks[selectedScheme.id] || {}}
                                    onToggleDocument={toggleDocumentCheck}
                                />
                            </div>
                        )}
                    </section>
                </div>
            </div>
        </main>
    );
}

function BiodataRow({ label, value, status }: { label: string; value: string; status: FieldMatchStatus }) {
    return (
        <div
            className={cx(
                'rounded-2xl border p-3 space-y-1 transition-all duration-300',
                status === 'match' && 'border-emerald-500/40 bg-emerald-500/10',
                status === 'mismatch' && 'border-rose-500/40 bg-rose-500/10',
                status === 'neutral' && 'border-border bg-background hover:border-emerald-400/30'
            )}
        >
            <div className="flex items-center justify-between gap-2">
                <p className="text-[9px] font-bold uppercase tracking-[0.2em] text-primary/60">{label}</p>
                {status === 'match' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />}
                {status === 'mismatch' && <CircleX className="w-3.5 h-3.5 text-rose-400" />}
            </div>
            <p className="text-[13px] text-foreground font-bold tracking-tight break-words">{value}</p>
            {status === 'mismatch' && (
                <p className="text-[9px] font-bold text-rose-500/80 uppercase tracking-widest mt-1">Not met</p>
            )}
        </div>
    );
}

function SchemeCard({
    scheme,
    checkedDocumentCount,
    selected,
    onSelect,
}: {
    scheme: SchemeEvaluation;
    checkedDocumentCount: number;
    selected: boolean;
    onSelect: () => void;
}) {
    const isEligible = scheme.eligibilityBand === 'eligible';
    const isLikelyNotEligible = scheme.eligibilityBand === 'likely_not_eligible';

    return (
        <button
            onClick={onSelect}
            className={cx(
                'w-full text-left rounded-2xl border p-3 transition-all duration-300 relative group',
                selected ? 'border-emerald-500/40 bg-emerald-500/10 shadow-md' : 'border-border bg-background hover:bg-emerald-500/5 hover:border-emerald-500/30'
            )}
        >
            <div className="flex items-start justify-between gap-3">
                <p className="text-[13px] font-bold text-foreground leading-tight tracking-tight">{scheme.name}</p>
                <div
                    className={cx(
                        'text-[8px] px-2 py-1 rounded-full border font-bold uppercase tracking-widest shrink-0',
                        isEligible
                            ? 'bg-emerald-500/10 text-emerald-700 border-emerald-500/40'
                            : isLikelyNotEligible
                                ? 'bg-amber-50 text-amber-600 border-amber-200'
                                : 'bg-rose-50 text-rose-700 border-rose-500/30'
                    )}
                >
                    {isEligible ? 'Eligible' : isLikelyNotEligible ? 'Possible' : 'Not Eligible'}
                </div>
            </div>
            <p className="text-[11px] text-primary/60 font-medium mt-2 leading-relaxed">{scheme.description}</p>
            <div className="mt-3 flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="flex flex-col">
                        <span className="text-[8px] font-bold text-primary/40 uppercase tracking-widest">Criteria</span>
                        <span className="text-[10px] font-bold text-foreground">{scheme.metCriteriaCount}/{scheme.totalCriteriaCount}</span>
                    </div>
                </div>
                <div className="h-8 w-px bg-border mx-2" />
                <div className="flex-1 flex flex-col">
                    <span className="text-[8px] font-bold text-primary/40 uppercase tracking-widest">Docs Verified</span>
                    <span className="text-[10px] font-bold text-foreground">{checkedDocumentCount}/{scheme.totalDocumentCount}</span>
                </div>
            </div>
        </button>
    );
}

function SchemeDetails({
    scheme,
    checkedDocuments,
    onToggleDocument,
}: {
    scheme: SchemeEvaluation;
    checkedDocuments: Record<string, boolean>;
    onToggleDocument: (schemeId: string, documentId: string) => void;
}) {
    const metCriteria = scheme.criteria.filter((criterion) => criterion.met);
    const unmetCriteria = scheme.criteria.filter((criterion) => !criterion.met);
    const checkedCount = scheme.requiredDocuments.filter((document) => Boolean(checkedDocuments[document.id])).length;
    const allDocumentsChecked = scheme.requiredDocuments.length > 0 && checkedCount === scheme.requiredDocuments.length;
    const isEligible = scheme.eligibilityBand === 'eligible';
    const isLikelyNotEligible = scheme.eligibilityBand === 'likely_not_eligible';

    return (
        <div className="space-y-4">
            <div className="pb-4 border-b border-border">
                <div className="flex items-center justify-between gap-4 flex-wrap mb-4">
                    <h2 className="text-xl font-bold text-foreground tracking-tight">{scheme.name}</h2>
                    <div
                        className={cx(
                            'text-[10px] px-3 py-1.5 rounded-full border font-bold uppercase tracking-[0.15em] shadow-sm',
                            isEligible
                                ? 'bg-emerald-600 text-white border-emerald-600'
                                : isLikelyNotEligible
                                    ? 'bg-amber-100 text-amber-700 border-amber-200'
                                    : 'bg-rose-100 text-rose-700 border-rose-200'
                        )}
                    >
                        {isEligible
                            ? '✓ Eligible'
                            : isLikelyNotEligible
                                ? '⚡ Possibly Eligible'
                                : '✕ Not Eligible'}
                    </div>
                </div>
                <p className="text-[13px] text-primary/60 font-medium leading-relaxed">{scheme.description}</p>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
                    <div className="p-3 bg-amber-50/50 border border-amber-200/50 rounded-2xl flex items-start gap-3">
                        <div className="w-8 h-8 rounded-xl bg-amber-100 flex items-center justify-center shrink-0">
                            <CheckCircle2 className="w-4 h-4 text-amber-600" />
                        </div>
                        <div>
                            <p className="text-[9px] font-bold text-amber-700 uppercase tracking-widest mb-1">Signal Verification</p>
                            <p className="text-[11px] text-amber-800/80 font-medium leading-relaxed">Markers are preliminary. Scheme criteria needs manual verification.</p>
                        </div>
                    </div>
                    <div className="p-3 bg-rose-50/40 border border-rose-200/60 rounded-2xl flex items-start gap-3">
                        <div className="w-8 h-8 rounded-xl bg-rose-100 flex items-center justify-center shrink-0">
                            <span className="text-lg text-rose-600">⚠️</span>
                        </div>
                        <div>
                            <p className="text-[9px] font-bold text-rose-700 uppercase tracking-widest mb-1">Critical Check</p>
                            <p className="text-[11px] text-rose-700/80 font-medium leading-relaxed">Ensure Ration Card type is explicitly correct (BPL/AAY/etc.)</p>
                        </div>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="rounded-[2rem] border border-emerald-500/30 bg-emerald-500/10 p-4">
                    <h3 className="text-[10px] font-bold text-emerald-700 uppercase tracking-[0.2em] mb-3">Criteria met</h3>
                    {metCriteria.length === 0 ? (
                        <p className="text-[11px] text-emerald-700/60 italic font-medium">No criteria met yet.</p>
                    ) : (
                        <ul className="space-y-2">
                            {metCriteria.map((criterion) => (
                                <li key={criterion.id} className="text-[12px] text-foreground">
                                    <div className="flex items-start gap-3 bg-white/60 p-2 rounded-xl border border-emerald-500/20">
                                        <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                                        <div>
                                            <p className="font-bold text-[13px]">{criterion.label}</p>
                                            <p className="text-emerald-700/70 text-[11px] font-medium mt-0.5">{criterion.description}</p>
                                        </div>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>

                <div className="rounded-[2rem] border border-rose-500/20 bg-rose-50/50 p-4">
                    <h3 className="text-[10px] font-bold text-rose-700 uppercase tracking-[0.2em] mb-3">Criteria not met</h3>
                    {unmetCriteria.length === 0 ? (
                        <p className="text-[11px] text-emerald-600 font-bold">✓ All criteria are met.</p>
                    ) : (
                        <ul className="space-y-2">
                            {unmetCriteria.map((criterion) => (
                                <li key={criterion.id} className="text-[12px]">
                                    <div className="flex items-start gap-3 bg-white border border-rose-200 p-2 rounded-xl">
                                        <CircleX className="w-4 h-4 text-rose-400 shrink-0" />
                                        <div>
                                            <p className="font-bold text-[13px] text-rose-900">{criterion.label}</p>
                                            <p className="text-rose-600/60 text-[11px] font-medium mt-0.5">{criterion.description}</p>
                                        </div>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>

            <div className={cx(
                'rounded-[2rem] border p-4',
                allDocumentsChecked ? 'border-emerald-500/30 bg-emerald-500/10' : 'border-rose-500/20 bg-rose-50/20'
            )}>
                <div className="flex items-center justify-between gap-2 mb-3">
                    <h3 className="text-[10px] font-bold text-foreground uppercase tracking-[0.2em]">Required documents</h3>
                    <div className={cx(
                        'px-3 py-1 rounded-full text-[9px] font-bold uppercase tracking-widest border',
                        allDocumentsChecked ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-rose-100 text-rose-700 border-rose-200'
                    )}>
                        {checkedCount}/{scheme.requiredDocuments.length} Verified
                    </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    {scheme.requiredDocuments.map((document) => (
                        <button
                            key={document.id}
                            onClick={() => onToggleDocument(scheme.id, document.id)}
                            className={cx(
                                'w-full rounded-2xl border p-3 text-left transition-all duration-300 group',
                                checkedDocuments[document.id]
                                    ? 'border-emerald-500/40 bg-emerald-500/10 shadow-sm'
                                    : 'border-rose-200 bg-white hover:bg-rose-50/60 hover:border-rose-300'
                            )}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <p className="text-[13px] font-bold text-foreground">{document.name}</p>
                                <div className={cx(
                                    'w-5 h-5 rounded-full flex items-center justify-center transition-all',
                                    checkedDocuments[document.id] ? 'bg-emerald-600 text-white' : 'bg-rose-50 border border-rose-200 text-rose-300'
                                )}>
                                    <CheckCircle2 className="w-3.5 h-3.5" />
                                </div>
                            </div>
                            <p className="text-[11px] mt-1.5 text-primary/60 font-medium leading-relaxed italic">{document.evidence}</p>
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
}
