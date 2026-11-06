const BLOCKING_OVERLAY = ".ant-modal-wrap,.ant-modal-mask,.ant-popover,.ant-dropdown,.ant-select-dropdown,.ant-drawer,[role=dialog],[data-canvas-context-menu],[data-canvas-node-panel]";

/** Reference replacement is more specific than connecting to the containing card. */
export function canvasReferenceChipAtPoint(clientX: number, clientY: number): HTMLElement | null {
    if (typeof document === "undefined") return null;
    const hit = document.elementFromPoint(clientX, clientY);
    const chip = hit?.closest<HTMLElement>("[data-reference-chip]");
    if (chip) return chip;
    const shelf = hit?.closest<HTMLElement>(".canvas-node-composer-references");
    let closest: HTMLElement | null = null;
    let distance = Number.POSITIVE_INFINITY;
    shelf?.querySelectorAll<HTMLElement>("[data-reference-chip]").forEach((candidate) => {
        const rect = candidate.getBoundingClientRect();
        const next = Math.abs(clientX - (rect.left + rect.width / 2));
        if (next < distance) { closest = candidate; distance = next; }
    });
    return closest;
}

export function canvasConnectionPointerTarget(clientX: number, clientY: number) {
    if (typeof document === "undefined") return {};
    const hit = document.elementFromPoint(clientX, clientY);
    const node = hit?.closest<HTMLElement>("[data-node-id]");
    const blocked = Boolean(hit?.closest(BLOCKING_OVERLAY)
        || (!node && hit?.closest("[data-canvas-no-zoom]"))
        || !hit?.closest("[data-canvas-viewport]"));
    return { directNodeId: blocked ? null : node?.dataset.nodeId, blocked };
}
