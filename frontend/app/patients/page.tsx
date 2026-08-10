"use client";

import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowLeft, User, Activity, Calendar, FileText, Search, ClipboardList, Trash2, ChevronRight, Receipt, Stethoscope, RefreshCw, Download } from 'lucide-react';
import Link from 'next/link';
import { getAyushmanTemplate, getCGHSTemplate, getECHSTemplate } from '../utils/documentTemplates';
import { API } from '@/lib/api';

function downloadJson(filename: string, payload: unknown) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/fhir+json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

export default function PatientsPage() {
    const [patients, setPatients] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState("");
    const [selectedPatient, setSelectedPatient] = useState<any | null>(null);
    const [patientToDelete, setPatientToDelete] = useState<any | null>(null);
    const [patientToExport, setPatientToExport] = useState<any | null>(null);

    const handleExport = (scheme: 'AYUSHMAN' | 'CGHS' | 'ECHS') => {
        if (!patientToExport) return;

        let htmlContent = '';
        switch (scheme) {
            case 'AYUSHMAN':
                htmlContent = getAyushmanTemplate(patientToExport);
                break;
            case 'CGHS':
                htmlContent = getCGHSTemplate(patientToExport);
                break;
            case 'ECHS':
                htmlContent = getECHSTemplate(patientToExport);
                break;
        }

        const printWindow = window.open('', '_blank');
        if (printWindow) {
            printWindow.document.write(htmlContent);
            printWindow.document.close();
            printWindow.focus();
            // Allow time for styles to load/render implies immediate print might need a slight delay or onload
            setTimeout(() => {
                printWindow.print();
                printWindow.close();
            }, 500);
        }
        setPatientToExport(null);
    };

    const handleFhirExport = async () => {
        if (!patientToExport?.id) return;

        try {
            const response = await fetch(`${API.EHR}/patients/${patientToExport.id}/fhir`);
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || 'FHIR export failed');
            }
            const bundle = await response.json();
            downloadJson(`parchee-fhir-${patientToExport.id}.json`, bundle);
            setPatientToExport(null);
        } catch (error) {
            console.error(error);
            alert("Failed to export FHIR bundle");
        }
    };

    useEffect(() => {
        fetch(`${API.EHR}/patients`)
            .then(res => res.json())
            .then(data => {
                setPatients(data);
                setLoading(false);
            })
            .catch(err => {
                console.error(err);
                setLoading(false);
            });
    }, []);

    const filteredPatients = patients.filter(p =>
        p.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        p.tentative_doctor_diagnosis?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        p.initial_llm_diagnosis?.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const deletePatient = async () => {
        if (!patientToDelete) return;

        try {
            const res = await fetch(`${API.EHR}/patients/${patientToDelete.id}`, {
                method: 'DELETE',
            });
            if (res.ok) {
                setPatients(prev => prev.filter(p => p.id !== patientToDelete.id));
                setPatientToDelete(null);
                if (selectedPatient?.id === patientToDelete.id) {
                    setSelectedPatient(null);
                }
            } else {
                alert("Failed to delete patient");
            }
        } catch (error) {
            console.error(error);
            alert("Error deleting patient");
        }
    };

    return (
        <main className="h-screen flex flex-col bg-background text-foreground overflow-hidden">
            <div className="flex-none bg-white/80 backdrop-blur-xl border-b border-border px-6 py-4 flex items-center justify-between gap-4">
                <h1 className="text-sm font-bold tracking-[0.1em] text-foreground uppercase">Archives <span className="text-primary/20">/</span> Patient Registry</h1>

                <div className="relative">
                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-primary/40" />
                    <input
                        type="search"
                        placeholder="Search patient records..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="pl-11 pr-4 py-2 bg-secondary/50 border border-border rounded-xl text-[11px] font-bold focus:bg-white focus:border-primary/30 outline-none w-[320px] text-foreground transition-all uppercase tracking-wider"
                    />
                </div>
            </div>

            <div className="flex-1 overflow-auto p-3 md:p-6 space-y-6">

                {loading ? (
                    <div className="flex items-center justify-center h-[300px]">
                        <div className="w-4 h-4 border border-slate-300 border-t-white animate-spin rounded-full" />
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                        {filteredPatients.map((patient) => (
                            <motion.div
                                key={patient.id}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                className="bg-white border border-border rounded-3xl p-5 group hover:border-primary/30 hover:shadow-xl hover:shadow-primary/5 transition-all cursor-pointer relative"
                                onClick={() => setSelectedPatient(patient)}
                            >
                                <div className="flex items-start justify-between mb-5">
                                    <div className="flex items-center gap-4">
                                        <div className="w-10 h-10 rounded-2xl border border-primary/10 bg-primary/5 flex items-center justify-center text-primary">
                                            <User className="w-5 h-5" />
                                        </div>
                                        <div>
                                            <h3 className="font-bold text-sm tracking-tight text-foreground truncate max-w-[140px] uppercase">{patient.name || "Unknown"}</h3>
                                            <p className="text-[10px] text-muted-foreground font-bold tracking-tight">{patient.age}Y • {patient.gender}</p>
                                        </div>
                                    </div>
                                    <span className="text-[9px] font-bold text-primary/40 bg-primary/5 px-2 py-1 rounded-lg border border-primary/10">
                                        #{String(patient.id ?? '').slice(0, 8)}
                                    </span>
                                </div>

                                <div className="space-y-5">
                                    <div className="p-4 bg-secondary/30 border border-border rounded-2xl space-y-2.5">
                                        <div className="flex items-center gap-2 text-[9px] font-bold text-primary/40 uppercase tracking-[0.3em]">
                                            <Activity className="w-3 h-3" /> Biometrics
                                        </div>
                                        <div className="grid grid-cols-2 gap-3 text-[11px]">
                                            <div><span className="text-muted-foreground/50 font-bold uppercase tracking-tighter text-[9px] mr-1">BP</span> <span className="font-bold text-foreground/80">{patient.vitals?.blood_pressure || "--"}</span></div>
                                            <div><span className="text-muted-foreground/50 font-bold uppercase tracking-tighter text-[9px] mr-1">HR</span> <span className="font-bold text-foreground/80">{patient.vitals?.pulse || "--"}</span></div>
                                        </div>
                                    </div>

                                    <div className="space-y-2">
                                        <div className="flex items-center gap-2 text-[9px] font-bold text-primary/40 uppercase tracking-[0.3em]">
                                            <FileText className="w-3 h-3" /> Impression
                                        </div>
                                        <p className="text-[12px] font-medium text-foreground/70 line-clamp-2 leading-relaxed italic">
                                            "{patient.tentative_doctor_diagnosis || patient.initial_llm_diagnosis || "No clinical findings recorded"}"
                                        </p>

                                        {/* ICD-10-CM Code Badges */}
                                        {Array.isArray(patient.icd10_codes) && patient.icd10_codes.length > 0 && (
                                            <div className="flex flex-wrap gap-1.5 pt-1.5">
                                                {patient.icd10_codes.slice(0, 2).map((c: any, i: number) => (
                                                    <span key={i} className="px-2 py-0.5 bg-primary/5 border border-primary/10 text-primary text-[9px] font-bold rounded-lg uppercase tracking-tighter">
                                                        {c.code}
                                                    </span>
                                                ))}
                                                {patient.icd10_codes.length > 2 && (
                                                    <span className="px-2 py-0.5 text-muted-foreground/40 text-[9px] font-bold">+{patient.icd10_codes.length - 2}</span>
                                                )}
                                            </div>
                                        )}
                                        {/* ICD-10-PCS Procedure Badge Count */}
                                        {Array.isArray(patient.procedure_codes) && patient.procedure_codes.length > 0 && (
                                            <div className="flex items-center gap-2 pt-1">
                                                <Receipt className="w-3 h-3 text-primary/40" />
                                                <span className="text-[9px] font-bold text-primary/40 uppercase tracking-tighter">{patient.procedure_codes.length} PCS CODE{patient.procedure_codes.length > 1 ? 'S' : ''}</span>
                                            </div>
                                        )}
                                    </div>

                                    <div className="pt-5 border-t border-border flex items-center justify-between">
                                        <div className="flex items-center gap-2 text-[10px] text-muted-foreground font-bold uppercase tracking-tighter">
                                            <Calendar className="w-3 h-3" />
                                            {new Date(patient.created_at).toLocaleDateString()}
                                        </div>
                                        <div className="flex items-center gap-1">
                                            <button
                                                className="p-2 text-muted-foreground hover:text-primary hover:bg-primary/5 rounded-xl transition-all"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setPatientToExport(patient);
                                                }}
                                                title="Export Official Document"
                                            >
                                                <FileText className="w-4 h-4" />
                                            </button>
                                            <Link
                                                href={`/?patient_id=${patient.id}`}
                                                className="p-2 text-muted-foreground hover:text-primary hover:bg-primary/5 rounded-xl transition-all"
                                                onClick={(e) => e.stopPropagation()}
                                                title="Resume Session"
                                            >
                                                <RefreshCw className="w-4 h-4" />
                                            </Link>
                                            <button
                                                className="p-2 text-muted-foreground hover:text-rose-500 hover:bg-rose-500/5 rounded-xl transition-all"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setPatientToDelete(patient);
                                                }}
                                                title="Delete Record"
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </motion.div>
                        ))}

                        {filteredPatients.length === 0 && (
                            <div className="col-span-full py-12 text-center text-slate-400 text-[10px] font-mono uppercase tracking-widest bg-white border border-dashed border-slate-200 rounded-xl">
                                <p>Query returned 0 results</p>
                            </div>
                        )}
                    </div>
                )}

                {/* Patient Detail Modal */}
                <AnimatePresence>
                    {selectedPatient && (
                        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-[#f6f7fb]/90 backdrop-blur-md">
                            <motion.div
                                initial={{ opacity: 0, scale: 0.98 }}
                                animate={{ opacity: 1, scale: 1 }}
                                exit={{ opacity: 0, scale: 0.98 }}
                                className="bg-white border border-slate-200 rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col"
                            >
                            <div className="p-4 border-b border-border flex items-center justify-between bg-primary/5">
                                <div className="flex items-center gap-3">
                                    <div className="w-10 h-10 rounded-xl border border-primary/20 bg-background flex items-center justify-center text-primary/60 shadow-sm">
                                        <User className="w-5 h-5" />
                                    </div>
                                    <div>
                                        <h2 className="text-lg font-bold tracking-tight text-foreground uppercase">{selectedPatient.name}</h2>
                                        <p className="text-[10px] font-mono text-primary/40 uppercase">{selectedPatient.age}Y • {selectedPatient.gender} • ID:{selectedPatient.id}</p>
                                    </div>
                                </div>
                                <button
                                    onClick={() => setSelectedPatient(null)}
                                    className="p-2 hover:bg-primary/5 rounded-full transition-colors text-primary/40 hover:text-primary"
                                >
                                        <ArrowLeft className="w-5 h-5" />
                                    </button>
                                </div>

                                <div className="flex-1 overflow-y-auto p-6 space-y-8 scrollbar-hide">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                                        {/* Vitals */}
                                        <div className="md:col-span-1 space-y-6">
                                            <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.3em] flex items-center gap-2">
                                                <Activity className="w-3 h-3" /> Biometrics
                                            </h3>
                                            <div className="grid grid-cols-1 gap-2">
                                                <DetailBox label="Blood Pressure" value={selectedPatient.vitals?.blood_pressure} />
                                                <DetailBox label="Heart Rate" value={selectedPatient.vitals?.pulse} unit="BPM" />
                                                <DetailBox label="Temperature" value={selectedPatient.vitals?.temperature} />
                                                <DetailBox label="SpO2" value={selectedPatient.vitals?.spo2} unit="%" />
                                            </div>
                                        </div>

                                        {/* Clinical Info */}
                                        <div className="md:col-span-2 space-y-8">
                                            <div className="space-y-4">
                                                <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.3em] flex items-center gap-2">
                                                    <ClipboardList className="w-3 h-3" /> Analysis
                                                </h3>
                                                <div className="space-y-6">
                                                    <div>
                                                        <label className="text-[9px] font-bold text-slate-400 uppercase block mb-2 tracking-widest">Chief Complaint</label>
                                                        <p className="text-xs text-slate-700 border-l border-slate-200 pl-4 py-1 leading-relaxed">{selectedPatient.chief_complaint || "None"}</p>
                                                    </div>

                                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                        <div className="p-4 bg-slate-50 border border-slate-200 rounded-lg space-y-2">
                                                            <label className="text-[9px] font-bold text-slate-500 uppercase block tracking-widest">Medical Impression</label>
                                                            <p className="text-xs text-slate-700 leading-relaxed italic">
                                                                {selectedPatient.tentative_doctor_diagnosis || "Awaiting verification"}
                                                            </p>
                                                        </div>
                                                        <div className="p-4 bg-white border border-slate-200 rounded-lg space-y-2">
                                                            <label className="text-[9px] font-bold text-slate-400 uppercase block tracking-widest">Heuristic Output</label>
                                                            <p className="text-xs text-slate-500 leading-relaxed font-mono">
                                                                {selectedPatient.initial_llm_diagnosis || "None"}
                                                            </p>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 pt-4 border-t border-slate-200">
                                                <ListSection title="Symptoms" items={selectedPatient.symptoms} />
                                                <ListSection title="Medications" items={selectedPatient.medications} />
                                                <ListSection title="Procedures" items={selectedPatient.procedures} />
                                                <ListSection title="History" items={selectedPatient.medical_history} />
                                                <ListSection title="Family" items={selectedPatient.family_history} />
                                                <ListSection title="Allergies" items={selectedPatient.allergies} />
                                            </div>

                                            {/* Billing Codes Section */}
                                            {((selectedPatient.icd10_codes?.length ?? 0) > 0 || (selectedPatient.procedure_codes?.length ?? 0) > 0) && (
                                                <div className="pt-4 border-t border-slate-200 space-y-4">
                                                    <div className="flex items-center justify-between">
                                                        <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.3em] flex items-center gap-2">
                                                            <Receipt className="w-3 h-3" /> Billing Codes
                                                        </h3>
                                                        {selectedPatient.billing_summary?.coding_status && (
                                                            <span className={`text-[9px] font-bold px-2 py-0.5 rounded-full border ${selectedPatient.billing_summary.coding_status === 'confirmed'
                                                                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                                                    : 'bg-blue-50 text-blue-600 border-blue-200'
                                                                }`}>
                                                                {selectedPatient.billing_summary.coding_status === 'confirmed' ? '✓ Confirmed' : '⚡ Auto-coded'}
                                                            </span>
                                                        )}
                                                    </div>
                                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                        {(selectedPatient.icd10_codes?.length ?? 0) > 0 && (
                                                            <div className="space-y-1.5">
                                                                <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1">
                                                                    <Stethoscope className="w-2.5 h-2.5" /> ICD-10-CM Diagnoses
                                                                </label>
                                                                {selectedPatient.icd10_codes.map((c: any, i: number) => (
                                                                    <div key={i} className="flex justify-between items-center px-2 py-1 bg-blue-50 border border-blue-100 rounded text-[10px]">
                                                                        <span className="font-bold font-mono text-blue-700">{c.code}</span>
                                                                        <span className="text-slate-600 truncate mx-2 flex-1">{c.description}</span>
                                                                        <span className="font-mono text-slate-400 shrink-0">{Math.round(c.confidence * 100)}%</span>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        )}
                                                        {(selectedPatient.procedure_codes?.length ?? 0) > 0 && (
                                                            <div className="space-y-1.5">
                                                                <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1">
                                                                    <Receipt className="w-2.5 h-2.5" /> ICD-10-PCS Procedures
                                                                </label>
                                                                {selectedPatient.procedure_codes.map((c: any, i: number) => (
                                                                    <div key={i} className="flex justify-between items-center px-2 py-1 bg-violet-50 border border-violet-100 rounded text-[10px]">
                                                                        <span className="font-bold font-mono text-violet-700">{c.code}</span>
                                                                        <span className="text-slate-600 truncate mx-2 flex-1">{c.description}</span>
                                                                        <span className="font-mono text-slate-400 shrink-0">{Math.round(c.confidence * 100)}%</span>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </div>
                                                    <div className="pt-1">
                                                        <Link
                                                            href={`/diagnostics?patient_id=${selectedPatient.id}`}
                                                            className="text-[10px] font-semibold text-blue-600 hover:text-blue-800 transition-colors uppercase tracking-widest"
                                                        >
                                                            Open in Billing &amp; Coding Center →
                                                        </Link>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            </motion.div>
                        </div>
                    )}
                </AnimatePresence>

                {/* Export Scheme Selection Modal */}
                <AnimatePresence>
                    {patientToExport && (
                        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm">
                            <motion.div
                                initial={{ opacity: 0, scale: 0.95 }}
                                animate={{ opacity: 1, scale: 1 }}
                                exit={{ opacity: 0, scale: 0.95 }}
                                className="bg-white border border-slate-200 rounded-xl shadow-2xl w-full max-w-md p-6 space-y-6"
                            >
                                <div className="space-y-2 text-center">
                                    <div className="w-12 h-12 rounded-full bg-emerald-500/10 flex items-center justify-center text-emerald-500 mx-auto border border-emerald-500/20">
                                        <FileText className="w-6 h-6" />
                                    </div>
                                    <h3 className="text-xl font-bold text-slate-900 tracking-tight">Export Official Document</h3>
                                    <p className="text-sm text-slate-500 font-mono">
                                        Select the government scheme format for <span className="text-slate-900 font-bold">{patientToExport.name}</span>
                                    </p>
                                </div>

                                <div className="grid grid-cols-1 gap-3">
                                    <button
                                        onClick={handleFhirExport}
                                        className="group flex items-center justify-between p-4 rounded-lg bg-white/5 border border-slate-200 hover:bg-white/10 hover:border-slate-500/50 transition-all"
                                    >
                                        <div className="text-left">
                                            <div className="text-sm font-bold text-slate-900 group-hover:text-slate-700 transition-colors">FHIR R4 Bundle</div>
                                            <div className="text-[10px] text-slate-400 font-mono">Interoperability JSON Export</div>
                                        </div>
                                        <Download className="w-4 h-4 text-slate-400 group-hover:text-slate-700" />
                                    </button>

                                    <button
                                        onClick={() => handleExport('AYUSHMAN')}
                                        className="group flex items-center justify-between p-4 rounded-lg bg-white/5 border border-slate-200 hover:bg-white/10 hover:border-emerald-500/50 transition-all"
                                    >
                                        <div className="text-left">
                                            <div className="text-sm font-bold text-slate-900 group-hover:text-emerald-400 transition-colors">Ayushman Bharat (PM-JAY)</div>
                                            <div className="text-[10px] text-slate-400 font-mono">Discharge Summary Format</div>
                                        </div>
                                        <ChevronRight className="w-4 h-4 text-slate-400 group-hover:text-emerald-500" />
                                    </button>

                                    <button
                                        onClick={() => handleExport('CGHS')}
                                        className="group flex items-center justify-between p-4 rounded-lg bg-white/5 border border-slate-200 hover:bg-white/10 hover:border-blue-500/50 transition-all"
                                    >
                                        <div className="text-left">
                                            <div className="text-sm font-bold text-slate-900 group-hover:text-blue-400 transition-colors">CGHS Prescription</div>
                                            <div className="text-[10px] text-slate-400 font-mono">Central Govt Health Scheme</div>
                                        </div>
                                        <ChevronRight className="w-4 h-4 text-slate-400 group-hover:text-blue-500" />
                                    </button>

                                    <button
                                        onClick={() => handleExport('ECHS')}
                                        className="group flex items-center justify-between p-4 rounded-lg bg-white/5 border border-slate-200 hover:bg-white/10 hover:border-orange-500/50 transition-all"
                                    >
                                        <div className="text-left">
                                            <div className="text-sm font-bold text-slate-900 group-hover:text-orange-400 transition-colors">ECHS Medical Slip</div>
                                            <div className="text-[10px] text-slate-400 font-mono">Ex-Servicemen Contributory Health</div>
                                        </div>
                                        <ChevronRight className="w-4 h-4 text-slate-400 group-hover:text-orange-500" />
                                    </button>
                                </div>

                                <button
                                    onClick={() => setPatientToExport(null)}
                                    className="w-full py-3 rounded-xl border border-border hover:bg-primary/5 text-xs font-bold uppercase tracking-widest text-primary/40 hover:text-primary transition-colors"
                                >
                                    Cancel
                                </button>
                            </motion.div>
                        </div>
                    )}
                </AnimatePresence>

                {/* Delete Confirmation Modal */}
                <AnimatePresence>
                    {patientToDelete && (
                        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm">
                            <motion.div
                                initial={{ opacity: 0, scale: 0.95 }}
                                animate={{ opacity: 1, scale: 1 }}
                                exit={{ opacity: 0, scale: 0.95 }}
                                className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-sm p-6 space-y-4"
                            >
                                <div className="space-y-2 text-center">
                                    <div className="w-12 h-12 rounded-full bg-destructive/10 flex items-center justify-center text-destructive mx-auto">
                                        <Trash2 className="w-6 h-6" />
                                    </div>
                                    <h3 className="text-lg font-bold">Delete Patient Record?</h3>
                                    <p className="text-sm text-muted-foreground">
                                        Are you sure you want to delete the record for <span className="font-bold text-foreground">{patientToDelete.name}</span>? This action cannot be undone.
                                    </p>
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <button
                                        onClick={() => setPatientToDelete(null)}
                                        className="px-4 py-2 rounded-lg border border-border bg-background hover:bg-muted font-medium transition-colors"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        onClick={deletePatient}
                                        className="px-4 py-2 rounded-lg bg-destructive text-destructive-foreground hover:bg-destructive/90 font-bold transition-colors"
                                    >
                                        Delete
                                    </button>
                                </div>
                            </motion.div>
                        </div>
                    )}
                </AnimatePresence>
            </div>
        </main>
    );
}

function DetailBox({ label, value, unit }: any) {
    return (
        <div className="p-3 bg-slate-50 border border-slate-200 rounded">
            <label className="text-[8px] font-bold text-slate-400 uppercase block tracking-widest mb-1">{label}</label>
            <p className="text-xs font-bold font-mono text-slate-700">
                {value || "--"}<span className="text-[10px] font-normal text-slate-400 ml-1">{unit}</span>
            </p>
        </div>
    )
}

function ListSection({ title, items }: any) {
    const list = Array.isArray(items) ? items : [];
    return (
        <div className="space-y-2">
            <h4 className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">{title}</h4>
            <div className="flex flex-wrap gap-1.5">
                {list.length > 0 ? list.map((item: string, i: number) => (
                    <span key={i} className="px-1.5 py-0.5 bg-white/5 text-slate-600 text-[9px] font-mono border border-slate-200 rounded capitalize">{item}</span>
                )) : (
                    <span className="text-[9px] text-slate-300 font-mono italic">None</span>
                )}
            </div>
        </div>
    )
}
