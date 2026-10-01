"use client";

import { useEffect, useRef } from 'react';

interface Props {
    isRecording: boolean;
    audioLevel?: number;
}

export function AudioVisualizer({ isRecording, audioLevel = 0 }: Props) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const levelRef = useRef(0);

    useEffect(() => {
        levelRef.current = audioLevel;
    }, [audioLevel]);

    useEffect(() => {
        if (!isRecording) {
            const canvas = canvasRef.current;
            const ctx = canvas?.getContext('2d');
            if (canvas && ctx) {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.fillStyle = 'hsla(236, 27%, 22%, 0.1)';
                ctx.fillRect(0, canvas.height / 2 - 1, canvas.width, 2);
            }
            return;
        }

        let animationFrameId: number;
        const canvas = canvasRef.current;
        const ctx = canvas?.getContext('2d');

        const draw = () => {
            if (!canvas || !ctx) return;

            ctx.clearRect(0, 0, canvas.width, canvas.height);

            const bars = 32;
            const gap = 2;
            const width = (canvas.width - ((bars - 1) * gap)) / bars;
            const level = levelRef.current;

            for (let i = 0; i < bars; i++) {
                const t = i / bars;
                const r1 = 35, g1 = 39, b1 = 71;
                const r2 = 32, g2 = 178, b2 = 170;
                const r = Math.round(r1 + (r2 - r1) * t);
                const g = Math.round(g1 + (g2 - g1) * t);
                const b = Math.round(b1 + (b2 - b1) * t);

                const flicker = 0.3 + 0.7 * Math.abs(Math.sin(Date.now() / 140 + i * 0.3));
                const height = level * canvas.height * flicker + 2;

                const x = i * (width + gap);
                const y = (canvas.height - height) / 2;

                ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.8)`;
                ctx.beginPath();
                const radius = width / 2;
                ctx.roundRect(x, y, width, Math.max(height, 1), radius);
                ctx.fill();
            }

            animationFrameId = requestAnimationFrame(draw);
        };

        draw();

        return () => {
            cancelAnimationFrame(animationFrameId);
        }
    }, [isRecording]);

    return (
        <div className="w-full bg-primary/5 rounded-md p-1 border border-primary/10">
            <canvas
                ref={canvasRef}
                width={300}
                height={24}
                className="w-full h-[24px] opacity-100"
            />
        </div>
    );
}
