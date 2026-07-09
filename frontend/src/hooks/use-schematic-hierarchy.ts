import { useEffect, useState } from "react";
import { fetchJson } from "@/lib/api";
import type { SchematicHierarchy } from "@/types/schematic-hierarchy";

interface UseSchematicHierarchyResult {
    data: SchematicHierarchy | null;
    loading: boolean;
    error: string | null;
}

/**
 * Fetch the resolved schematic sheet hierarchy for a project.
 * `enabled` gates the request (e.g. only fetch while the schematic tab is active).
 */
export function useSchematicHierarchy(
    projectId: string,
    commit?: string | null,
    enabled = true,
): UseSchematicHierarchyResult {
    const [data, setData] = useState<SchematicHierarchy | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!enabled) return;

        const controller = new AbortController();
        let cancelled = false;

        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                let url = `/api/projects/${projectId}/schematic/hierarchy`;
                if (commit) url += `?commit=${encodeURIComponent(commit)}`;
                const result = await fetchJson<SchematicHierarchy>(url, { signal: controller.signal });
                if (!cancelled) setData(result);
            } catch (err) {
                if (controller.signal.aborted || cancelled) return;
                setData(null);
                setError(err instanceof Error ? err.message : "Failed to load schematic hierarchy");
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        void load();
        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [projectId, commit, enabled]);

    return { data, loading, error };
}
