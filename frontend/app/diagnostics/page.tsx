"use client";

import { useEffect, useState, useCallback, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Search, Receipt, Stethoscope, Activity,
    CheckCircle2, Clock, AlertCircle, ArrowLeft, ChevronDown, ChevronUp,
    FileText, RefreshCw, User
} from 'lucide-react';
import Link from 'next/link';
import { API } from '@/lib/api';

const API_BASE = API.EHR;

// ─── Types ────────────────────────────────────────────────────────────────────

interface CodeEntry {
    code: string;
    description: string;
    confidence: number;
    source: string;
}

interface BillingSummary {
    patient_id: number;
    encounter_date: string;
    principal_diagnosis_code: string;
    principal_diagnosis_description: string;
    diagnosis_codes: CodeEntry[];
    procedure_codes: CodeEntry[];
    billing_notes: string;
    coding_status: 'auto_coded' | 'confirmed' | 'partial';
}

interface Patient {
    id: number;
    name?: string;
    age?: string;
    gender?: string;
    created_at?: string;
    icd10_codes?: CodeEntry[];
    procedure_codes?: CodeEntry[];
    billing_summary?: BillingSummary;
}

interface TrendItem {
    label: string;
    count: number;
}

interface Trends {
    top_diagnoses: TrendItem[];
    top_procedures: TrendItem[];
    top_symptoms: TrendItem[];
    total_patients: number;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function confidenceColor(c: number): string {
    if (c >= 0.85) return 'text-emerald-700 bg-emerald-50 border-emerald-200';
    if (c >= 0.65) return 'text-amber-700 bg-amber-50 border-amber-200';
    return 'text-slate-500 bg-slate-50 border-slate-200';
}

function statusBadge(status?: string): React.ReactNode {
    if (!status) return null;
    const map: Record<string, string> = {
        confirmed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        auto_coded: 'bg-blue-50 text-blue-600 border-blue-200',
        partial: 'bg-amber-50 text-amber-600 border-amber-200',
    };
    const icons: Record<string, React.ReactElement> = {
        confirmed: <CheckCircle2 className="w-3 h-3" />,
        auto_coded: <Clock className="w-3 h-3" />,
        partial: <AlertCircle className="w-3 h-3" />,
    };
    return (
        <span className={`inline-flex items-center gap-1 text-[9px] font-bold px-2 py-0.5 rounded-full border ${map[status] ?? map.partial}`}>
            {icons[status]} {status.replace('_', ' ')}
        </span>
    );
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function CodeRow({ entry, variant }: { entry: CodeEntry; variant: 'dx' | 'px' }) {
    const isDx = variant === 'dx';
    return (
        <div className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-[11px] ${isDx ? 'bg-blue-50 border-blue-100' : 'bg-violet-50 border-violet-100'}`}>
            <span className={`font-bold font-mono shrink-0 ${isDx ? 'text-blue-700' : 'text-violet-700'}`}>{entry.code}</span>
            <span className="text-slate-600 flex-1 min-w-0 truncate">{entry.description}</span>
            <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border shrink-0 ${confidenceColor(entry.confidence)}`}>
                {Math.round(entry.confidence * 100)}%
            </span>
        </div>
    );
}

function TrendBar({ label, count, max }: { label: string; count: number; max: number }) {
    const pct = max > 0 ? (count / max) * 100 : 0;
    return (
        <div className="space-y-1">
            <div className="flex justify-between items-center text-[10px]">
                <span className="text-slate-600 font-mono truncate max-w-[80%]">{label}</span>
                <span className="text-slate-400 font-bold shrink-0">{count}</span>
            </div>
            <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                <motion.div
                    className="h-full bg-blue-500 rounded-full"
                    initial={{ width: 0 }}
                    animate={{ width: `${pct}%` }}
                    transition={{ duration: 0.6, ease: 'easeOut' }}
                />
            </div>
        </div>
    );
}

// ─── Main page (inner — needs search params) ───────────────────────────────────

function DiagnosticsInner() {
    const searchParams = useSearchParams();
    const initialPatientId = searchParams.get('patient_id');

    // Tabs
    const [activeTab, setActiveTab] = useState<'browser' | 'audit' | 'trends'>('audit');

    // Code browser state
    const [searchQuery, setSearchQuery] = useState('');
    const [codeType, setCodeType] = useState<'diagnosis' | 'procedure'>('diagnosis');
    const [searchResults, setSearchResults] = useState<CodeEntry[]>([]);
    const [searchLoading, setSearchLoading] = useState(false);

    // Audit state
    const [patients, setPatients] = useState<Patient[]>([]);
    const [patientsLoading, setPatientsLoading] = useState(true);
    const [expandedPatient, setExpandedPatient] = useState<number | null>(
        initialPatientId ? parseInt(initialPatientId) : null
    );
    const [confirmingId, setConfirmingId] = useState<number | null>(null);

    // Trends state
    const [trends, setTrends] = useState<Trends | null>(null);
    const [trendsLoading, setTrendsLoading] = useState(false);

    // ── Fetch patients for audit ──
    useEffect(() => {
        fetch(`${API_BASE}/patients`)
            .then(r => r.json())
            .then((data: Patient[]) => {
                // Sort newest first
                setPatients(data.sort((a, b) => (b.id ?? 0) - (a.id ?? 0)));
            })
            .catch(console.error)
            .finally(() => setPatientsLoading(false));
    }, []);

    // ── Auto-jump to requested patient ──
    useEffect(() => {
        if (initialPatientId) {
            setActiveTab('audit');
            setExpandedPatient(parseInt(initialPatientId));
        }
    }, [initialPatientId]);

    // ── Code browser search (debounced) ──
    const doSearch = useCallback(async (q: string, type: string) => {
        if (!q.trim()) { setSearchResults([]); return; }
        setSearchLoading(true);
        try {
            const res = await fetch(`${API_BASE}/code-search`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: q, code_type: type, top_k: 12 }),
            });
            const data = await res.json();
            setSearchResults(data.results ?? []);
        } catch (e) {
            console.error(e);
        } finally {
            setSearchLoading(false);
        }
    }, []);

    useEffect(() => {
        const t = setTimeout(() => doSearch(searchQuery, codeType), 320);
        return () => clearTimeout(t);
    }, [searchQuery, codeType, doSearch]);

    // ── Load trends ──
    useEffect(() => {
        if (activeTab !== 'trends' || trends) return; // not visible or already loaded
        setTrendsLoading(true);
        fetch(`${API_BASE}/analytics/trends`)
            .then(r => r.json())
            .then(setTrends)
            .catch(console.error)
            .finally(() => setTrendsLoading(false));
    }, [activeTab, trends]);

    // ── Confirm billing for a patient ──
    const confirmBilling = async (patientId: number) => {
        setConfirmingId(patientId);
        try {
            await fetch(`${API_BASE}/patients/${patientId}/billing`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}), // empty body = just set status to confirmed
            });
            setPatients(prev => prev.map(p => {
                if (p.id !== patientId) return p;
                return {
                    ...p,
                    billing_summary: p.billing_summary
                        ? { ...p.billing_summary, coding_status: 'confirmed' }
                        : undefined,
                };
            }));
        } catch (e) {
            console.error(e);
        } finally {
            setConfirmingId(null);
        }
    };

    const codedCount = patients.filter(p => (p.icd10_codes?.length ?? 0) > 0).length;
    const confirmedCount = patients.filter(p => p.billing_summary?.coding_status === 'confirmed').length;

    return (
        <main className="h-screen flex flex-col bg-[#f8fafc] text-foreground overflow-hidden">
            {/* Header */}
            <div className="flex-none bg-white border-b border-slate-200 px-4 py-2 flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                    <Link href="/patients" className="text-slate-400 hover:text-slate-700 transition-colors">
                        <ArrowLeft className="w-4 h-4" />
                    </Link>
                    <div>
                        <h1 className="text-sm font-bold tracking-tight text-slate-900 uppercase">
                            Billing <span className="text-slate-400">/</span> Coding Center
                        </h1>
                        <p className="text-[9px] text-slate-400 font-mono uppercase tracking-wider">
                            ICD-10-CM · ICD-10-PCS · Offline NLP · Insurance Ready
                        </p>
                    </div>
                </div>

                {/* Stat pills */}
                <div className="flex items-center gap-2">
                    <span className="text-[10px] font-mono text-slate-500 bg-slate-100 border border-slate-200 px-2 py-1 rounded-lg">
                        {patients.length} patients
                    </span>
                    <span className="text-[10px] font-mono text-blue-600 bg-blue-50 border border-blue-200 px-2 py-1 rounded-lg">
                        {codedCount} coded
                    </span>
                    <span className="text-[10px] font-mono text-emerald-600 bg-emerald-50 border border-emerald-200 px-2 py-1 rounded-lg">
                        {confirmedCount} confirmed
                    </span>
                </div>
            </div>

            {/* Tab bar */}
            <div className="flex-none bg-white border-b border-slate-200 px-4 flex items-center gap-1">
                {(['audit', 'browser', 'trends'] as const).map(tab => (
                    <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        className={`px-3 py-2 text-[10px] font-bold uppercase tracking-widest border-b-2 transition-all ${activeTab === tab
                            ? 'border-slate-800 text-slate-800'
                            : 'border-transparent text-slate-400 hover:text-slate-600'
                            }`}
                    >
                        {tab === 'audit' && '📋 Encounter Audit'}
                        {tab === 'browser' && '🔍 Code Browser'}
                        {tab === 'trends' && '📊 Clinical Trends'}
                    </button>
                ))}
            </div>

            <div className="flex-1 overflow-auto">
                {/* ── Encounter Audit Tab ─────────────────────────────────────────────── */}
                {activeTab === 'audit' && (
                    <div className="p-4 space-y-3 max-w-5xl mx-auto">
                        {patientsLoading ? (
                            <div className="flex items-center justify-center h-64">
                                <div className="w-5 h-5 border-2 border-slate-300 border-t-slate-700 rounded-full animate-spin" />
                            </div>
                        ) : patients.length === 0 ? (
                            <div className="text-center py-20 text-slate-400 text-[11px] font-mono uppercase tracking-widest">
                                No patient records found
                            </div>
                        ) : (
                            patients.map(patient => {
                                const hasCodes = (patient.icd10_codes?.length ?? 0) > 0 || (patient.procedure_codes?.length ?? 0) > 0;
                                const isExpanded = expandedPatient === patient.id;
                                const status = patient.billing_summary?.coding_status;

                                return (
                                    <motion.div
                                        key={patient.id}
                                        initial={{ opacity: 0, y: 4 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        className="bg-white border border-slate-200 rounded-xl overflow-hidden"
                                    >
                                        {/* Patient row */}
                                        <div
                                            className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-50 transition-colors"
                                            onClick={() => setExpandedPatient(isExpanded ? null : patient.id)}
                                        >
                                            <div className="w-7 h-7 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0">
                                                <User className="w-3.5 h-3.5 text-slate-500" />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-xs font-bold text-slate-800 uppercase">{patient.name || 'Unknown'}</span>
                                                    <span className="text-[10px] text-slate-400 font-mono">{patient.age}Y · {patient.gender}</span>
                                                </div>
                                                <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                                                    {statusBadge(status)}
                                                    {!hasCodes && (
                                                        <span className="text-[9px] font-mono text-slate-400 italic">No codes yet — commit to EHR to trigger auto-coding</span>
                                                    )}
                                                    {patient.icd10_codes?.slice(0, 3).map((c, i) => (
                                                        <span key={i} className="text-[9px] font-bold font-mono text-blue-700 bg-blue-50 border border-blue-200 px-1.5 py-0.5 rounded">
                                                            {c.code}
                                                        </span>
                                                    ))}
                                                    {(patient.procedure_codes?.length ?? 0) > 0 && (
                                                        <span className="text-[9px] font-mono text-violet-600">
                                                            +{patient.procedure_codes!.length} px
                                                        </span>
                                                    )}
                                                </div>
                                            </div>
                                            <div className="flex items-center gap-2 shrink-0">
                                                {hasCodes && status !== 'confirmed' && (
                                                    <button
                                                        onClick={e => { e.stopPropagation(); confirmBilling(patient.id); }}
                                                        disabled={confirmingId === patient.id}
                                                        className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold uppercase tracking-widest bg-emerald-50 text-emerald-700 border border-emerald-200 rounded hover:bg-emerald-100 transition-colors disabled:opacity-50"
                                                    >
                                                        {confirmingId === patient.id
                                                            ? <RefreshCw className="w-2.5 h-2.5 animate-spin" />
                                                            : <CheckCircle2 className="w-2.5 h-2.5" />
                                                        }
                                                        Confirm
                                                    </button>
                                                )}
                                                {isExpanded
                                                    ? <ChevronUp className="w-4 h-4 text-slate-400" />
                                                    : <ChevronDown className="w-4 h-4 text-slate-400" />
                                                }
                                            </div>
                                        </div>

                                        {/* Expanded billing detail */}
                                        <AnimatePresence>
                                            {isExpanded && hasCodes && (
                                                <motion.div
                                                    initial={{ height: 0, opacity: 0 }}
                                                    animate={{ height: 'auto', opacity: 1 }}
                                                    exit={{ height: 0, opacity: 0 }}
                                                    transition={{ duration: 0.2 }}
                                                    className="overflow-hidden"
                                                >
                                                    <div className="px-4 pb-4 pt-1 border-t border-slate-100 space-y-4">
                                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                            {/* Diagnosis codes */}
                                                            {(patient.icd10_codes?.length ?? 0) > 0 && (
                                                                <div className="space-y-2">
                                                                    <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1">
                                                                        <Stethoscope className="w-2.5 h-2.5" /> ICD-10-CM Diagnoses
                                                                    </label>
                                                                    <div className="space-y-1">
                                                                        {patient.icd10_codes!.map((c, i) => <CodeRow key={i} entry={c} variant="dx" />)}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {/* Procedure codes */}
                                                            {(patient.procedure_codes?.length ?? 0) > 0 && (
                                                                <div className="space-y-2">
                                                                    <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1">
                                                                        <Receipt className="w-2.5 h-2.5" /> ICD-10-PCS Procedures
                                                                    </label>
                                                                    <div className="space-y-1">
                                                                        {patient.procedure_codes!.map((c, i) => <CodeRow key={i} entry={c} variant="px" />)}
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </div>

                                                        {/* Billing notes */}
                                                        {patient.billing_summary?.billing_notes && (
                                                            <div className="space-y-1.5">
                                                                <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1">
                                                                    <FileText className="w-2.5 h-2.5" /> Insurance Claim Notes
                                                                </label>
                                                                <pre className="text-[10px] font-mono text-slate-600 bg-slate-50 border border-slate-200 rounded-lg p-3 whitespace-pre-wrap leading-relaxed">
                                                                    {patient.billing_summary.billing_notes}
                                                                </pre>
                                                            </div>
                                                        )}
                                                    </div>
                                                </motion.div>
                                            )}
                                            {isExpanded && !hasCodes && (
                                                <div className="px-4 pb-3 pt-1 border-t border-slate-100">
                                                    <p className="text-[10px] font-mono text-slate-400 italic">
                                                        Billing codes have not been generated for this patient yet. They auto-generate in the background after EHR commit.
                                                    </p>
                                                </div>
                                            )}
                                        </AnimatePresence>
                                    </motion.div>
                                );
                            })
                        )}
                    </div>
                )}

                {/* ── Code Browser Tab ────────────────────────────────────────────────── */}
                {activeTab === 'browser' && (
                    <div className="p-4 space-y-4 max-w-3xl mx-auto">
                        <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
                            <div className="flex items-center gap-2">
                                {/* Toggle */}
                                <div className="flex bg-slate-100 border border-slate-200 rounded-lg p-0.5 shrink-0">
                                    <button
                                        onClick={() => setCodeType('diagnosis')}
                                        className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded-md transition-all ${codeType === 'diagnosis' ? 'bg-white shadow-sm text-blue-700 border border-blue-200' : 'text-slate-500 hover:text-slate-700'}`}
                                    >
                                        ICD-10-CM
                                    </button>
                                    <button
                                        onClick={() => setCodeType('procedure')}
                                        className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded-md transition-all ${codeType === 'procedure' ? 'bg-white shadow-sm text-violet-700 border border-violet-200' : 'text-slate-500 hover:text-slate-700'}`}
                                    >
                                        ICD-10-PCS
                                    </button>
                                </div>

                                {/* Search input */}
                                <div className="relative flex-1">
                                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-400" />
                                    {searchLoading && (
                                        <RefreshCw className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-400 animate-spin" />
                                    )}
                                    <input
                                        type="text"
                                        placeholder={codeType === 'diagnosis' ? 'Search by code (J06) or description (fever)…' : 'Search procedure code or description…'}
                                        value={searchQuery}
                                        onChange={e => setSearchQuery(e.target.value)}
                                        className="w-full pl-8 pr-8 py-2 bg-slate-50 border border-slate-200 rounded-lg text-[11px] font-mono focus:bg-white focus:border-slate-400 outline-none text-slate-900 transition-all"
                                    />
                                </div>
                            </div>

                            <p className="text-[9px] text-slate-400 font-mono italic">
                                {codeType === 'diagnosis'
                                    ? '74,000+ ICD-10-CM diagnosis codes — fully offline'
                                    : '78,000+ ICD-10-PCS procedure codes — fully offline'}
                            </p>
                        </div>

                        {/* Results */}
                        <AnimatePresence>
                            {searchResults.length > 0 && (
                                <motion.div
                                    initial={{ opacity: 0, y: 4 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0 }}
                                    className="space-y-1.5"
                                >
                                    {searchResults.map((r, i) => (
                                        <CodeRow key={i} entry={r} variant={codeType === 'diagnosis' ? 'dx' : 'px'} />
                                    ))}
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {searchQuery && !searchLoading && searchResults.length === 0 && (
                            <div className="text-center py-8 text-slate-400 text-[10px] font-mono uppercase tracking-widest">
                                No codes found for &ldquo;{searchQuery}&rdquo;
                            </div>
                        )}

                        {!searchQuery && (
                            <div className="text-center py-12 text-slate-300 text-[10px] font-mono uppercase tracking-widest">
                                Start typing to search the offline code database
                            </div>
                        )}
                    </div>
                )}

                {/* ── Clinical Trends Tab ──────────────────────────────────────────────── */}
                {activeTab === 'trends' && (
                    <div className="p-4 space-y-4 max-w-5xl mx-auto">
                        {trendsLoading ? (
                            <div className="flex items-center justify-center h-64">
                                <div className="w-5 h-5 border-2 border-slate-300 border-t-slate-700 rounded-full animate-spin" />
                            </div>
                        ) : !trends ? (
                            <div className="text-center py-20 text-slate-400 text-[11px] font-mono">Failed to load trends</div>
                        ) : (
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                {/* Top diagnoses */}
                                <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
                                    <div className="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase tracking-[0.2em]">
                                        <Stethoscope className="w-3 h-3" /> Top Diagnoses
                                    </div>
                                    {trends.top_diagnoses.length === 0 ? (
                                        <p className="text-[10px] font-mono text-slate-300 italic">No data yet</p>
                                    ) : (
                                        <div className="space-y-3">
                                            {trends.top_diagnoses.map((item, i) => (
                                                <TrendBar
                                                    key={i}
                                                    label={item.label}
                                                    count={item.count}
                                                    max={trends.top_diagnoses[0]?.count ?? 1}
                                                />
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {/* Top procedures */}
                                <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
                                    <div className="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase tracking-[0.2em]">
                                        <Receipt className="w-3 h-3" /> Top Procedures
                                    </div>
                                    {trends.top_procedures.length === 0 ? (
                                        <p className="text-[10px] font-mono text-slate-300 italic">No data yet</p>
                                    ) : (
                                        <div className="space-y-3">
                                            {trends.top_procedures.map((item, i) => (
                                                <TrendBar
                                                    key={i}
                                                    label={item.label}
                                                    count={item.count}
                                                    max={trends.top_procedures[0]?.count ?? 1}
                                                />
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {/* Top symptoms */}
                                <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
                                    <div className="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase tracking-[0.2em]">
                                        <Activity className="w-3 h-3" /> Top Symptoms
                                    </div>
                                    {trends.top_symptoms.length === 0 ? (
                                        <p className="text-[10px] font-mono text-slate-300 italic">No data yet</p>
                                    ) : (
                                        <div className="space-y-3">
                                            {trends.top_symptoms.map((item, i) => (
                                                <TrendBar
                                                    key={i}
                                                    label={item.label}
                                                    count={item.count}
                                                    max={trends.top_symptoms[0]?.count ?? 1}
                                                />
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {/* Summary card */}
                                <div className="md:col-span-3 bg-white border border-slate-200 rounded-xl p-4">
                                    <div className="flex items-center gap-6 text-center divide-x divide-slate-200">
                                        <div className="flex-1">
                                            <p className="text-2xl font-bold text-slate-800">{trends.total_patients}</p>
                                            <p className="text-[10px] font-mono text-slate-400 uppercase tracking-widest mt-1">Total Patients</p>
                                        </div>
                                        <div className="flex-1 pl-6">
                                            <p className="text-2xl font-bold text-blue-700">{codedCount}</p>
                                            <p className="text-[10px] font-mono text-slate-400 uppercase tracking-widest mt-1">Auto-coded</p>
                                        </div>
                                        <div className="flex-1 pl-6">
                                            <p className="text-2xl font-bold text-emerald-700">{confirmedCount}</p>
                                            <p className="text-[10px] font-mono text-slate-400 uppercase tracking-widest mt-1">Confirmed</p>
                                        </div>
                                        <div className="flex-1 pl-6">
                                            <p className="text-2xl font-bold text-violet-700">{trends.top_procedures.reduce((s, t) => s + t.count, 0)}</p>
                                            <p className="text-[10px] font-mono text-slate-400 uppercase tracking-widest mt-1">Procedure Codes</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </main>
    );
}

// ─── Default export wraps in Suspense (required for useSearchParams) ──────────

export default function DiagnosticsPage() {
    return (
        <Suspense fallback={
            <div className="h-screen flex items-center justify-center bg-[#f8fafc]">
                <div className="w-5 h-5 border-2 border-slate-300 border-t-slate-700 rounded-full animate-spin" />
            </div>
        }>
            <DiagnosticsInner />
        </Suspense>
    );
}
