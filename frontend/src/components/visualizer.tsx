import { lazy, Suspense, useEffect, useState, useCallback, useRef, useLayoutEffect, useMemo } from "react";
import { Cpu, Box, FileText, MessageSquarePlus, MessageSquare, GitBranch, CircuitBoard, Link2, Copy, Check, ArrowLeft, ArrowUp, ArrowRight, ListTree, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CommentOverlay } from "./comment-overlay";
import { CommentForm } from "./comment-form";
import { CommentPanel } from "./comment-panel";
import { SchematicHierarchyTree } from "./schematic-hierarchy-tree";
import { useSchematicHierarchy } from "@/hooks/use-schematic-hierarchy";
import { fetchApi } from "@/lib/api";
import type { User } from "@/types/auth";
import type { Comment, CommentContext } from "@/types/comments";
import type { SchematicHierarchyNode } from "@/types/schematic-hierarchy";
import type {
    CrossProbeContext,
    ECadViewerElement,
    KiCanvasSelectDetail,
} from "@/types/ecad-viewer";

const Model3DViewer = lazy(() =>
    import("./model-3d-viewer").then((module) => ({ default: module.Model3DViewer }))
);

interface VisualizerProps {
    projectId: string;
    user: User | null;
    commit?: string | null;
}

type VisualizerTab = "sch" | "pcb" | "3d" | "ibom";

interface CommentsSourceUrls {
    project_id: string;
    project_name: string;
    base_url: string;
    list_url: string;
    patch_url_template: string;
    reply_url_template: string;
    delete_url_template: string;
}

const isAbortError = (error: unknown): boolean =>
    error instanceof DOMException && error.name === "AbortError";

const CROSS_PROBE_MAX_RETRIES = 12;
const CROSS_PROBE_RETRY_DELAY_MS = 120;

type ViewerBlobSource = {
    filename: string;
    content: string;
};

const buildViewerKey = (
    kind: "schematic" | "pcb",
    projectId: string,
    commit: string | null | undefined,
    sources: ViewerBlobSource[],
) => {
    const signature = sources
        .map(({ filename, content }) => `${filename}:${content.length}`)
        .join("|");
    return `${kind}:${projectId}:${commit ?? "latest"}:${signature}`;
};

type EcadViewerHostProps = {
    viewerKey: string;
    sources: ViewerBlobSource[];
    setViewerRef: (node: ECadViewerElement | null) => void;
};

function EcadViewerHost({ viewerKey, sources, setViewerRef }: EcadViewerHostProps) {
    const hostRef = useRef<ECadViewerElement | null>(null);

    const attachViewerRef = useCallback((node: ECadViewerElement | null) => {
        hostRef.current = node;
        setViewerRef(node);
    }, [setViewerRef]);

    useLayoutEffect(() => {
        const viewer = hostRef.current;
        if (!viewer || sources.length === 0) return;

        let cancelled = false;

        const hydrateViewer = async () => {
            await customElements.whenDefined("ecad-blob");
            if (cancelled || !hostRef.current) return;

            const activeViewer = hostRef.current;
            activeViewer.querySelectorAll("ecad-blob").forEach((blob) => blob.remove());

            for (const source of sources) {
                const blob = document.createElement("ecad-blob") as HTMLElement & {
                    filename?: string;
                    content?: string;
                };
                blob.filename = source.filename;
                blob.content = source.content;
                activeViewer.appendChild(blob);
            }

            const viewerWithLoader = activeViewer as ECadViewerElement & {
                load_src?: () => Promise<void> | void;
            };
            if (typeof viewerWithLoader.load_src === "function") {
                await viewerWithLoader.load_src();
            }
        };

        void hydrateViewer();

        return () => {
            cancelled = true;
        };
    }, [sources, viewerKey]);

    return (
        <ecad-viewer
            ref={attachViewerRef}
            style={{ width: "100%", height: "100%" }}
            show-header="true"
            header-sections="beginning,end"
            key={viewerKey}
        />
    );
}

