export type DreamaProject = { id: number; name: string; promptMode: string; aspectRatio: string };
export type DreamaSeries = { id: number; name: string; projectId: number };
export type DreamaCanvasBinding = {
    schemaVersion: 1;
    projectId: number;
    projectName: string;
    promptMode: "drama";
    aspectRatio: string;
    defaultSeriesId: number;
    revision: number;
};
export type DreamaGenerationBinding = {
    schemaVersion: 1;
    canvasId: string;
    nodeId: string;
    projectId: number;
    seriesId: number;
    promptMode: "drama";
    aspectRatio: string;
    bindingRevision: number;
};
export type DreamaGenerationPreview = {
    binding: DreamaGenerationBinding;
    model: string;
    mode: string;
    prompt: string;
    parameters: Record<string, unknown>;
    media: Array<{ kind: string; index: number; id: string; name: string }>;
    voices: Array<{ image_index: number; name: string; voice_id?: string; sample?: boolean }>;
};
