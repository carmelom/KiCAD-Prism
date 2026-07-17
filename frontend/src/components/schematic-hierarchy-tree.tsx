import { useState } from "react";
import { ChevronDown, ChevronRight, FileText, AlertTriangle } from "lucide-react";
import type { SchematicHierarchyNode } from "@/types/schematic-hierarchy";

interface SchematicHierarchyTreeProps {
    root: SchematicHierarchyNode;
    activeSheetPath: string;
    onSelect: (node: SchematicHierarchyNode) => void;
}

interface TreeNodeProps {
    node: SchematicHierarchyNode;
    depth: number;
    activeSheetPath: string;
    onSelect: (node: SchematicHierarchyNode) => void;
}

function TreeNode({ node, depth, activeSheetPath, onSelect }: TreeNodeProps) {
    const [expanded, setExpanded] = useState(true);
    const hasChildren = node.children.length > 0;
    const isActive = node.sheetPath === activeSheetPath;
    const label = node.name || node.file || "(unnamed)";

    return (
        <div>
            <div
                role="treeitem"
                aria-selected={isActive}
                title={node.displayPath}
                onClick={() => onSelect(node)}
                onDoubleClick={() => onSelect(node)}
                className={`flex items-center gap-1 rounded-none px-1 py-0.5 cursor-pointer select-none ${isActive ? "bg-primary/15 text-primary font-medium" : "hover:bg-muted"
                    }`}
                style={{ paddingLeft: depth * 12 + 4 }}
            >
                {hasChildren ? (
                    <button
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            setExpanded((v) => !v);
                        }}
                        className="shrink-0 text-muted-foreground hover:text-foreground"
                        aria-label={expanded ? "Collapse" : "Expand"}
                    >
                        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    </button>
                ) : (
                    <span className="w-3 shrink-0" />
                )}
                {node.unresolved ? (
                    <AlertTriangle className="h-3 w-3 shrink-0 text-amber-500" />
                ) : (
                    <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                )}
                <span className="flex-1 truncate">{label}</span>
                {node.page && (
                    <span className="shrink-0 text-[10px] text-muted-foreground">page {node.page}</span>
                )}
            </div>
            {hasChildren && expanded && (
                <div role="group">
                    {node.children.map((child) => (
                        <TreeNode
                            key={child.sheetPath}
                            node={child}
                            depth={depth + 1}
                            activeSheetPath={activeSheetPath}
                            onSelect={onSelect}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

export function SchematicHierarchyTree({ root, activeSheetPath, onSelect }: SchematicHierarchyTreeProps) {
    return (
        <div role="tree" className="text-xs">
            <TreeNode node={root} depth={0} activeSheetPath={activeSheetPath} onSelect={onSelect} />
        </div>
    );
}
