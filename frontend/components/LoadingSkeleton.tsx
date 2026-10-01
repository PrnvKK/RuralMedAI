export function Skeleton({ className }: { className?: string }) {
    return (
        <div className={`animate-pulse bg-slate-100 rounded-xl ${className || ''}`} />
    );
}

export function PageSkeleton() {
    return (
        <main className="h-screen flex flex-col bg-background overflow-hidden">
            <div className="flex-none bg-white/80 border-b border-border px-6 py-2.5">
                <div className="w-full max-w-5xl mx-auto flex items-center gap-4">
                    <Skeleton className="h-10 w-64" />
                    <Skeleton className="h-10 flex-1" />
                    <Skeleton className="h-10 w-40" />
                </div>
            </div>
            <div className="flex-1 flex">
                <div className="flex-1 p-4 space-y-3">
                    <Skeleton className="h-12 w-48" />
                    <Skeleton className="h-24 w-full" />
                    <Skeleton className="h-24 w-full" />
                    <Skeleton className="h-24 w-full" />
                </div>
                <div className="w-[320px] p-4 space-y-3 border-l border-border">
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-24 w-full" />
                    <Skeleton className="h-24 w-full" />
                </div>
            </div>
        </main>
    );
}
