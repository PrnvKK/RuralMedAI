"use client";

import { ChangeEvent, useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import type { FieldPath, UseFormRegisterReturn } from 'react-hook-form';
import { motion, AnimatePresence } from 'framer-motion';
import { PatientData, Vitals } from '@/types';
import { ClipboardList, Thermometer, User, Activity, CreditCard, CheckCircle2, Info, Stethoscope, Receipt } from 'lucide-react';

interface LiveFormProps {
    data: PatientData;
    onFieldChange?: (field: string, value: string) => void;
}

// register() wrapper that forwards edits to the parent; typed against the
// PatientData field names so callers cannot pass a typo'd field path.
type SyncedRegister = (name: FieldPath<PatientData>) => UseFormRegisterReturn<string>;

export function LiveForm({ data, onFieldChange }: LiveFormProps) {
    const { register, setValue, getValues } = useForm<PatientData>({ defaultValues: data });
    const [lastUpdatedField, setLastUpdatedField] = useState<string | null>(null);
    const syncedRegister: SyncedRegister = (name) => register(name, {
        onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
            onFieldChange?.(String(name), event.target.value);
        }
    });

    useEffect(() => {
        Object.keys(data).forEach((key) => {
            const k = key as keyof PatientData;
            if (data[k] !== undefined) {
                if (k === 'vitals' && typeof data[k] === 'object') {
                    const vitals = data[k] as Vitals;
                    (Object.keys(vitals) as Array<keyof Vitals>).forEach(vKey => {
                        const path = `vitals.${String(vKey)}` as FieldPath<PatientData>;
                        if (JSON.stringify(vitals[vKey]) !== JSON.stringify(getValues(path))) {
                            setValue(path, vitals[vKey]);
                            setLastUpdatedField('vitals');
                            setTimeout(() => setLastUpdatedField(null), 1000);
                        }
                    });
                } else if (JSON.stringify(data[k]) !== JSON.stringify(getValues(k))) {
                    setValue(k, data[k]);
                    setLastUpdatedField(k);
                    setTimeout(() => setLastUpdatedField(null), 1000);
                }
            }
        });
    }, [data, setValue, getValues]);

    return (
        <div className="space-y-4 p-4 bg-transparent h-full flex flex-col overflow-hidden">
            <div className="bg-white border border-border shadow-sm rounded-3xl px-6 py-4 flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-2xl bg-primary/5 border border-primary/10 flex items-center justify-center">
                        <ClipboardList className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                        <h2 className="text-sm font-bold tracking-[0.2em] uppercase text-foreground">Intelligent Clinical Scribe</h2>
                        <p className="text-[10px] text-muted-foreground uppercase tracking-[0.14em] font-medium mt-1">Real-time data extraction and analysis</p>
                    </div>
                </div>
                <div className="px-4 py-2 bg-primary/5 rounded-2xl border border-primary/10 flex items-center gap-3">
                    <div className="w-2.5 h-2.5 rounded-full bg-primary shadow-[0_0_8px_rgba(75,83,32,0.4)] animate-pulse" />
                    <span className="text-[10px] font-bold text-primary uppercase tracking-widest">Live extraction</span>
                </div>
            </div>

            <div className="grid grid-cols-1 gap-4 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                <section className="bg-white border border-border shadow-sm rounded-[2rem] p-5">
                    <div className="grid grid-cols-1 md:grid-cols-[1.5fr_1px_1fr] gap-6 items-center">
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-[10px] font-bold text-primary/80 uppercase tracking-[0.2em]">
                                <User className="w-4 h-4" /> Identification
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
                                <div className="md:col-span-2">
                                    <InputField label="Name" name="name" register={syncedRegister} highlight={lastUpdatedField === 'name'} placeholder="Full Name" />
                                </div>
                                <InputField label="Age" name="age" register={syncedRegister} highlight={lastUpdatedField === 'age'} placeholder="--" />
                                <InputField label="Gender" name="gender" register={syncedRegister} highlight={lastUpdatedField === 'gender'} placeholder="--" />
                            </div>
                            <InputField
                                label="Chief Complaint"
                                name="chief_complaint"
                                register={syncedRegister}
                                highlight={lastUpdatedField === 'chief_complaint'}
                                isTextArea
                                placeholder="Describe symptoms..."
                                minHeight="h-[84px]"
                            />
                        </div>

                        <div className="hidden md:block w-px h-[82%] bg-border self-center" />

                        <div className="flex flex-col justify-center gap-4 w-full max-w-[420px] md:mx-auto">
                            <div className="flex items-center gap-2 text-[10px] font-bold text-primary/80 uppercase tracking-[0.2em]">
                                <Thermometer className="w-4 h-4" /> Biometrics
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                                <VitalField label="BP" name="vitals.blood_pressure" register={syncedRegister} highlight={lastUpdatedField === 'vitals'} unit="mmHg" />
                                <VitalField label="HR" name="vitals.pulse" register={syncedRegister} highlight={lastUpdatedField === 'vitals'} unit="BPM" />
                                <VitalField label="Temp" name="vitals.temperature" register={syncedRegister} highlight={lastUpdatedField === 'vitals'} unit="°C" />
                                <VitalField label="SPO2" name="vitals.spo2" register={syncedRegister} highlight={lastUpdatedField === 'vitals'} unit="%" />
                            </div>
                        </div>
                    </div>
                </section>

                <section className="bg-white border border-border shadow-sm rounded-[2rem] p-5 space-y-4">
                    <div className="flex items-center gap-2 text-[10px] font-bold text-primary/60 uppercase tracking-[0.2em]">
                        <CreditCard className="w-4 h-4" /> Eligibility Verification
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-[1.6fr_1px_1fr] gap-6 items-center">
                        <div>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-foreground">
                                <InputField label="Ration Card" name="ration_card_type" register={syncedRegister} highlight={lastUpdatedField === 'ration_card_type'} placeholder="Yes/No" />
                                <InputField label="Income" name="income" register={syncedRegister} highlight={lastUpdatedField === 'income'} placeholder="Per month" />
                                <InputField label="Occupation" name="occupation" register={syncedRegister} highlight={lastUpdatedField === 'occupation'} placeholder="Occupation" />
                                <InputField label="Caste" name="caste_category" register={syncedRegister} highlight={lastUpdatedField === 'caste_category'} placeholder="SC/ST/OBC" />
                                <InputField label="Housing" name="housing_type" register={syncedRegister} highlight={lastUpdatedField === 'housing_type'} placeholder="Kutcha/Pucca" />
                                <InputField label="Location" name="location" register={syncedRegister} highlight={lastUpdatedField === 'location'} placeholder="State/City" />
                            </div>
                        </div>

                        <div className="hidden md:block w-px h-[82%] bg-border self-center" />

                        <div className="flex items-center self-center">
                            <div className="w-full md:mx-auto">
                                <EligibilityStatus data={data} />
                            </div>
                        </div>
                    </div>
                </section>

                <section className="bg-white border border-border shadow-sm rounded-[2rem] p-5 space-y-4">
                    <div className="flex items-center gap-2 text-[10px] font-bold text-primary/60 uppercase tracking-[0.2em]">
                        <Activity className="w-4 h-4" /> Clinical Intelligence
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
                        <ListSection title="Symptoms" items={data.symptoms} highlight={lastUpdatedField === 'symptoms'} placeholder="No symptoms captured" />
                        <ListSection title="Medications" items={data.medications} highlight={lastUpdatedField === 'medications'} placeholder="No medications captured" />
                        <ListSection title="Procedures" items={data.procedures} highlight={lastUpdatedField === 'procedures'} placeholder="No procedures captured" />
                        <ListSection title="Allergies" items={data.allergies} highlight={lastUpdatedField === 'allergies'} placeholder="No allergies captured" />
                        <ListSection title="History" items={data.medical_history} highlight={lastUpdatedField === 'medical_history'} placeholder="No history captured" />
                        <ListSection title="Family History" items={data.family_history} highlight={lastUpdatedField === 'family_history'} placeholder="No family history captured" />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                        <InputField
                            label="Clinical Impression"
                            name="tentative_doctor_diagnosis"
                            register={syncedRegister}
                            highlight={lastUpdatedField === 'tentative_doctor_diagnosis'}
                            isTextArea
                            placeholder="Physician findings..."
                            minHeight="min-h-[72px]"
                        />
                        <InputField
                            label="Diagnostic Rationale"
                            name="initial_llm_diagnosis"
                            register={syncedRegister}
                            highlight={lastUpdatedField === 'initial_llm_diagnosis'}
                            isTextArea
                            placeholder="Observer rationale..."
                            minHeight="min-h-[72px]"
                        />
                    </div>
                </section>

                {/* ICD-10 & Billing Panel */}
                <section className="bg-white border border-border shadow-sm rounded-[2rem] p-5 space-y-4">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 text-[10px] font-bold text-primary/60 uppercase tracking-[0.2em]">
                            <Receipt className="w-4 h-4" /> ICD-10 Billing Codes
                        </div>
                        {data.billing_summary?.coding_status && (
                            <span className={`text-[10px] font-bold px-3 py-1 rounded-full border ${data.billing_summary.coding_status === 'confirmed'
                                    ? 'bg-primary/10 text-primary border-primary/20'
                                    : 'bg-accent/50 text-accent-foreground border-accent'
                                }`}>
                                {data.billing_summary.coding_status === 'confirmed' ? '✓ Confirmed' : '⚡ Auto-coded'}
                            </span>
                        )}
                    </div>

                    {(!data.icd10_codes || data.icd10_codes.length === 0) && (!data.procedure_codes || data.procedure_codes.length === 0) ? (
                        <p className="text-[11px] text-slate-400 font-mono italic py-1">
                            Billing codes auto-generate after EHR commit via background task.
                        </p>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                            {/* Diagnosis Codes */}
                            {(data.icd10_codes?.length ?? 0) > 0 && (
                                <div className="space-y-1.5">
                                    <label className="text-[9px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-1">
                                        <Stethoscope className="w-2.5 h-2.5" /> ICD-10-CM Diagnoses
                                    </label>
                                    <div className="space-y-1">
                                        {data.icd10_codes!.map((entry, i) => (
                                            <div key={i} className="flex items-center justify-between px-2 py-1 bg-blue-50 border border-blue-100 rounded-lg">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-[10px] font-bold font-mono text-blue-700">{entry.code}</span>
                                                    <span className="text-[10px] text-slate-600 truncate max-w-[180px]">{entry.description}</span>
                                                </div>
                                                <span className="text-[9px] font-mono text-slate-400 shrink-0 ml-2">{Math.round(entry.confidence * 100)}%</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Procedure Codes */}
                            {(data.procedure_codes?.length ?? 0) > 0 && (
                                <div className="space-y-1.5">
                                    <label className="text-[9px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-1">
                                        <Receipt className="w-2.5 h-2.5" /> ICD-10-PCS Procedures
                                    </label>
                                    <div className="space-y-1">
                                        {data.procedure_codes!.map((entry, i) => (
                                            <div key={i} className="flex items-center justify-between px-2 py-1 bg-violet-50 border border-violet-100 rounded-lg">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-[10px] font-bold font-mono text-violet-700">{entry.code}</span>
                                                    <span className="text-[10px] text-slate-600 truncate max-w-[180px]">{entry.description}</span>
                                                </div>
                                                <span className="text-[9px] font-mono text-slate-400 shrink-0 ml-2">{Math.round(entry.confidence * 100)}%</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {data.id && (
                        <div className="pt-2 border-t border-border">
                            <a
                                href={`/diagnostics?patient_id=${data.id}`}
                                className="text-[10px] font-bold text-primary hover:text-primary/80 transition-colors uppercase tracking-[0.2em]"
                            >
                                View &amp; Confirm Full Billing Claim →
                            </a>
                        </div>
                    )}
                </section>
            </div>
        </div>
    );
}

function EligibilityStatus({ data }: { data: PatientData }) {
    const backendData = data.scheme_eligibility;
    const requiredEligibilityFields = [
        data.age,
        data.ration_card_type,
        data.income || data.income_bracket,
        data.occupation,
        data.caste_category,
        data.housing_type,
        data.location,
    ];

    const requiredFieldsFilled = requiredEligibilityFields.every((value) => String(value ?? '').trim().length > 0);
    const isEligiblePMJAY = Boolean(backendData?.pmjay?.eligible);
    const isEligibleState = Boolean(backendData?.state_scheme?.eligible);
    const reasons = backendData?.pmjay?.reasons || [];

    return (
        <div className="bg-background border border-border shadow-inner rounded-3xl p-4 w-full min-h-[128px] space-y-3">
            <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold text-primary/80 uppercase tracking-[0.2em]">Eligibility Record</span>
                {backendData && (
                    <span className="text-[9px] font-bold text-primary bg-primary/10 px-3 py-1 border border-primary/20 rounded-xl">Verified</span>
                )}
            </div>

            {!backendData && (
                <p className="text-[11px] text-slate-500 leading-relaxed">
                    {requiredFieldsFilled
                        ? 'Awaiting scheme verification response.'
                        : 'Fill age, ration card, income, occupation, caste, housing, and location to run verification.'}
                </p>
            )}

            <div className="space-y-2">
                <StatusRow label="PM-JAY" eligible={isEligiblePMJAY} />

                {isEligiblePMJAY && reasons.length > 0 && (
                    <div className="pl-5 space-y-1">
                        {reasons.map((reason: string, index: number) => (
                            <p key={index} className="text-[10px] text-slate-500 leading-tight">• {reason}</p>
                        ))}
                    </div>
                )}

                <StatusRow label="State Health" eligible={isEligibleState} />
            </div>
        </div>
    );
}

function StatusRow({ label, eligible }: { label: string; eligible: boolean }) {
    return (
        <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
                {eligible ? <CheckCircle2 className="w-4 h-4 text-primary" /> : <Info className="w-4 h-4 text-muted-foreground/40" />}
                <span className="text-[13px] font-bold text-foreground/80">{label}</span>
            </div>
            <span className={`text-[10px] font-bold uppercase tracking-widest ${eligible ? 'text-primary' : 'text-muted-foreground'}`}>
                {eligible ? 'Eligible' : 'Pending'}
            </span>
        </div>
    );
}

const DARK_TEXT_CURSOR = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='12'%3E%3Cline x1='1' y1='0' x2='7' y2='0' stroke='black' stroke-width='1.5'/%3E%3Cline x1='4' y1='0' x2='4' y2='12' stroke='black' stroke-width='1.5'/%3E%3Cline x1='1' y1='12' x2='7' y2='12' stroke='black' stroke-width='1.5'/%3E%3C/svg%3E") 4 6, text`;

interface InputFieldProps {
    label: string;
    name: FieldPath<PatientData>;
    register: SyncedRegister;
    highlight?: boolean;
    isTextArea?: boolean;
    placeholder?: string;
    minHeight?: string;
}

function InputField({ label, name, register, highlight, isTextArea, placeholder, minHeight = 'h-[48px]' }: InputFieldProps) {
    return (
        <div className="relative group w-full flex flex-col">
            <label className="block text-[10px] font-bold text-primary/80 uppercase tracking-[0.15em] mb-1.5 px-0.5">{label}</label>
            <div className={`relative transition-all duration-500 rounded-2xl border ${highlight ? 'border-primary bg-primary/5 shadow-[0_0_30px_rgba(75,83,32,0.15)] z-10' : 'border-border bg-background hover:border-primary/30'}`}>
                {isTextArea ? (
                    <textarea
                        {...register(name)}
                        placeholder={placeholder}
                        style={{ cursor: DARK_TEXT_CURSOR }}
                        className={`w-full p-3 text-[13px] bg-transparent outline-none placeholder:text-muted-foreground/30 ${minHeight} resize-none leading-relaxed font-sans font-medium text-foreground`}
                    />
                ) : (
                    <input
                        {...register(name)}
                        placeholder={placeholder}
                        style={{ cursor: DARK_TEXT_CURSOR }}
                        className="w-full h-10 p-3 text-[13px] bg-transparent outline-none placeholder:text-muted-foreground/30 font-sans font-medium text-foreground"
                    />
                )}
            </div>
        </div>
    );
}

interface VitalFieldProps {
    label: string;
    name: FieldPath<PatientData>;
    register: SyncedRegister;
    highlight?: boolean;
    unit?: string;
}

function VitalField({ label, name, register, highlight, unit }: VitalFieldProps) {
    return (
        <div className={`p-3 rounded-2xl border transition-all duration-500 h-[72px] flex flex-col justify-between ${highlight ? 'border-primary bg-primary/5 shadow-[0_0_30px_rgba(75,83,32,0.15)] z-10' : 'border-border bg-background hover:border-primary/30'}`}>
            <label className="block text-[10px] font-bold text-primary/80 uppercase tracking-widest">{label}</label>
            <div className="flex items-end gap-1.5">
                <input
                    {...register(name)}
                    style={{ cursor: DARK_TEXT_CURSOR }}
                    className="w-full bg-transparent text-2xl font-bold font-sans outline-none text-foreground placeholder:text-muted-foreground/20 leading-none"
                    placeholder="--"
                />
                <span className="text-[10px] font-bold text-primary/40 mb-1">{unit}</span>
            </div>
        </div>
    );
}

interface ListSectionProps {
    title: string;
    items?: string[];
    highlight?: boolean;
    placeholder?: string;
}

function ListSection({ title, items, highlight, placeholder }: ListSectionProps) {
    const list = Array.isArray(items) ? items : [];

    return (
        <div className="space-y-1.5 flex flex-col h-full">
            <label className="text-[10px] font-bold text-primary/80 uppercase tracking-[0.15em]">{title}</label>
            <div className={`p-3 transition-all duration-500 min-h-[64px] rounded-2xl border flex-1 ${highlight ? 'border-primary bg-primary/5 shadow-[0_0_30px_rgba(75,83,32,0.15)] z-10' : 'border-border bg-background'}`} style={{ cursor: DARK_TEXT_CURSOR }}>
                <AnimatePresence mode="popLayout">
                    {list.length ? (
                        <div className="flex flex-col gap-2">
                            {list.map((entry: string) => (
                                <motion.div
                                    key={entry}
                                    initial={{ opacity: 0, x: -5 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    className="text-[12px] text-foreground/80 font-medium flex items-center gap-2"
                                >
                                    <span className="w-1 h-1 rounded-full bg-primary/30" />
                                    {entry}
                                </motion.div>
                            ))}
                        </div>
                    ) : (
                        <span className="text-[11px] text-muted-foreground/30 font-medium italic">{placeholder}</span>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}

