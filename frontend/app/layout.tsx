import type { Metadata } from 'next';
import { Inter, Outfit } from 'next/font/google';
import './globals.css';

const inter = Inter({
    subsets: ['latin'],
    variable: '--font-inter',
    preload: false,
});

const outfit = Outfit({
    subsets: ['latin'],
    variable: '--font-outfit',
    preload: false,
});

export const metadata: Metadata = {
    title: 'Parchee - Live Medical Scribe',
    description: 'Real-time AI scribe for medical consultations',
};

import { Sidebar } from '@/components/Sidebar';
import { ErrorBoundary } from '@/components/ErrorBoundary';

export default function RootLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <html lang="en" className="light">
            <body className={`${inter.variable} ${outfit.variable} font-sans bg-background text-foreground antialiased min-h-screen relative`}>
                <div className="fixed inset-0 z-[-1] mesh-gradient opacity-70" />
                <div className="fixed inset-0 z-[-1] bg-grid-premium opacity-25" />

                <div className="relative z-10 flex h-screen overflow-hidden">
                    <Sidebar />
                    <main className="flex-1 overflow-auto relative">
                        <ErrorBoundary>
                            {children}
                        </ErrorBoundary>
                    </main>
                </div>
            </body>
        </html>
    );
}
