// Shape of GET /api/projects/{id}/schematic/hierarchy
// (resolved by backend/app/services/schematic_hierarchy_service.py)

export interface SchematicHierarchyNode {
    /** Instance identity: the root-relative UUID sheet-path, e.g. "/rootUuid/sheetUuid". */
    sheetPath: string;
    /** Human-readable path of sheet names, e.g. "/root/TransimpedanceAmplifier". */
    displayPath: string;
    name: string | null;
    /** The Sheetfile this instance renders (relative path), or null for the root. */
    file: string | null;
    /** Per-instance page number (string; may be non-numeric like "2A"). */
    page: string | null;
    children: SchematicHierarchyNode[];
    /** Present + true when the child .kicad_sch file could not be loaded. */
    unresolved?: boolean;
}

export interface SchematicSymbolReference {
    reference: string;
    unit: string | null;
}

export interface SchematicHierarchy {
    rootUuid: string | null;
    version: string | null;
    root: SchematicHierarchyNode;
    /** references[sheetPath][symbolUuid] -> resolved per-instance reference. */
    references: Record<string, Record<string, SchematicSymbolReference>>;
}
