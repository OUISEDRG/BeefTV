import { http } from "@/services/api/request";
import type { DreamaProject, DreamaSeries } from "@/types/dreama-workbench";

export type DreamaTask = {
    id: string; kind: string; status: string; stage?: string; pointsEstimate?: number;
    autoExecuteAt?: string | number; cancelable: boolean; projectId: number; seriesId: number;
    canvasId: string; nodeId: string; error?: string; taskId?: string; resultReady?: boolean;
};
export type DreamaStatus = { schemaVersion: number; service: string; integrationVersion: string; executionMode: string; delaySeconds: number };
const root = "/integrations/dreama/v1";

export const dreamaApi = {
    ensure: (channelId: string, signal?: AbortSignal) => http.post<DreamaStatus>(`${root}/ensure`, { channelId }, { signal, timeout: 35_000 }),
    status: (channelId: string, signal?: AbortSignal) => http.get<DreamaStatus>(`${root}/status`, { params: { channelId }, signal }),
    projects: (channelId: string, signal?: AbortSignal) => http.get<{ items: DreamaProject[] }>(`${root}/projects`, { params: { channelId }, signal }),
    series: (channelId: string, projectId: number, signal?: AbortSignal) => http.get<{ items: DreamaSeries[] }>(`${root}/series`, { params: { channelId, projectId }, signal }),
    createSeries: (channelId: string, projectId: number, name: string) => http.post<{ item: DreamaSeries }>(`${root}/series`, { channelId, projectId, name }),
    tasks: (channelId: string, canvasId: string, signal?: AbortSignal) => http.get<{ items: DreamaTask[] }>(`${root}/tasks`, { params: { channelId, canvasId }, signal }),
    cancelTask: (channelId: string, id: string) => http.post<DreamaTask>(`${root}/tasks/${encodeURIComponent(id)}/cancel`, { channelId }),
    refreshCatalog: (channelId: string, expectedRevision: number) => http.post<{ revision: number; updatedChannels: string[] }>(`${root}/catalog/refresh`, { channelId, expectedRevision }, { timeout: 35_000 }),
};
