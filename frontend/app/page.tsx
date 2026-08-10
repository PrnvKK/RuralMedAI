"use client";

import { useState, useCallback, useEffect, useRef } from 'react';
import { useSocket } from '@/hooks/useSocket';
import { AudioChunk, useAudioStream } from '@/hooks/useAudioStream';
import { LiveForm } from '@/components/LiveForm';
import { AudioVisualizer } from '@/components/AudioVisualizer';
import { PatientData, TranscriptItem } from '@/types';
import { Mic, Square, Save, RefreshCw, FileText, Eraser, Clock3, Plus, Download } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import Link from 'next/link';
import { clearScribeSession, loadScribeSession, saveScribeSession } from '@/lib/sessionStore';
import { API } from '@/lib/api';

const SEND_INTERVAL_MS = Number(process.env.NEXT_PUBLIC_PARCHEE_SEND_INTERVAL_MS || 500);

function nowClock() {
    return new Date().toLocaleTimeString([], {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function makeId(prefix: string) {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

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

function applyPatientUpdate(prev: PatientData, field: string, value: unknown): PatientData {
    const next: PatientData = { ...prev };

    if (field.startsWith('vitals.')) {
        const vitalField = field.split('.')[1] as keyof NonNullable<PatientData['vitals']>;
        if (['temperature', 'blood_pressure', 'pulse', 'spo2'].includes(vitalField)) {
            next.vitals = { ...(next.vitals || {}), [vitalField]: value as string };
            return next;
        }
    }

    if (['temperature', 'blood_pressure', 'pulse', 'spo2'].includes(field)) {
        next.vitals = { ...(next.vitals || {}), [field]: value as string };
        return next;
    }

    (next as Record<string, unknown>)[field] = value;
    return next;
}

function hasMeaningfulPatientData(data: PatientData): boolean {
    return Object.entries(data).some(([key, value]) => {
        if (key === 'id') return false;
        if (value === null || value === undefined) return false;
        if (typeof value === 'string') return value.trim() !== '';
        if (Array.isArray(value)) return value.length > 0;
        if (typeof value === 'object') {
            return Object.values(value as Record<string, unknown>).some((item) => {
                if (item === null || item === undefined) return false;
                return String(item).trim() !== '';
            });
        }
        return true;
    });
}

export default function Home() {
    const [patientData, setPatientData] = useState<PatientData>({});
    const patientDataRef = useRef<PatientData>(patientData);
    patientDataRef.current = patientData;
    const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
    const [activePatientId, setActivePatientId] = useState<number | null>(null);

    // Resume Logic
    const [searchParams, setSearchParams] = useState<URLSearchParams | null>(null);
    useEffect(() => {
        setSearchParams(new URLSearchParams(window.location.search));
    }, []);

    useEffect(() => {
        const patientId = searchParams?.get('patient_id');
        if (patientId) {
            console.log(`Resuming session for patient ${patientId}...`);
            fetch(`${API.BASE}/api/ehr/patients/${patientId}`)
                .then(res => res.json())
                .then(data => {
                    console.log("Loaded patient data:", data);
                    setPatientData(data);

                    // Add Summary to Transcript
                    if (data.transcript_summary) {
                        const now = new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
                        setTranscript(prev => [...prev, {
                            id: 'sys-resume-' + Date.now(),
                            type: 'text',
                            content: `**RESUMED SESSION**\n\nIMPORTANT POINTS:\n${data.transcript_summary}`,
                            timestamp: now
                        }]);
                    }
                })
                .catch(err => console.error("Failed to load patient", err));
        }
    }, [searchParams]);
    const [entryMode, setEntryMode] = useState<'create' | 'update'>('create');
    const [formInstanceKey, setFormInstanceKey] = useState(0);
    const [sessionHydrated, setSessionHydrated] = useState(false);
    const [sessionSyncedAt, setSessionSyncedAt] = useState<string | null>(null);

    const [audioDevices, setAudioDevices] = useState<MediaDeviceInfo[]>([]);
    const [selectedDeviceId, setSelectedDeviceId] = useState<string>('');
    const [isCommiting, setIsCommiting] = useState(false);
    const [isExportingFhir, setIsExportingFhir] = useState(false);
    const [stopRequested, setStopRequested] = useState(false);

    const scrollRef = useRef<HTMLDivElement>(null);
    const audioBufferRef = useRef<AudioChunk[]>([]);
    const disconnectRef = useRef<() => void>(() => { });
    const stoppingRef = useRef(false);

    useEffect(() => {
        const snapshot = loadScribeSession();
        if (snapshot) {
            const restoredId = typeof snapshot.activePatientId === 'number'
                ? snapshot.activePatientId
                : typeof snapshot.patientData?.id === 'number'
                    ? snapshot.patientData.id
                    : null;
            setActivePatientId(restoredId);
            setEntryMode(snapshot.entryMode || (restoredId ? 'update' : 'create'));
            setPatientData(snapshot.patientData || {});
            setTranscript(Array.isArray(snapshot.transcript) ? snapshot.transcript : []);
            setSessionSyncedAt(new Date(snapshot.updatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }));
        }
        setSessionHydrated(true);
    }, []);

    useEffect(() => {
        if (!sessionHydrated) return;

        const updatedAt = new Date().toISOString();
        saveScribeSession({
            patientData: activePatientId ? { ...patientData, id: activePatientId } : patientData,
            transcript: transcript.slice(-400),
            activePatientId,
            entryMode,
            updatedAt
        });
        setSessionSyncedAt(nowClock());
    }, [patientData, transcript, activePatientId, entryMode, sessionHydrated]);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [transcript]);

    const handleMessage = useCallback((data: any) => {
        const ts = nowClock();

        if (data.type === 'session_complete') {
            disconnectRef.current();
            setStopRequested(false);
            stoppingRef.current = false;
            return;
        }

        if (data.type === 'update' && data.field) {
            setPatientData(prev => applyPatientUpdate(prev, data.field, data.value));
            setTranscript(prev => ([
                ...prev,
                {
                    id: makeId('tool'),
                    type: 'tool',
                    toolInfo: { field: String(data.field), value: data.value },
                    timestamp: ts
                }
            ]));
            return;
        }

        if (data.type === 'content' && data.text) {
            setTranscript(prev => {
                const lastItem = prev[prev.length - 1];
                if (lastItem && lastItem.type === 'text') {
                    return [
                        ...prev.slice(0, -1),
                        { ...lastItem, content: `${lastItem.content || ''}${data.text}` }
                    ];
                }

                return [
                    ...prev,
                    {
                        id: makeId('txt'),
                        type: 'text',
                        content: data.text,
                        timestamp: ts
                    }
                ];
            });
        }
    }, []);

    const { isConnected, connect, disconnect, sendMessage } = useSocket(handleMessage);
    disconnectRef.current = disconnect;

    const flushAudioBuffer = useCallback((final = false) => {
        if (!isConnected || audioBufferRef.current.length === 0) {
            if (final && isConnected) {
                sendMessage({ type: 'end_session' });
            }
            return;
        }

        const chunks = audioBufferRef.current.map(chunk => ({
            mimeType: 'audio/pcm',
            data: chunk.data,
            rms: chunk.rms,
            durationMs: chunk.durationMs,
        }));
        audioBufferRef.current = [];

        sendMessage({ realtimeInput: { mediaChunks: chunks } });
        if (final) {
            sendMessage({ type: 'end_session' });
        }
    }, [isConnected, sendMessage]);

    const onAudioChunk = useCallback((chunk: AudioChunk) => {
        if (isConnected) {
            audioBufferRef.current.push(chunk);
        }
    }, [isConnected]);

    const { isRecording, startRecording, stopRecording, getAudioDevices, audioLevel = 0 } = useAudioStream(onAudioChunk);

    useEffect(() => {
        getAudioDevices().then(devices => {
            setAudioDevices(devices);
            if (devices.length > 0) {
                const defaultDevice = devices.find(d => d.deviceId === 'default');
                setSelectedDeviceId(defaultDevice ? defaultDevice.deviceId : devices[0].deviceId);
            }
        });
    }, [getAudioDevices]);

    useEffect(() => {
        if (isConnected && !isRecording && !stopRequested && !stoppingRef.current) {
            startRecording(selectedDeviceId);
        }
    }, [isConnected, isRecording, startRecording, selectedDeviceId, stopRequested]);

    useEffect(() => {
        if (!isConnected || !isRecording) return;
        const interval = window.setInterval(() => flushAudioBuffer(false), SEND_INTERVAL_MS);
        return () => window.clearInterval(interval);
    }, [flushAudioBuffer, isConnected, isRecording]);

    const handleStart = () => {
        audioBufferRef.current = [];
        setStopRequested(false);
        stoppingRef.current = false;
        connect();
    };

    const handleStop = () => {
        stoppingRef.current = true;
        setStopRequested(true);
        stopRecording();
        window.setTimeout(() => flushAudioBuffer(true), 150);
    };

    const handleResetSession = () => {
        stopRecording();
        disconnect();
        setStopRequested(false);
        stoppingRef.current = false;
        audioBufferRef.current = [];
        setActivePatientId(null);
        setEntryMode('create');
        setPatientData({});
        setFormInstanceKey((prev) => prev + 1);
        setTranscript([]);
        clearScribeSession();
        setSessionSyncedAt(null);
    };

    const handleStartNewEntry = async () => {
        let nextId = 1;

        try {
            const response = await fetch(`${API.EHR}/patients`);
            if (response.ok) {
                const data = await response.json();
                if (Array.isArray(data) && data.length > 0) {
                    const maxId = data.reduce((max: number, item: unknown) => {
                        const rawId = typeof item === 'object' && item !== null ? (item as { id?: unknown }).id : null;
                        const parsed = Number.parseInt(String(rawId ?? ''), 10);
                        if (Number.isNaN(parsed)) return max;
                        return Math.max(max, parsed);
                    }, 0);
                    nextId = maxId + 1;
                }
            }
        } catch (error) {
            console.error('Failed to fetch latest patient id:', error);
        }

        setActivePatientId(nextId);
        setEntryMode('create');
        setPatientData({ id: nextId });
        setFormInstanceKey((prev) => prev + 1);
        setTranscript([
            {
                id: makeId('sys-new'),
                type: 'text',
                content: `System: Started a new patient entry (ID: ${nextId}).`,
                timestamp: nowClock(),
            }
        ]);
    };

    const handlePatientIdChange = (raw: string) => {
        const normalized = raw.replace(/[^\d]/g, '');
        if (!normalized) {
            setActivePatientId(null);
            setEntryMode('create');
            setPatientData(prev => {
                if (prev.id === undefined) return prev;
                const { id, ...rest } = prev;
                return rest;
            });
            return;
        }

        const parsed = Number.parseInt(normalized, 10);
        if (Number.isNaN(parsed) || parsed <= 0) return;
        setActivePatientId(parsed);
        setEntryMode('update');
        setPatientData(prev => ({ ...prev, id: parsed }));
    };

    const handleCommit = async () => {
        const currentData = patientDataRef.current;
        if (!hasMeaningfulPatientData(currentData)) {
            alert('No patient data to commit.');
            return;
        }

        setIsCommiting(true);
        const ts = nowClock();
        const isUpdate = entryMode === 'update' && activePatientId !== null;
        setTranscript(prev => [...prev, {
            id: makeId('sys'),
            type: 'text',
            content: isUpdate
                ? `System: Updating EHR record ${activePatientId}...`
                : 'System: Creating a new EHR record...',
            timestamp: ts
        }]);

        try {
            const payload: PatientData = isUpdate && activePatientId
                ? { ...currentData, id: activePatientId }
                : (() => {
                    const { id, ...rest } = currentData;
                    return rest;
                })();
            const updateEndpoint = activePatientId ? `${API.EHR}/patients/${activePatientId}` : '';
            const createEndpoint = `${API.EHR}/commit`;
            const createPayload = (() => {
                const { id, ...rest } = payload;
                return rest;
            })();

            let response = await fetch(isUpdate ? updateEndpoint : createEndpoint, {
                method: isUpdate ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(isUpdate ? payload : createPayload),
            });
            let result = await response.json();

            const shouldFallbackToCreate = isUpdate && !response.ok && (
                response.status === 404 ||
                String(result?.detail || '').toLowerCase().includes('not found')
            );

            if (shouldFallbackToCreate) {
                setTranscript(prev => [...prev, {
                    id: makeId('sys-fallback'),
                    type: 'text',
                    content: `System: Record ${activePatientId} not found. Creating a new record instead...`,
                    timestamp: ts
                }]);
                response = await fetch(createEndpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(createPayload),
                });
                result = await response.json();
            }

            if (response.ok) {
                const savedId = Number.parseInt(String(result.patient_id ?? activePatientId ?? ''), 10);
                if (!Number.isNaN(savedId)) {
                    setActivePatientId(savedId);
                    setEntryMode('update');
                    setPatientData(prev => ({ ...prev, id: savedId }));
                }
                setTranscript(prev => [...prev, {
                    id: makeId('sys-success'),
                    type: 'text',
                    content: `System: Success! Record ${isUpdate ? 'updated' : 'saved'} (ID: ${result.patient_id ?? activePatientId})`,
                    timestamp: ts
                }]);
                alert(`Successfully ${isUpdate ? 'updated' : 'saved'} EHR record. Patient ID: ${result.patient_id ?? activePatientId}`);
            } else {
                throw new Error(result.detail || 'Failed to commit');
            }
        } catch (error) {
            console.error(error);
            setTranscript(prev => [...prev, { id: makeId('sys-error'), type: 'text', content: 'System Error: EHR Commit Failed', timestamp: ts }]);
            alert('Failed to commit to EHR. Check backend connection.');
        } finally {
            setIsCommiting(false);
        }
    };

    const handleExportFhir = async () => {
        const currentData = patientDataRef.current;
        if (!hasMeaningfulPatientData(currentData)) {
            alert('No patient data to export.');
            return;
        }

        setIsExportingFhir(true);
        try {
            const endpoint = activePatientId
                ? `${API.EHR}/patients/${activePatientId}/fhir`
                : `${API.EHR}/fhir/export`;
            const response = await fetch(endpoint, activePatientId
                ? undefined
                : {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(currentData),
                });

            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || 'FHIR export failed');
            }

            const bundle = await response.json();
            const patientLabel = activePatientId || currentData.id || 'draft';
            downloadJson(`parchee-fhir-${patientLabel}.json`, bundle);
            setTranscript(prev => [...prev, {
                id: makeId('sys-fhir'),
                type: 'text',
                content: `System: Exported FHIR R4 bundle (${bundle.entry?.length ?? 0} resources).`,
                timestamp: nowClock()
            }]);
        } catch (error) {
            console.error(error);
            alert('Failed to export FHIR bundle. Check backend connection.');
        } finally {
            setIsExportingFhir(false);
        }
    };

    return (
        <main className="h-screen flex flex-col bg-background text-foreground overflow-hidden text-[13px]">
            {/* Scribe Toolbar */}
            <div className="flex-none bg-white/80 backdrop-blur-xl border-b border-border px-6 py-2.5 flex items-center justify-center z-50">
                <div className="w-full max-w-5xl flex items-center gap-4 bg-secondary/50 p-1 rounded-2xl border border-border shadow-sm">
                    <div className="flex items-center gap-2.5 px-4 py-1.5 bg-white rounded-xl shadow-sm border border-border/50 ml-0.5">
                        <span className="text-[9px] font-black text-primary/40 uppercase tracking-widest text-nowrap">Source</span>
                        <select
                            className="bg-transparent text-[11px] font-bold text-foreground focus:outline-none cursor-pointer max-w-[220px] truncate"
                            value={selectedDeviceId}
                            onChange={(e) => setSelectedDeviceId(e.target.value)}
                            disabled={isRecording}
                        >
                            {audioDevices.map(device => (
                                <option key={device.deviceId} value={device.deviceId} className="font-sans">
                                    {device.label || `Mic ${device.deviceId.slice(0, 5)}...`}
                                </option>
                            ))}
                        </select>
                    </div>

                    <div className="h-5 w-px bg-border/50" />

                    <div className="flex-1 px-4">
                        <AudioVisualizer isRecording={isRecording} audioLevel={audioLevel} />
                    </div>

                    <div className="h-5 w-px bg-border/50" />

                    <div className="flex items-center gap-2 px-1">
                        <div className="flex items-center gap-1.5">
                            <span className="text-[9px] font-bold text-primary/40 uppercase tracking-widest pl-1">Patient</span>
                            <input
                                type="text"
                                inputMode="numeric"
                                value={activePatientId ? String(activePatientId) : ''}
                                onChange={(e) => handlePatientIdChange(e.target.value)}
                                placeholder="NEW"
                                className="w-20 h-8 px-3 rounded-xl bg-white text-[11px] font-bold border border-border/50 outline-none focus:border-primary/30 transition-all text-center"
                            />
                            <button
                                onClick={handleStartNewEntry}
                                className="h-8 w-8 flex items-center justify-center rounded-xl border border-primary/20 bg-primary/5 text-primary hover:bg-primary/10 transition-all shadow-sm"
                            >
                                <Plus className="w-4 h-4" />
                            </button>
                        </div>
                    </div>

                    <div className="h-5 w-px bg-border/50" />

                    <button
                        onClick={handleExportFhir}
                        disabled={isExportingFhir}
                        className="flex items-center gap-2 px-3 py-2 bg-white text-primary rounded-xl border border-primary/15 text-[10px] font-bold hover:bg-primary/5 transition-all disabled:opacity-50 uppercase tracking-widest"
                        title="Export current encounter as FHIR R4 JSON"
                    >
                        {isExportingFhir ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                        FHIR
                    </button>

                    <div className="h-5 w-px bg-border/50" />

                    {!isRecording ? (
                        <button
                            onClick={handleStart}
                            className="flex items-center gap-2 px-6 py-2.5 bg-primary text-white rounded-xl text-[11px] font-bold hover:bg-primary/90 transition-all shadow-lg shadow-primary/20 ml-1"
                        >
                            <Mic className="w-3.5 h-3.5" /> START SESSION
                        </button>
                    ) : (
                        <button
                            onClick={handleStop}
                            className="flex items-center gap-2 px-6 py-2.5 bg-rose-600 text-white rounded-xl text-[11px] font-bold hover:bg-rose-700 transition-all animate-pulse shadow-lg shadow-rose-200 ml-1"
                        >
                            <Square className="w-3.5 h-3.5 fill-current" /> STOP SESSION
                        </button>
                    )}
                </div>
            </div>

            <div className="flex-1 flex overflow-hidden bg-background">
                <div className="flex-1 flex flex-col min-w-0 border-r border-border relative bg-background">
                    {/* Form Header REMOVED */}

                    {/* Scrollable Form Content */}
                    <div className="flex-1 overflow-y-auto p-2">
                        <div className="max-w-7xl mx-auto h-full">
                            <LiveForm
                                key={formInstanceKey}
                                data={patientData}
                                onFieldChange={(field, value) => {
                                    setPatientData(prev => applyPatientUpdate(prev, field, value));
                                }}
                            />
                        </div>
                    </div>
                </div>

                <div className="w-[320px] xl:w-[380px] flex flex-col bg-white border-l border-border">
                    <div className="flex-none p-4 border-b border-border space-y-3 bg-white">
                        <button
                            onClick={handleCommit}
                            disabled={isCommiting}
                            className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary text-white hover:bg-primary/90 rounded-xl font-bold transition-all disabled:opacity-50 uppercase tracking-[0.1em] text-[10px] shadow-lg shadow-primary/10"
                        >
                            {isCommiting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                            {entryMode === 'update' && activePatientId ? 'Update EHR Record' : 'Commit to EHR'}
                        </button>

                        <div className="grid grid-cols-2 gap-2">
                            <Link
                                href="/patients"
                                className="flex items-center justify-center gap-2 py-2.5 rounded-xl bg-secondary border border-border hover:bg-secondary/80 text-foreground font-bold transition-all text-[9px] uppercase tracking-widest"
                            >
                                <FileText className="w-3.5 h-3.5 text-primary/80" /> Archive
                            </Link>
                            <button
                                onClick={handleResetSession}
                                className="flex items-center justify-center gap-2 py-2.5 rounded-xl bg-rose-50 border border-rose-100 hover:bg-rose-100 text-rose-700 font-bold transition-all text-[9px] uppercase tracking-widest"
                            >
                                <Eraser className="w-3.5 h-3.5" /> Reset
                            </button>
                        </div>
                    </div>

                    <div className="flex-none px-4 py-3 border-b border-border flex items-center justify-between bg-background/50">
                        <h3 className="text-[10px] font-bold uppercase tracking-[0.2em] text-primary/80">Session Stream</h3>
                        <div className="flex items-center gap-2 text-[9px] text-muted-foreground font-bold tracking-tighter">
                            <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-primary shadow-[0_0_8px_rgba(75,83,32,0.5)]' : 'bg-border'}`} />
                            {isConnected ? (stopRequested ? 'FINALIZING' : 'STREAMING') : 'OFFLINE'}
                        </div>
                    </div>

                    <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-hide">
                        {transcript.length === 0 && (
                            <div className="h-full flex flex-col items-center justify-center text-center p-6 opacity-30">
                                <div className="w-12 h-12 rounded-full border-2 border-dashed border-primary/20 flex items-center justify-center mb-4">
                                    <Clock3 className="w-6 h-6 text-primary/20" />
                                </div>
                                <p className="text-[9px] font-bold uppercase tracking-[0.4em] text-primary/40">Awaiting Signal...</p>
                            </div>
                        )}

                        <AnimatePresence initial={false}>
                            {transcript.map((item) => (
                                <motion.div
                                    key={item.id}
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    className="text-[12px]"
                                >
                                    {item.type === 'text' ? (
                                        <div className="group relative space-y-2">
                                            <p className="text-foreground/80 whitespace-pre-wrap leading-relaxed font-sans font-medium text-[12px]">{item.content}</p>
                                            <div className="flex items-center gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
                                                <div className="h-px flex-1 bg-border/50" />
                                                <span className="text-[8px] text-muted-foreground font-bold uppercase tracking-widest">{item.timestamp}</span>
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="my-3 p-4 rounded-2xl bg-primary/5 border border-primary/10 space-y-2 shadow-sm">
                                            <div className="flex items-center justify-between">
                                                <span className="text-[9px] font-bold text-primary uppercase tracking-[0.1em]">{item.toolInfo?.field}</span>
                                                <span className="text-[8px] text-primary/40 font-bold uppercase tracking-widest">{item.timestamp}</span>
                                            </div>
                                            <p className="text-[11px] font-medium text-foreground/70 truncate bg-white/50 p-2 rounded-xl border border-primary/5">
                                                {typeof item.toolInfo?.value === 'string' ? item.toolInfo.value : JSON.stringify(item.toolInfo?.value)}
                                            </p>
                                        </div>
                                    )}
                                </motion.div>
                            ))}
                        </AnimatePresence>
                    </div>

                    <div className="flex-none p-4 border-t border-border bg-secondary/50 text-[9px] tracking-[0.4em] uppercase font-black text-center text-primary/30">
                        SESSION SECURE • END-TO-END
                    </div>
                </div>
            </div>
        </main>
    );
}
