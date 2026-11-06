import { CanvasNodeUpscaleDialog, type CanvasImageUpscaleParams } from "@/components/canvas/canvas-node-upscale-dialog";
import type { CanvasNodeData } from "@/types/canvas";
import type { AiConfig } from "@/stores/use-config-store";
import { useResolvedCanvasImageUrl } from "./use-resolved-canvas-image-url";

type CanvasProjectMediaDialogsProps = {
    upscaleNode: CanvasNodeData | null;
    onCloseUpscale: () => void;
    onUpscale: (node: CanvasNodeData, params: CanvasImageUpscaleParams) => void;
    config: AiConfig;
};

export function CanvasProjectMediaDialogs({
    upscaleNode,
    onCloseUpscale,
    onUpscale,
    config,
}: CanvasProjectMediaDialogsProps) {
    const upscaleImageUrl = useResolvedCanvasImageUrl(upscaleNode);

    return (
        <>
            {upscaleNode && upscaleImageUrl ? <CanvasNodeUpscaleDialog dataUrl={upscaleImageUrl} open onClose={onCloseUpscale} onConfirm={(params) => onUpscale(upscaleNode, params)} /> : null}
        </>
    );
}
