import { useCallback, useState } from "react";

import type { InsertAssetPayload } from "@/components/canvas/asset-picker-modal";
import type { CanvasNodeData, Position } from "@/types/canvas";

type UseCanvasProjectAssetPickerOptions = {
    linkedProjectId?: string;
    refetchLinkedProject: () => Promise<unknown>;
    handleAssetsInsert: (payloads: InsertAssetPayload[]) => Promise<CanvasNodeData[]>;
    handleProjectAssetsInsert: (payloads: InsertAssetPayload[], position?: Position) => Promise<CanvasNodeData[]>;
    openAssetsAtPosition: (position?: Position) => void;
};

export function useCanvasProjectAssetPicker({
    linkedProjectId,
    refetchLinkedProject,
    handleAssetsInsert,
    handleProjectAssetsInsert,
    openAssetsAtPosition,
}: UseCanvasProjectAssetPickerOptions) {
    const [projectAssetOpen, setProjectAssetOpen] = useState(false);
    const [projectAssetInitialCategory, setProjectAssetInitialCategory] = useState("all");
    const [projectAssetInitialFolderId, setProjectAssetInitialFolderId] = useState("all");
    const [projectAssetInsertPosition, setProjectAssetInsertPosition] = useState<Position | undefined>();

    const handleLibraryAssetsInsert = useCallback(
        async (payloads: InsertAssetPayload[]) => {
            await handleAssetsInsert(payloads);
        },
        [handleAssetsInsert],
    );

    const insertProjectAssets = useCallback(
        async (payloads: InsertAssetPayload[]) => {
            await handleProjectAssetsInsert(payloads, projectAssetInsertPosition);
        },
        [handleProjectAssetsInsert, projectAssetInsertPosition],
    );

    const openProjectAssets = useCallback(
        (initialCategory = "all", position?: Position, initialFolderId = "all") => {
            setProjectAssetInitialCategory(initialCategory);
            setProjectAssetInitialFolderId(initialFolderId);
            setProjectAssetInsertPosition(position);
            setProjectAssetOpen(true);
            // 资产与项目实时同步：打开弹窗前刷新关联短剧项目资产，避免缓存导致资产列表空白/过期。
            if (linkedProjectId) void refetchLinkedProject();
        },
        [linkedProjectId, refetchLinkedProject],
    );

    const closeProjectAssets = useCallback(() => {
        setProjectAssetOpen(false);
        setProjectAssetInsertPosition(undefined);
        setProjectAssetInitialFolderId("all");
    }, []);

    return {
        projectAssetOpen,
        projectAssetInitialCategory,
        projectAssetInitialFolderId,
        projectAssetInsertPosition,
        handleLibraryAssetsInsert,
        insertProjectAssets,
        openProjectAssets,
        openCanvasAssetLibrary: openAssetsAtPosition,
        closeProjectAssets,
    };
}