export function Visualizer({ projectId, user, commit }: VisualizerProps) {
    const [schematicViewerElement, setSchematicViewerElement] = useState<ECadViewerElement | null>(null);
    const [pcbViewerElement, setPcbViewerElement] = useState<ECadViewerElement | null>(null);
    const schematicViewerRef = useRef<ECadViewerElement | null>(null);
    const pcbViewerRef = useRef<ECadViewerElement | null>(null);

    // Callback refs to sync state and refs
    const setSchematicViewerRef = useCallback((node: ECadViewerElement | null) => {
        schematicViewerRef.current = node;
        setSchematicViewerElement(node);
    }, []);

    const setPcbViewerRef = useCallback((node: ECadViewerElement | null) => {
        pcbViewerRef.current = node;
        setPcbViewerElement(node);
    }, []);

    const [activeTab, setActiveTab] = useState<VisualizerTab>("sch");
    // One pre-annotated .kicad_sch blob per sheet instance (from /schematic/flattened):
    // this is what makes the viewer render correct per-instance reference designators.
    const [schematicBlobs, setSchematicBlobs] = useState<
        { filename: string; sheetPath: string; content: string; isRoot?: boolean }[]
    >([]);
    const [pcbContent, setPcbContent] = useState<string | null>(null);
    const [modelUrl, setModelUrl] = useState<string | null>(null);
    const [ibomUrl, setIbomUrl] = useState<string | null>(null);
    const [schematicContentLoaded, setSchematicContentLoaded] = useState(false);
    const [pcbContentLoaded, setPcbContentLoaded] = useState(false);
    const [loading, setLoading] = useState(true);

    const [comments, setComments] = useState<Comment[]>([]);
    const [activePage, setActivePage] = useState<string>("root.kicad_sch");
    const [commentMode, setCommentMode] = useState(false);
    const [showCommentForm, setShowCommentForm] = useState(false);
    const [showCommentPanel, setShowCommentPanel] = useState(false);
    const [pendingLocation, setPendingLocation] = useState<{ x: number, y: number, layer: string } | null>(null);
    const [pendingContext, setPendingContext] = useState<CommentContext>("PCB");
    const [isSubmittingComment, setIsSubmittingComment] = useState(false);
    const [isPushingComments, setIsPushingComments] = useState(false);
    const [pushMessage, setPushMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
    const [showPushDialog, setShowPushDialog] = useState(false);
    const [commentsSourceUrls, setCommentsSourceUrls] = useState<CommentsSourceUrls | null>(null);
    const [isUrlsPopoverOpen, setIsUrlsPopoverOpen] = useState(false);
    const [copiedField, setCopiedField] = useState<string | null>(null);
    const canModifyComments = user?.role === "admin" || user?.role === "designer";
    const lastCrossProbeRef = useRef<Record<CrossProbeContext, string | null>>({
        SCH: null,
        PCB: null,
    });
    const crossProbeRetryTimerRef = useRef<Record<CrossProbeContext, number | null>>({
        SCH: null,
        PCB: null,
    });
    const crossProbeRunIdRef = useRef<Record<CrossProbeContext, number>>({
        SCH: 0,
        PCB: 0,
    });
    // "Open datasheet" feature: the datasheet URL of the currently-selected
    // schematic symbol (null if the selection has none or isn't a symbol), plus
    // when that selection last fired. The double-click trigger uses the timestamp
    // as a freshness guard (see the datasheet effect below).
    const selectedDatasheetUrlRef = useRef<string | null>(null);
    const lastSchematicSelectAtRef = useRef<number>(0);
    const activeCommentContext: CommentContext | null = activeTab === "sch" ? "SCH" : activeTab === "pcb" ? "PCB" : null;

    const applyCommentModeToViewer = useCallback((viewer: ECadViewerElement | null, enabled: boolean) => {
        if (!viewer) return;
        if (viewer.setCommentMode) {
            viewer.setCommentMode(enabled);
            return;
        }

        if (enabled) {
            viewer.setAttribute("comment-mode", "true");
        } else {
            viewer.removeAttribute("comment-mode");
        }
    }, []);

    const normalizeDesignator = useCallback((value: unknown): string | null => {
        if (typeof value !== "string") return null;
        const trimmed = value.trim();
        if (!trimmed) return null;
        return /^[A-Za-z]+\d+/.test(trimmed) ? trimmed : null;
    }, []);

    const extractDesignatorFromSelection = useCallback((item: unknown): string | null => {
        const findDesignator = (value: unknown, depth = 0): string | null => {
            if (!value || typeof value !== "object" || depth > 3) return null;
            const entry = value as Record<string, unknown>;

            const direct = [
                entry.reference,
                entry.Reference,
                entry.designator,
                entry.elementRef,
                entry.ref,
                entry.Ref,
            ];
            for (const candidate of direct) {
                const designator = normalizeDesignator(candidate);
                if (designator) return designator;
            }

            if (typeof entry.get_property_text === "function") {
                try {
                    const fromProperty = normalizeDesignator(
                        (entry.get_property_text as (name: string) => unknown)("Reference")
                    );
                    if (fromProperty) return fromProperty;
                } catch {
                    // noop
                }
            }

            const properties = entry.properties;
            if (properties instanceof Map) {
                const refProp = properties.get("Reference");
                if (refProp && typeof refProp === "object") {
                    const propEntry = refProp as Record<string, unknown>;
                    const fromMap = normalizeDesignator(
                        propEntry.shown_text ?? propEntry.text ?? propEntry.value
                    );
                    if (fromMap) return fromMap;
                }
            }

            const defaultInstance = entry.default_instance;
            if (defaultInstance && typeof defaultInstance === "object") {
                const fromDefault = normalizeDesignator(
                    (defaultInstance as Record<string, unknown>).reference
                );
                if (fromDefault) return fromDefault;
            }

            return (
                findDesignator(entry.parent, depth + 1) ||
                findDesignator(entry.item, depth + 1) ||
                findDesignator(entry.context, depth + 1)
            );
        };

        return findDesignator(item);
    }, [normalizeDesignator]);

    // Pull a usable datasheet URL off a selected schematic symbol. The live item
    // is a SchematicSymbol instance, which exposes `datasheet`,
    // `get_property_text("Datasheet")`, and a `properties` Map — we probe all
    // three defensively. KiCad stores "~" (or empty) to mean "no datasheet", and
    // we only surface web URLs for opening in a new tab.
    const extractDatasheetUrl = useCallback((item: unknown): string | null => {
        const normalize = (value: unknown): string | null => {
            if (typeof value !== "string") return null;
            const trimmed = value.trim();
            if (!trimmed || trimmed === "~") return null;
            if (!/^https?:\/\//i.test(trimmed)) return null;
            return trimmed;
        };

        const read = (value: unknown, depth = 0): string | null => {
            if (!value || typeof value !== "object" || depth > 3) return null;
            const entry = value as Record<string, unknown>;

            const direct = normalize(entry.datasheet ?? entry.Datasheet);
            if (direct) return direct;

            if (typeof entry.get_property_text === "function") {
                try {
                    const fromProperty = normalize(
                        (entry.get_property_text as (name: string) => unknown)("Datasheet")
                    );
                    if (fromProperty) return fromProperty;
                } catch {
                    // noop
                }
            }

            const properties = entry.properties;
            if (properties instanceof Map) {
                const prop = properties.get("Datasheet");
                if (prop && typeof prop === "object") {
                    const propEntry = prop as Record<string, unknown>;
                    const fromMap = normalize(
                        propEntry.text ?? propEntry.shown_text ?? propEntry.value
                    );
                    if (fromMap) return fromMap;
                }
            }

            return read(entry.item, depth + 1) || read(entry.parent, depth + 1);
        };

        return read(item);
    }, []);

    const openDatasheet = useCallback((url: string | null) => {
        if (!url) return;
        // Use a synthetic anchor click instead of window.open(url, "_blank",
        // "noopener,noreferrer"): Firefox's popup blocker rejects window.open()
        // with a feature string — keydown isn't in dom.popup_allowed_events, and
        // the feature string trips the blocker on dblclick too. A <a target=
        // "_blank"> click during a user gesture is a link navigation, so it opens
        // a tab reliably in both Firefox and Chrome.
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.style.display = "none";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
    }, []);

    const getCrossProbeTargetContext = useCallback(
        (sourceContext: CrossProbeContext): CrossProbeContext =>
            sourceContext === "SCH" ? "PCB" : "SCH",
        [],
    );

    const clearCrossProbeRetry = useCallback((targetContext: CrossProbeContext) => {
        const timerId = crossProbeRetryTimerRef.current[targetContext];
        if (timerId !== null) {
            window.clearTimeout(timerId);
            crossProbeRetryTimerRef.current[targetContext] = null;
        }
    }, []);

    const runCrossProbe = useCallback(
        function runCrossProbe(
            targetViewer: ECadViewerElement | null,
            sourceContext: "SCH" | "PCB",
            designator: string,
            attempts = 0,
            runId?: number,
        ) {
            const targetContext = getCrossProbeTargetContext(sourceContext);

            if (attempts === 0) {
                clearCrossProbeRetry(targetContext);
                crossProbeRunIdRef.current[targetContext] += 1;
                runId = crossProbeRunIdRef.current[targetContext];
            }

            if (!targetViewer) {
                clearCrossProbeRetry(targetContext);
                return;
            }

            if (!runId || crossProbeRunIdRef.current[targetContext] !== runId) {
                return;
            }

            if (typeof targetViewer.requestCrossProbe !== "function") {
                clearCrossProbeRetry(targetContext);
                return;
            }

            const result = targetViewer.requestCrossProbe({
                sourceContext,
                targetContext,
                mode: "select",
                kind: "designator",
                value: designator,
                designator,
            });

            if (
                !result.resolved &&
                result.reason === "target-not-available" &&
                attempts < CROSS_PROBE_MAX_RETRIES
            ) {
                crossProbeRetryTimerRef.current[targetContext] = window.setTimeout(() => {
                    runCrossProbe(
                        targetViewer,
                        sourceContext,
                        designator,
                        attempts + 1,
                        runId,
                    );
                }, CROSS_PROBE_RETRY_DELAY_MS);
                return;
            }

            clearCrossProbeRetry(targetContext);
        },
        [clearCrossProbeRetry, getCrossProbeTargetContext],
    );

    const copyToClipboard = async (label: string, value: string) => {
        try {
            await navigator.clipboard.writeText(value);
            setCopiedField(label);
            setTimeout(() => setCopiedField(null), 1400);
        } catch (error) {
            console.warn("Failed to copy URL", error);
        }
    };

    const appendCommit = useCallback((url: string) => {
        if (!commit) return url;
        return `${url}${url.includes("?") ? "&" : "?"}commit=${encodeURIComponent(commit)}`;
    }, [commit]);

    // --- Schematic hierarchy navigation ---
    const { data: hierarchy, loading: hierarchyLoading, error: hierarchyError } =
        useSchematicHierarchy(projectId, commit, activeTab === "sch");
    const [showHierarchy, setShowHierarchy] = useState(true);
    const [nav, setNav] = useState<{ stack: string[]; index: number }>({ stack: [], index: -1 });
    const suppressDriveRef = useRef(false);
    const lastDrivenRef = useRef<string | null>(null);

    const rootSheetPath = hierarchy?.root.sheetPath ?? null;

    const parentByPath = useMemo(() => {
        const parentMap = new Map<string, string>();
        const walk = (node: SchematicHierarchyNode, parent: SchematicHierarchyNode | null) => {
            if (parent) parentMap.set(node.sheetPath, parent.sheetPath);
            node.children.forEach((child) => walk(child, node));
        };
        if (hierarchy?.root) walk(hierarchy.root, null);
        return parentMap;
    }, [hierarchy]);

    // Each flattened blob carries its sheetPath + the synthetic filename the viewer
    // keys on, so navigation maps a specific instance to its own annotated page.
    const { blobFilenameByPath, blobPathByFilename } = useMemo(() => {
        const byPath = new Map<string, string>();
        const byFilename = new Map<string, string>();
        for (const blob of schematicBlobs) {
            byPath.set(blob.sheetPath, blob.filename);
            byFilename.set(blob.filename, blob.sheetPath);
        }
        return { blobFilenameByPath: byPath, blobPathByFilename: byFilename };
    }, [schematicBlobs]);

    // Map a hierarchy sheet-path to the synthetic per-instance page id the viewer loads.
    const pageIdForSheetPath = useCallback((sheetPath: string): string | null => {
        if (!rootSheetPath) return null;
        return blobFilenameByPath.get(sheetPath) ?? "root.kicad_sch";
    }, [rootSheetPath, blobFilenameByPath]);

    const sheetPathForPageId = useCallback((pageId: string): string | null => {
        if (!hierarchy?.root) return null;
        if (blobPathByFilename.has(pageId)) return blobPathByFilename.get(pageId)!;
        // Fallback: the viewer may report a basename or "root".
        const base = pageId.split("/").pop() || pageId;
        if (base === "root.kicad_sch" || base === "root") return hierarchy.root.sheetPath;
        return blobPathByFilename.get(base) ?? null;
    }, [hierarchy, blobPathByFilename]);

    // Keep a stable ref so the (comment/sheet) listener effect need not depend on it.
    const sheetPathForPageIdRef = useRef(sheetPathForPageId);
    useEffect(() => { sheetPathForPageIdRef.current = sheetPathForPageId; }, [sheetPathForPageId]);

    const activeSheetPath = nav.index >= 0 ? nav.stack[nav.index] : (rootSheetPath ?? "");
    const canGoBack = nav.index > 0;
    const canGoForward = nav.index >= 0 && nav.index < nav.stack.length - 1;
    const canGoUp = Boolean(parentByPath.get(activeSheetPath));

    const pushNav = useCallback((sheetPath: string) => {
        setNav((prev) => {
            if (prev.index >= 0 && prev.stack[prev.index] === sheetPath) return prev;
            const base = prev.stack.slice(0, prev.index + 1);
            return { stack: [...base, sheetPath], index: base.length };
        });
    }, []);

    const driveViewer = useCallback((sheetPath: string) => {
        const pageId = pageIdForSheetPath(sheetPath);
        if (!pageId) return;
        if (lastDrivenRef.current === pageId) return;
        lastDrivenRef.current = pageId;
        const viewer = schematicViewerRef.current;
        if (viewer?.switchPage) {
            try { viewer.switchPage(pageId); } catch (err) { console.warn("switchPage failed", err); }
        }
        setActivePage(pageId);
    }, [pageIdForSheetPath]);

    // Initialize navigation to the root once the hierarchy resolves.
    useEffect(() => {
        if (hierarchy?.root && nav.index === -1) {
            suppressDriveRef.current = true; // viewer already shows the root
            lastDrivenRef.current = "root.kicad_sch";
            setNav({ stack: [hierarchy.root.sheetPath], index: 0 });
        }
    }, [hierarchy, nav.index]);

    // Drive the viewer when the active sheet changes via user/history navigation
    // (but not when the change originated from a viewer sheet-loaded event).
    useEffect(() => {
        if (activeTab !== "sch") return;
        if (nav.index < 0) return;
        if (suppressDriveRef.current) { suppressDriveRef.current = false; return; }
        driveViewer(activeSheetPath);
    }, [activeSheetPath, activeTab, nav.index, driveViewer]);

    const handleHierarchySelect = useCallback((node: SchematicHierarchyNode) => {
        setActiveTab("sch");
        pushNav(node.sheetPath);
    }, [pushNav]);

    const handleNavUp = useCallback(() => {
        const parent = parentByPath.get(activeSheetPath);
        if (parent) pushNav(parent);
    }, [parentByPath, activeSheetPath, pushNav]);

    const handleNavBack = useCallback(() => {
        setNav((prev) => (prev.index > 0 ? { ...prev, index: prev.index - 1 } : prev));
    }, []);

    const handleNavForward = useCallback(() => {
        setNav((prev) => (prev.index < prev.stack.length - 1 ? { ...prev, index: prev.index + 1 } : prev));
    }, []);

    useEffect(() => {
        setModelUrl(null);
        setIbomUrl(null);
        setSchematicBlobs([]);
        setPcbContent(null);
        setSchematicContentLoaded(false);
        setPcbContentLoaded(false);
    }, [projectId, commit]);

    // Initial Data Fetch
    useEffect(() => {
        const controller = new AbortController();
        const signal = controller.signal;

        const fetchData = async () => {
            setLoading(true);
            const baseUrl = `/api/projects/${projectId}`;

            try {
                // Parallel fetch for main assets (excluding schematic and PCB content for now)
                const [modelRes, ibomRes, commentsRes, filesRes] = await Promise.allSettled([
                    fetch(appendCommit(`${baseUrl}/3d-model`), { signal }),
                    fetch(appendCommit(`${baseUrl}/ibom`), { signal }),
                    fetch(`/api/projects/${projectId}/comments`, { signal }),
                    fetch(appendCommit(`${baseUrl}/files?type=design`), { signal })
                ]);

                // Handle 3D
                let glbUrl = null;
                if (filesRes.status === "fulfilled" && filesRes.value.ok) {
                    try {
                        const files = await filesRes.value.json();
                        if (signal.aborted) return;
                        const glbFile = files.find((f: any) =>
                            f.path.toLowerCase().startsWith("3dmodel/") &&
                            f.name.toLowerCase().endsWith(".glb")
                        );
                        if (glbFile) {
                            glbUrl = appendCommit(`${baseUrl}/download?path=${encodeURIComponent(glbFile.path)}&type=design&inline=true`);
                        }
                    } catch (e) {
                        if (!isAbortError(e)) {
                            console.warn("Error parsing design files", e);
                        }
                    }
                }

                if (glbUrl) {
                    setModelUrl(glbUrl);
                } else if (modelRes.status === "fulfilled" && modelRes.value.ok) {
                    setModelUrl(appendCommit(`${baseUrl}/3d-model`));
                } else {
                    setModelUrl(null);
                }

                // Handle iBoM
                if (ibomRes.status === "fulfilled" && ibomRes.value.ok) {
                    setIbomUrl(appendCommit(`${baseUrl}/ibom`));
                } else {
                    setIbomUrl(null);
                }

                // Handle Comments
                if (commentsRes.status === "fulfilled" && commentsRes.value.ok) {
                    const cData = await commentsRes.value.json();
                    if (signal.aborted) return;
                    setComments(cData.comments || []);
                } else {
                    setComments([]);
                }

                try {
                    const sourceResponse = await fetch(`/api/projects/${projectId}/comments/source-urls`, { signal });

                    if (sourceResponse.ok) {
                        const sourceData = await sourceResponse.json();
                        if (signal.aborted) return;
                        setCommentsSourceUrls(sourceData);
                    } else {
                        setCommentsSourceUrls(null);
                    }
                } catch (sourceError) {
                    if (!isAbortError(sourceError)) {
                        console.warn("Failed to load comments source URLs", sourceError);
                    }
                }

            } catch (err) {
                if (!isAbortError(err)) {
                    console.error("Error loading visualizer data", err);
                }
            } finally {
                if (!signal.aborted) {
                    setLoading(false);
                }
            }
        };

        void fetchData();
        return () => controller.abort();
    }, [projectId, appendCommit]);

    // Lazy load schematic content when schematic tab is first accessed
    useEffect(() => {
        if (activeTab === "sch" && !schematicContentLoaded) {
            const controller = new AbortController();
            const signal = controller.signal;

            const loadSchematic = async () => {
                try {
                    const baseUrl = `/api/projects/${projectId}`;
                    // One request returns every sheet instance as a pre-annotated blob,
                    // with correct per-instance reference designators baked in.
                    const res = await fetch(appendCommit(`${baseUrl}/schematic/flattened`), { signal });

                    if (res.ok) {
                        const data = await res.json();
                        if (signal.aborted) return;
                        setSchematicBlobs(Array.isArray(data.blobs) ? data.blobs : []);
                    } else {
                        console.error("Schematic not found");
                        setSchematicBlobs([]);
                    }
                } catch (err) {
                    if (!isAbortError(err)) {
                        console.error("Error loading schematic content", err);
                    }
                } finally {
                    if (!signal.aborted) {
                        setSchematicContentLoaded(true);
                    }
                }
            };

            void loadSchematic();
            return () => controller.abort();
        }
    }, [activeTab, schematicContentLoaded, projectId, appendCommit]);

    // Lazy load PCB content when PCB tab is first accessed
    useEffect(() => {
        if (activeTab === "pcb" && !pcbContentLoaded) {
            const controller = new AbortController();
            const signal = controller.signal;

            const loadPcb = async () => {
                try {
                    const baseUrl = `/api/projects/${projectId}`;
                    const pcbRes = await fetch(appendCommit(`${baseUrl}/pcb`), { signal });

                    if (pcbRes.ok) {
                        const pcbText = await pcbRes.text();
                        if (signal.aborted) return;
                        setPcbContent(pcbText);
                    } else {
                        console.error("PCB not found");
                        setPcbContent(null);
                    }
                } catch (err) {
                    if (!isAbortError(err)) {
                        console.error("Error loading PCB content", err);
                    }
                } finally {
                    if (!signal.aborted) {
                        setPcbContentLoaded(true);
                    }
                }
            };

            void loadPcb();
            return () => controller.abort();
        }
    }, [activeTab, pcbContentLoaded, projectId, appendCommit]);

    // Reset lazy loading flags when project changes
    useEffect(() => {
        setSchematicContentLoaded(false);
        setPcbContentLoaded(false);
        setSchematicBlobs([]);
        setPcbContent(null);
        setModelUrl(null);
        setIbomUrl(null);
        setComments([]);
        setCommentsSourceUrls(null);
        setActivePage("root.kicad_sch");
        setNav({ stack: [], index: -1 });
        setShowHierarchy(true);
        suppressDriveRef.current = false;
        lastDrivenRef.current = null;
        setCommentMode(false);
        setShowCommentForm(false);
        setShowCommentPanel(false);
        setPendingLocation(null);
        setPendingContext("PCB");
        setIsSubmittingComment(false);
        setIsPushingComments(false);
        setPushMessage(null);
        setShowPushDialog(false);
        setIsUrlsPopoverOpen(false);
        setCopiedField(null);
        lastCrossProbeRef.current = { SCH: null, PCB: null };
        clearCrossProbeRetry("SCH");
        clearCrossProbeRetry("PCB");
        crossProbeRunIdRef.current = { SCH: 0, PCB: 0 };
        selectedDatasheetUrlRef.current = null;
        lastSchematicSelectAtRef.current = 0;
    }, [projectId, clearCrossProbeRetry]);

    useEffect(() => {
        return () => {
            clearCrossProbeRetry("SCH");
            clearCrossProbeRetry("PCB");
        };
    }, [clearCrossProbeRetry]);

    // Event Listeners for ecad-viewer
    useEffect(() => {
        const schematicViewer = schematicViewerElement;
        const pcbViewer = pcbViewerElement;

        if (!schematicViewer && !pcbViewer) return;

        const handleCommentClick = (e: CustomEvent) => {
            if (!canModifyComments) {
                return;
            }
            if (activeCommentContext !== "SCH" && activeCommentContext !== "PCB") {
                return;
            }

            const detail = e.detail;
            setPendingLocation({
                x: detail.worldX,
                y: detail.worldY,
                layer: detail.layer || "F.Cu",
            });
            setPendingContext(activeCommentContext);
            setShowCommentForm(true);
        };

        const handleSheetLoad = (e: CustomEvent) => {
            let pageId: string | null = null;
            if (typeof e.detail === 'string') pageId = e.detail;
            else if (e.detail?.filename) pageId = e.detail.filename;
            else if (e.detail?.sheetName) pageId = e.detail.sheetName;
            if (!pageId) return;
            // Selection doesn't survive a sheet change; drop any stale datasheet
            // so "D" can't open a symbol that's no longer on screen.
            selectedDatasheetUrlRef.current = null;
            lastSchematicSelectAtRef.current = 0;
            setActivePage(pageId);
            lastDrivenRef.current = pageId;
            // Sync the hierarchy panel/history to a page change that originated in the
            // viewer (e.g. double-click-to-enter), without re-driving the viewer.
            const sheetPath = sheetPathForPageIdRef.current(pageId);
            if (sheetPath) {
                setNav((prev) => {
                    if (prev.index >= 0 && prev.stack[prev.index] === sheetPath) return prev;
                    suppressDriveRef.current = true;
                    const base = prev.stack.slice(0, prev.index + 1);
                    return { stack: [...base, sheetPath], index: base.length };
                });
            }
        };

        // Add listeners to both viewers
        if (schematicViewer) {
            schematicViewer.addEventListener("ecad-viewer:comment:click", handleCommentClick as EventListener);
            schematicViewer.addEventListener("kicanvas:sheet:loaded", handleSheetLoad as EventListener);
        }

        if (pcbViewer) {
            pcbViewer.addEventListener("ecad-viewer:comment:click", handleCommentClick as EventListener);
            pcbViewer.addEventListener("kicanvas:sheet:loaded", handleSheetLoad as EventListener);
        }

        return () => {
            if (schematicViewer) {
                schematicViewer.removeEventListener("ecad-viewer:comment:click", handleCommentClick as EventListener);
                schematicViewer.removeEventListener("kicanvas:sheet:loaded", handleSheetLoad as EventListener);
            }
            if (pcbViewer) {
                pcbViewer.removeEventListener("ecad-viewer:comment:click", handleCommentClick as EventListener);
                pcbViewer.removeEventListener("kicanvas:sheet:loaded", handleSheetLoad as EventListener);
            }
        };
    }, [activeCommentContext, canModifyComments, schematicViewerElement, pcbViewerElement]);

    // Toggle Comment Mode
    const toggleCommentMode = () => {
        if (!canModifyComments) {
            return;
        }
        setCommentMode((previous) => {
            const next = !previous;
            applyCommentModeToViewer(schematicViewerRef.current, next);
            applyCommentModeToViewer(pcbViewerRef.current, next);
            return next;
        });
    };

    useEffect(() => {
        applyCommentModeToViewer(schematicViewerElement, commentMode);
        applyCommentModeToViewer(pcbViewerElement, commentMode);
    }, [commentMode, schematicViewerElement, pcbViewerElement, applyCommentModeToViewer]);

    useEffect(() => {
        if (!commentMode) return;

        if (activeTab === "sch") {
            applyCommentModeToViewer(schematicViewerRef.current, true);
            return;
        }

        if (activeTab === "pcb") {
            applyCommentModeToViewer(pcbViewerRef.current, true);
        }
    }, [activeTab, commentMode, applyCommentModeToViewer]);

    useEffect(() => {
        // Cross-probe is optional: some viewer bundle versions don't expose it.
        schematicViewerRef.current?.setCrossProbeEnabled?.(true);
        pcbViewerRef.current?.setCrossProbeEnabled?.(true);
    }, [schematicViewerElement, pcbViewerElement]);

    useEffect(() => {
        const schematicViewer = schematicViewerElement;
        const pcbViewer = pcbViewerElement;
        if (!schematicViewer && !pcbViewer) return;

        const handleCrossProbeSelection = (
            fallbackSourceContext: CrossProbeContext,
            targetViewer: ECadViewerElement | null,
            event: Event,
        ) => {
            const detail = (event as CustomEvent<KiCanvasSelectDetail>).detail;
            const sourceContext = detail?.sourceContext ?? fallbackSourceContext;
            const designator = extractDesignatorFromSelection(detail?.item);
            if (!designator) return;
            lastCrossProbeRef.current[sourceContext] = designator;
            runCrossProbe(targetViewer, sourceContext, designator);
        };

        const onSchematicSelect = (event: Event) => {
            // Track the selected symbol's datasheet for the "D" / double-click
            // open triggers. The timestamp is the double-click freshness guard.
            const detail = (event as CustomEvent<KiCanvasSelectDetail>).detail;
            selectedDatasheetUrlRef.current = extractDatasheetUrl(detail?.item);
            lastSchematicSelectAtRef.current = Date.now();
            handleCrossProbeSelection("SCH", pcbViewerRef.current, event);
        };
        const onPcbSelect = (event: Event) =>
            handleCrossProbeSelection("PCB", schematicViewerRef.current, event);

        schematicViewer?.addEventListener("kicanvas:select", onSchematicSelect as EventListener);
        pcbViewer?.addEventListener("kicanvas:select", onPcbSelect as EventListener);

        return () => {
            schematicViewer?.removeEventListener("kicanvas:select", onSchematicSelect as EventListener);
            pcbViewer?.removeEventListener("kicanvas:select", onPcbSelect as EventListener);
        };
    }, [schematicViewerElement, pcbViewerElement, extractDesignatorFromSelection, extractDatasheetUrl, runCrossProbe]);

    // Open the selected symbol's datasheet in a new tab: press "D", or
    // double-click the symbol. Both act on the schematic tab only.
    useEffect(() => {
        const schematicViewer = schematicViewerElement;

        // First click of a double-click re-fires selection on the item under the
        // cursor, so a selection within this window means the cursor is genuinely
        // over a symbol. Empty-space double-clicks fire no selection (the viewer
        // only emits select when an item is hit), so the timestamp stays stale.
        const DOUBLE_CLICK_FRESH_MS = 500;

        const isEditableTarget = (target: EventTarget | null): boolean => {
            const el = target as HTMLElement | null;
            if (!el || typeof el.tagName !== "string") return false;
            const tag = el.tagName.toLowerCase();
            return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
        };

        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "d" && event.key !== "D") return;
            if (event.ctrlKey || event.metaKey || event.altKey) return;
            if (activeTab !== "sch") return;
            if (isEditableTarget(event.target)) return;
            const url = selectedDatasheetUrlRef.current;
            if (!url) return;
            event.preventDefault();
            openDatasheet(url);
        };

        const onDblClick = () => {
            const url = selectedDatasheetUrlRef.current;
            if (!url) return;
            if (Date.now() - lastSchematicSelectAtRef.current > DOUBLE_CLICK_FRESH_MS) return;
            openDatasheet(url);
        };

        document.addEventListener("keydown", onKeyDown);
        schematicViewer?.addEventListener("dblclick", onDblClick);

        return () => {
            document.removeEventListener("keydown", onKeyDown);
            schematicViewer?.removeEventListener("dblclick", onDblClick);
        };
    }, [schematicViewerElement, activeTab, openDatasheet]);

    useEffect(() => {
        if (activeTab === "pcb" && lastCrossProbeRef.current.SCH) {
            runCrossProbe(pcbViewerRef.current, "SCH", lastCrossProbeRef.current.SCH);
        } else if (activeTab === "sch" && lastCrossProbeRef.current.PCB) {
            runCrossProbe(schematicViewerRef.current, "PCB", lastCrossProbeRef.current.PCB);
        }
    }, [activeTab, runCrossProbe, schematicViewerElement, pcbViewerElement]);

    // Submit Comment
    const handleSubmitComment = async (content: string) => {
        if (!pendingLocation || !canModifyComments) return;
        setIsSubmittingComment(true);
        try {
            const location = { ...pendingLocation, page: pendingContext === "SCH" ? activePage : "" };
            const response = await fetchApi(`/api/projects/${projectId}/comments`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    context: pendingContext,
                    location,
                    content,
                    author: user?.name || "anonymous"
                })
            });

            if (response.ok) {
                const newComment = await response.json();
                setComments(prev => [...prev, newComment]);
                setShowCommentForm(false);
                setPendingLocation(null);
                // Turn off comment mode after posting? User might want to post multiple. Keep it on.
            }
        } catch (err) {
            console.error("Create comment failed", err);
        } finally {
            setIsSubmittingComment(false);
        }
    };

    // Navigate to Comment
    const handleCommentNavigate = (comment: Comment) => {
        // Force switch to appropriate tab if in 3D/iBom
        if (comment.context === "SCH" && activeTab !== "sch") {
            setActiveTab("sch");
        } else if (comment.context === "PCB" && activeTab !== "pcb") {
            setActiveTab("pcb");
        }

        // Get the appropriate viewer
        const viewer = comment.context === "SCH" ? schematicViewerRef.current : pcbViewerRef.current;
        if (!viewer) return;

        if (comment.context === "SCH" && comment.location.page) {
            viewer.switchPage(comment.location.page);
        }

        if (viewer.zoomToLocation) {
            viewer.zoomToLocation(comment.location.x, comment.location.y);
        }
    };

    // Resolving/Replying
    const handleResolveComment = async (commentId: string, resolved: boolean) => {
        if (!canModifyComments) return;
        const response = await fetchApi(`/api/projects/${projectId}/comments/${commentId}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: resolved ? "RESOLVED" : "OPEN" })
        });
        if (response.ok) {
            const updated = await response.json();
            setComments(prev => prev.map(c => c.id === commentId ? updated : c));
        }
    };

    const handleReplyComment = async (commentId: string, content: string) => {
        if (!canModifyComments) return;
        const response = await fetchApi(`/api/projects/${projectId}/comments/${commentId}/replies`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                content,
                author: user?.name || "anonymous"
            })
        });
        if (response.ok) {
            const data = await response.json();
            setComments(prev => prev.map(c => c.id === commentId ? data.comment : c));
        }
    };

    const handleDeleteComment = async (commentId: string) => {
        if (!canModifyComments) return;
        try {
            const response = await fetchApi(`/api/projects/${projectId}/comments/${commentId}`, {
                method: "DELETE",
            });
            if (response.ok) {
                setComments(prev => prev.filter(c => c.id !== commentId));
            }
        } catch (err) {
            console.error("Failed to delete comment", err);
        }
    };

    // Export comments.json artifact from DB snapshot
    const handlePushComments = async () => {
        if (!canModifyComments) return;
        setIsPushingComments(true);
        setPushMessage(null);

        try {
            const response = await fetchApi(`/api/projects/${projectId}/comments/push`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });

            const data = await response.json();

            if (response.ok) {
                const artifactPath = data.comments_path ? ` (${data.comments_path})` : "";
                setPushMessage({ type: "success", text: `${data.message || "Generated comments artifact."}${artifactPath}` });
                setShowPushDialog(false);
            } else {
                setPushMessage({ type: "error", text: data.detail || "Failed to generate comments artifact." });
            }
        } catch (err: any) {
            setPushMessage({ type: "error", text: err.message || "Network error while generating comments artifact." });
        } finally {
            setIsPushingComments(false);
            // Clear message after 5 seconds
            setTimeout(() => setPushMessage(null), 5000);
        }
    };

    // Filtering comments for Overlay
    const overlayComments = comments.filter(c => {
        if (!activeCommentContext) return false;

        // Must match context
        if (c.context !== activeCommentContext) return false;

        // If SCH, match page
        if (activeCommentContext === "SCH") {
            const norm = (p: string) => p ? p.split('/').pop() || p : "";
            const cPage = norm(c.location.page || "");
            const aPage = norm(activePage);
            // Root handling
            const isRootC = cPage === "root.kicad_sch" || cPage === "root";
            const isRootA = aPage === "root.kicad_sch" || aPage === "root";

            if (isRootA && isRootC) return true;
            return cPage === aPage;
        }
        return true;
    });

    const shouldShowOverlay =
        (activeTab === "sch" && Boolean(schematicBlobs.length && schematicViewerElement)) ||
        (activeTab === "pcb" && Boolean(pcbContent && pcbViewerElement));
    const schematicSources = useMemo<ViewerBlobSource[]>(
        () => schematicBlobs.map(({ filename, content }) => ({ filename, content })),
        [schematicBlobs],
    );
    const pcbSources = useMemo<ViewerBlobSource[]>(
        () => (pcbContent
            ? [{ filename: "board.kicad_pcb", content: pcbContent }]
            : []),
        [pcbContent],
    );
    const schematicViewerKey = buildViewerKey("schematic", projectId, commit, schematicSources);
    const pcbViewerKey = buildViewerKey("pcb", projectId, commit, pcbSources);

    // Tab Config
    const tabs: { id: VisualizerTab; label: string; icon: any }[] = [
        { id: "sch", label: "Schematic", icon: Cpu },
        { id: "pcb", label: "PCB Layout", icon: CircuitBoard },
        { id: "3d", label: "3D View", icon: Box },
        { id: "ibom", label: "iBoM", icon: FileText },
    ];

    if (loading) return <div className="flex justify-center items-center h-full">Loading Visualizer...</div>;

    return (
        <div className="flex flex-col h-full bg-background relative selection-none">
            {/* Toolbar */}
            <div className="flex items-center gap-1 border-b px-2 py-1 bg-muted/20">
                {tabs.map(tab => {
                    const Icon = tab.icon;
                    return (
                        <Button
                            key={tab.id}
                            variant={activeTab === tab.id ? "secondary" : "ghost"}
                            size="sm"
                            onClick={() => setActiveTab(tab.id)}
                            className="text-xs h-8"
                        >
                            <Icon className="w-3 h-3 mr-2" />
                            {tab.label}
                        </Button>
                    );
                })}

                {/* Schematic hierarchy navigation */}
                {activeTab === "sch" && (
                    <>
                        <div className="mx-1 h-5 w-px bg-border" />
                        <Button
                            variant={showHierarchy ? "secondary" : "ghost"}
                            size="sm"
                            onClick={() => setShowHierarchy((v) => !v)}
                            className="text-xs h-8"
                            title="Toggle schematic hierarchy"
                        >
                            <ListTree className="w-3 h-3 mr-2" />
                            Hierarchy
                        </Button>
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={handleNavBack}
                            disabled={!canGoBack}
                            className="h-8 w-8"
                            title="Back"
                            aria-label="Back"
                        >
                            <ArrowLeft className="w-4 h-4" />
                        </Button>
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={handleNavUp}
                            disabled={!canGoUp}
                            className="h-8 w-8"
                            title="Up a sheet"
                            aria-label="Up a sheet"
                        >
                            <ArrowUp className="w-4 h-4" />
                        </Button>
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={handleNavForward}
                            disabled={!canGoForward}
                            className="h-8 w-8"
                            title="Forward"
                            aria-label="Forward"
                        >
                            <ArrowRight className="w-4 h-4" />
                        </Button>
                    </>
                )}
                <div className="flex-1" />

                {/* Comment Controls */}
                {(activeTab === "sch" || activeTab === "pcb") && (
                    <>
                        <Popover open={isUrlsPopoverOpen} onOpenChange={setIsUrlsPopoverOpen}>
                            <PopoverTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="text-xs h-8"
                                    aria-label="Show KiCad comments REST URLs"
                                >
                                    <Link2 className="w-3 h-3 mr-2" />
                                    REST URLs
                                </Button>
                            </PopoverTrigger>
                            <PopoverContent align="end" side="bottom" className="w-[520px] max-w-[calc(100vw-2rem)] p-3">
                                <div className="space-y-3">
                                    <div>
                                        <p className="text-sm font-medium">KiCad Comments REST URLs</p>
                                        <p className="text-xs text-muted-foreground">
                                            Copy these into KiCad Comments Source Settings.
                                        </p>
                                    </div>
                                    {commentsSourceUrls ? (
                                        <div className="space-y-2">
                                            {[
                                                { label: "List URL", value: commentsSourceUrls.list_url },
                                                { label: "Patch URL Template", value: commentsSourceUrls.patch_url_template },
                                                { label: "Reply URL Template", value: commentsSourceUrls.reply_url_template },
                                                { label: "Delete URL Template", value: commentsSourceUrls.delete_url_template },
                                            ].map((entry) => (
                                                <div key={entry.label} className="rounded border bg-muted/30 p-2">
                                                    <div className="mb-1 text-[11px] font-medium text-muted-foreground">{entry.label}</div>
                                                    <div className="flex items-start gap-2">
                                                        <code className="flex-1 break-all rounded bg-background px-2 py-1 text-[11px]">{entry.value}</code>
                                                        <Button
                                                            type="button"
                                                            variant="outline"
                                                            size="sm"
                                                            className="h-7 shrink-0"
                                                            onClick={() => copyToClipboard(entry.label, entry.value)}
                                                        >
                                                            {copiedField === entry.label ? (
                                                                <>
                                                                    <Check className="h-3 w-3 mr-1" />
                                                                    Copied
                                                                </>
                                                            ) : (
                                                                <>
                                                                    <Copy className="h-3 w-3 mr-1" />
                                                                    Copy
                                                                </>
                                                            )}
                                                        </Button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <p className="text-xs text-muted-foreground">Loading URL helpers...</p>
                                    )}
                                </div>
                            </PopoverContent>
                        </Popover>
                        <Button
                            variant={commentMode ? "default" : "ghost"}
                            size="sm"
                            onClick={toggleCommentMode}
                            disabled={!canModifyComments}
                            className={`text-xs h-8 ${commentMode ? "bg-amber-600 text-white hover:bg-amber-700" : ""}`}
                        >
                            <MessageSquarePlus className="w-3 h-3 mr-2" />
                            {commentMode ? "Commenting Mode" : "Add Comment"}
                        </Button>
                        <Button
                            variant={showCommentPanel ? "secondary" : "ghost"}
                            size="sm"
                            onClick={() => setShowCommentPanel(!showCommentPanel)}
                            className="text-xs h-8 ml-1"
                        >
                            <MessageSquare className="w-3 h-3 mr-2" />
                            Comments
                            <span className="ml-1 bg-muted-foreground/20 px-1 rounded-full text-[10px]">
                                {comments.length}
                            </span>
                        </Button>
                        {canModifyComments && (
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setShowPushDialog(true)}
                                className="text-xs h-8 ml-1"
                                title="Generate comments.json artifact from DB"
                            >
                                <GitBranch className="w-3 h-3 mr-2" />
                                Generate JSON
                            </Button>
                        )}
                    </>
                )}
            </div>

            {/* Push Message Feedback */}
            {pushMessage && (
                <div className={`px-4 py-2 text-sm border-b ${pushMessage.type === "success"
                    ? "bg-green-500/10 border-green-500/20 text-green-500"
                    : "bg-red-500/10 border-red-500/20 text-red-500"
                    }`}>
                    {pushMessage.text}
                    <button
                        onClick={() => setPushMessage(null)}
                        className="ml-2 text-xs underline"
                    >
                        Dismiss
                    </button>
                </div>
            )}

            {/* Generate comments.json Dialog */}
            <Dialog open={showPushDialog} onOpenChange={setShowPushDialog}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Generate Comments Artifact</DialogTitle>
                        <DialogDescription>
                            This writes the latest DB comments to `.comments/comments.json`. Push to remote is handled by your Git workflow.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowPushDialog(false)} disabled={isPushingComments}>
                            Cancel
                        </Button>
                        <Button onClick={handlePushComments} disabled={isPushingComments}>
                            {isPushingComments ? "Generating..." : "Generate"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Content Area */}
            <div className="flex-1 relative overflow-hidden">
                {/* Schematic View - always mounted but conditionally visible */}
                <div className={`absolute inset-0 z-10 transition-opacity duration-200 ${activeTab === "sch" ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"}`}>
                    {schematicContentLoaded ? (
                        schematicSources.length > 0 ? (
                            <EcadViewerHost
                                viewerKey={schematicViewerKey}
                                sources={schematicSources}
                                setViewerRef={setSchematicViewerRef}
                            />
                        ) : (
                            <div className="flex items-center justify-center h-full text-muted-foreground">
                                <p>No schematic files found.</p>
                            </div>
                        )
                    ) : (
                        <div className="flex items-center justify-center h-full text-muted-foreground">
                            <p>Loading schematic...</p>
                        </div>
                    )}
                </div>

                {/* PCB View - always mounted but conditionally visible */}
                <div className={`absolute inset-0 z-10 transition-opacity duration-200 ${activeTab === "pcb" ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"}`}>
                    {pcbContentLoaded ? (
                        pcbSources.length > 0 ? (
                            <EcadViewerHost
                                viewerKey={pcbViewerKey}
                                sources={pcbSources}
                                setViewerRef={setPcbViewerRef}
                            />
                        ) : (
                            <div className="flex items-center justify-center h-full text-muted-foreground">
                                <p>No PCB files found.</p>
                            </div>
                        )
                    ) : (
                        <div className="flex items-center justify-center h-full text-muted-foreground">
                            <p>Loading PCB...</p>
                        </div>
                    )}
                </div>

                {/* Comment Overlay - only visible on sch/pcb tabs */}
                {shouldShowOverlay ? (
                    <CommentOverlay
                        comments={overlayComments}
                        viewerRef={activeTab === "sch" ? schematicViewerRef : pcbViewerRef}
                        onPinClick={() => {
                            setShowCommentPanel(true);
                        }}
                    />
                ) : null}

                {/* 3D View */}
                {activeTab === "3d" && (
                    <div className="absolute inset-0 z-20 bg-background">
                        {modelUrl ? (
                            <Suspense fallback={<div className="p-10">Loading 3D Viewer...</div>}>
                                <Model3DViewer modelUrl={modelUrl} sceneKey={`project:${projectId}:tab:3d`} />
                            </Suspense>
                        ) : (
                            <div className="p-10">No 3D Model</div>
                        )}
                    </div>
                )}

                {/* iBoM View */}
                {activeTab === "ibom" && (
                    <div className="absolute inset-0 z-20 bg-white">
                        {ibomUrl ? <iframe src={ibomUrl} className="w-full h-full border-0" /> : <div className="p-10">No iBoM Found</div>}
                    </div>
                )}

                {/* Schematic Hierarchy Panel */}
                {activeTab === "sch" && showHierarchy && (
                    <div className="absolute top-0 left-0 bottom-0 z-40 flex w-64 flex-col border-r bg-background/95 backdrop-blur">
                        <div className="flex items-center justify-between border-b px-2 py-1">
                            <span className="text-xs font-semibold">Schematic Hierarchy</span>
                            <button
                                type="button"
                                onClick={() => setShowHierarchy(false)}
                                className="text-muted-foreground hover:text-foreground"
                                aria-label="Close schematic hierarchy"
                            >
                                <X className="h-3 w-3" />
                            </button>
                        </div>
                        <div className="flex-1 overflow-auto p-1">
                            {hierarchy?.root ? (
                                <SchematicHierarchyTree
                                    root={hierarchy.root}
                                    activeSheetPath={activeSheetPath}
                                    onSelect={handleHierarchySelect}
                                />
                            ) : hierarchyLoading ? (
                                <p className="p-2 text-xs text-muted-foreground">Loading hierarchy…</p>
                            ) : hierarchyError ? (
                                <p className="p-2 text-xs text-destructive">{hierarchyError}</p>
                            ) : (
                                <p className="p-2 text-xs text-muted-foreground">No schematic hierarchy.</p>
                            )}
                        </div>
                    </div>
                )}

                {/* Sidebar Overlay */}
                {showCommentPanel && (
                    <div className="absolute top-0 right-0 bottom-0 z-50 animate-in slide-in-from-right">
                        <CommentPanel
                            comments={comments}
                            onClose={() => setShowCommentPanel(false)}
                            onResolve={handleResolveComment}
                            onReply={handleReplyComment}
                            onDelete={handleDeleteComment}
                            onCommentClick={handleCommentNavigate}
                            canModify={canModifyComments}
                        />
                    </div>
                )}
            </div>

            {/* Modals */}
            <CommentForm
                isOpen={showCommentForm}
                onClose={() => setShowCommentForm(false)}
                onSubmit={handleSubmitComment}
                location={pendingLocation}
                context={pendingContext}
                isSubmitting={isSubmittingComment}
            />
        </div>
    );
}
