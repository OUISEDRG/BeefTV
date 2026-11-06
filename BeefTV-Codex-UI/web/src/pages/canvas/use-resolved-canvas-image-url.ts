import { useEffect, useState } from "react";
import { resolveImageEditSource } from "@/services/image-storage";
import type { CanvasNodeData } from "@/types/canvas";

export function useResolvedCanvasImageUrl(node: CanvasNodeData | null) {
    const content = node?.metadata?.content || "";
    const storageKey = node?.metadata?.storageKey || "";
    const [resolved, setResolved] = useState<{ storageKey: string; content: string; url: string }>();

    useEffect(() => {
        if (!storageKey && !content) return;
        let active = true;
        void resolveImageEditSource(storageKey, content).catch(() => "").then((url) => {
            if (active) setResolved({ storageKey, content, url });
        });
        return () => { active = false; };
    }, [content, storageKey]);

    if (!storageKey && !content) return "";
    return resolved?.storageKey === storageKey && resolved.content === content ? resolved.url : undefined;
}
