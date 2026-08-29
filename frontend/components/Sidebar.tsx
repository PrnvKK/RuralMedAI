"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
    Stethoscope,
    HeartPulse,
    ClipboardList,
    ChevronRight,
    Search,
    Receipt
} from 'lucide-react';
import { motion } from 'framer-motion';

const NAV_ITEMS = [
    {
        name: 'Scribe',
        href: '/',
        icon: Stethoscope,
        description: 'AI Consultation Scribe'
    },
    {
        name: 'Insurance',
        href: '/claims',
        icon: HeartPulse,
        description: 'Claims & Eligibility'
    },
    {
        name: 'Records',
        href: '/patients',
        icon: ClipboardList,
        description: 'Patient History'
    },
    {
        name: 'Billing',
        href: '/diagnostics',
        icon: Receipt,
        description: 'ICD-10 Coding Center'
    }
];


export function Sidebar() {
    const pathname = usePathname();

    return (
        <aside className="w-[236px] flex-none border-r border-border bg-background/50 backdrop-blur-xl flex flex-col z-[100] relative">
            {/* Logo Section */}
            <div className="p-6">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-foreground leading-none">Parchee</h1>
                    <p className="text-[10px] font-bold text-primary/60 mt-2 uppercase tracking-[0.2em]">Clinical Intelligence</p>
                </div>
            </div>

            {/* Navigation */}
            <nav className="flex-1 px-3 space-y-1">
                {NAV_ITEMS.map((item) => {
                    const isActive = pathname === item.href;
                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            className={`group relative flex items-center gap-3 px-3 py-3 rounded-2xl transition-all ${isActive
                                ? 'bg-primary text-primary-foreground shadow-xl shadow-primary/20'
                                : 'text-muted-foreground hover:bg-primary/5 hover:text-primary'
                                }`}
                        >
                            <item.icon className={`w-5 h-5 ${isActive ? 'text-primary-foreground' : 'text-primary/40 group-hover:text-primary'}`} />
                            <div className="flex-1">
                                <p className="text-[13px] font-bold tracking-tight">{item.name}</p>
                                <p className={`text-[10px] font-semibold ${isActive ? 'text-primary-foreground/70' : 'text-muted-foreground/60'}`}>
                                    {item.description}
                                </p>
                            </div>
                            {isActive && (
                                <motion.div
                                    layoutId="active-indicator"
                                    className="absolute right-3"
                                >
                                    <ChevronRight className="w-3 h-3 text-slate-400" />
                                </motion.div>
                            )}
                        </Link>
                    );
                })}
            </nav>

            {/* Search Placeholder / Footer */}
            <div className="p-4 mt-auto">
                <div className="bg-primary/5 border border-primary/10 rounded-2xl p-4">
                    <div className="flex items-center gap-2 text-primary/60 mb-2">
                        <Search className="w-3.5 h-3.5" />
                        <span className="text-[10px] font-bold uppercase tracking-widest">Quick Search</span>
                    </div>
                    <div className="h-6 bg-background/50 border border-primary/10 rounded text-[9px] flex items-center px-2 text-primary/60 font-mono">
                        PRESS ⌘K TO SEARCH
                    </div>
                </div>

                <div className="mt-4 pt-4 border-t border-border flex items-center justify-between px-2">
                    <div className="flex items-center gap-2">
                        <div className="w-6 h-6 rounded-full bg-primary flex items-center justify-center text-[10px] font-bold text-white shadow-sm">PK</div>
                        <span className="text-[11px] font-bold text-foreground/80">Dr. Pranav</span>
                    </div>
                    <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)] animate-pulse" />
                </div>
            </div>
        </aside>
    );
}
