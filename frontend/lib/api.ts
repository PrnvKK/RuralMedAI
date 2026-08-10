const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8003";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8003/ws/live-consultation";

export const API = {
  BASE: API_BASE,
  EHR: `${API_BASE}/api/ehr`,
  WS: WS_BASE,
};
