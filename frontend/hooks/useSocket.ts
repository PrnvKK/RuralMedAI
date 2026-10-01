import { useState, useRef, useCallback, useEffect } from 'react';
import { API } from '@/lib/api';
import type { LiveScribeMessage } from '@/types';

export const useSocket = (onMessageReceived: (data: LiveScribeMessage) => void) => {
    const [isConnected, setIsConnected] = useState(false);
    const socketRef = useRef<WebSocket | null>(null);

    const connect = useCallback(() => {
        if (socketRef.current?.readyState === WebSocket.OPEN) return;

        const ws = new WebSocket(API.WS);

        ws.onopen = () => {
            console.log('✅ Connected to backend');
            setIsConnected(true);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data) as LiveScribeMessage;
                onMessageReceived(data);
            } catch (e) {
                console.error("Error parsing WS message:", e);
            }
        };

        ws.onclose = () => {
            console.log('❌ Disconnected from backend');
            setIsConnected(false);
        };

        socketRef.current = ws;
    }, [onMessageReceived]);

    const disconnect = useCallback(() => {
        if (socketRef.current) {
            socketRef.current.close();
            socketRef.current = null;
        }
    }, []);

    const sendMessage = useCallback((data: Record<string, unknown>) => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
            socketRef.current.send(JSON.stringify(data));
        }
    }, []);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (socketRef.current) socketRef.current.close();
        }
    }, [])

    return { isConnected, connect, disconnect, sendMessage };
};
