export type DreamaVoiceBinding = {
    version: 1;
    name: string;
    source: "catalog" | "resource";
    voiceId?: string;
    sampleResourceId?: string;
};

export type DreamaVoiceCatalogItem = {
    id: string;
    name: string;
    source: "public" | "personal";
    previewUrl: string;
    description?: string;
};
