"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Loader2, Check, AlertCircle } from "lucide-react";
import { isDialogSubmitShortcut } from "@/lib/dialog-shortcuts";

interface DiscoveredProject {
  name: string;
  relative_path: string;
  has_schematic: boolean;
  has_pcb: boolean;
}

interface AnalysisResult {
  repo_name: string;
  repo_url: string;
  import_type: "type1" | "type2";
  projects: DiscoveredProject[];
}

interface JobStatus {
  job_id: string;
  status: "running" | "completed" | "failed";
  message: string;
  percent: number;
  project_ids?: string[];
  error?: string;
  logs?: string[];
}

interface CommentsSourceUrls {
  project_id: string;
  project_name: string;
  base_url: string;
  list_url: string;
  patch_url_template: string;
  reply_url_template: string;
  delete_url_template: string;
}

interface ImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImportComplete: () => void;
}

type ImportState =
  | { step: "input" }
  | { step: "analyzing"; url: string; jobId?: string; status?: JobStatus }
  | { step: "review"; url: string; analysis: AnalysisResult }
  | { step: "importing"; url: string; jobId: string; status: JobStatus }
  | { step: "uploading"; filename: string }
  | {
      step: "complete";
      success: boolean;
      message: string;
      commentsSourceUrls?: CommentsSourceUrls[];
    };

export function ImportDialog({
  open,
  onOpenChange,
  onImportComplete,
}: ImportDialogProps) {
  const [state, setState] = useState<ImportState>({ step: "input" });
  const [url, setUrl] = useState("");
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"github" | "upload">("github");
  const [file, setFile] = useState<File | null>(null);
  const [uploadName, setUploadName] = useState("");
  const pollTimeoutRef = useRef<number | null>(null);
  const pollControllerRef = useRef<AbortController | null>(null);
  const pollingTokenRef = useRef(0);

  const clearPollingHandles = useCallback(() => {
    if (pollTimeoutRef.current !== null) {
      window.clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }
    if (pollControllerRef.current) {
      pollControllerRef.current.abort();
      pollControllerRef.current = null;
    }
  }, []);

  const stopPolling = useCallback(() => {
    pollingTokenRef.current += 1;
    clearPollingHandles();
  }, [clearPollingHandles]);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  useEffect(() => {
    if (!open) {
      stopPolling();
    }
  }, [open, stopPolling]);

  const reset = () => {
    stopPolling();
    setState({ step: "input" });
    setUrl("");
    setSelectedPaths(new Set());
    setMode("github");
    setFile(null);
    setUploadName("");
  };

  const handleClose = () => {
    reset();
    onOpenChange(false);
  };

  const analyzeRepo = async () => {
    if (!url.trim()) return;

    stopPolling();
    setState({ step: "analyzing", url });

    try {
      const res = await fetch("/api/projects/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || "Analysis failed");
      }

      const { job_id } = await res.json();

      // Start polling analysis job
      pollAnalysisJob(job_id, url);

    } catch (error: any) {
      setState({
        step: "complete",
        success: false,
        message: error.message || "Failed to start analysis",
      });
    }
  };

  const uploadArchive = async () => {
    if (!file) return;

    stopPolling();
    setState({ step: "uploading", filename: file.name });

    try {
      const form = new FormData();
      form.append("file", file);
      if (uploadName.trim()) form.append("name", uploadName.trim());

      const res = await fetch("/api/projects/upload", {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        const error = await res.json().catch(() => ({}));
        throw new Error(error.detail || "Upload failed");
      }

      const result = await res.json();
      setState({
        step: "complete",
        success: true,
        message: `Successfully imported ${result.project_ids?.length || 1} project(s) from archive`,
      });
      onImportComplete();
    } catch (error: any) {
      setState({
        step: "complete",
        success: false,
        message: error.message || "Failed to upload archive",
      });
    }
  };

  const pollAnalysisJob = async (jobId: string, repoUrl: string) => {
    stopPolling();
    const pollingToken = pollingTokenRef.current;

    const poll = async () => {
      const controller = new AbortController();
      try {
        pollControllerRef.current = controller;
        const res = await fetch(`/api/projects/jobs/${jobId}`, { signal: controller.signal });
        if (pollingTokenRef.current !== pollingToken) return;
        if (!res.ok) throw new Error("Failed to get job status");

        const status: JobStatus = await res.json();
        if (pollingTokenRef.current !== pollingToken) return;

        // Update state with ongoing job status
        setState({ step: "analyzing", url: repoUrl, jobId, status });

        if (status.status === "completed") {
          // Job completed, result should be in status (we need to ensure backend sends it)
          // The backend project_import_service puts 'result' in job dict
          // We need to extend JobStatus interface or cast it
          const result = (status as any).result as AnalysisResult;

          if (!result) {
            throw new Error("Analysis completed but no result returned");
          }

          // Auto-select type1
          if (result.import_type === "type1" && result.projects.length === 1) {
            setSelectedPaths(new Set([result.projects[0].relative_path]));
          }

          setState({ step: "review", url: repoUrl, analysis: result });

        } else if (status.status === "failed") {
          setState({
            step: "complete",
            success: false,
            message: status.error || "Analysis failed",
          });
        } else {
          // Continue polling
          pollTimeoutRef.current = window.setTimeout(() => {
            void poll();
          }, 1000);
        }
      } catch (error: any) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (pollingTokenRef.current !== pollingToken) return;
        setState({
          step: "complete",
          success: false,
          message: error.message || "Failed to check analysis status",
        });
      } finally {
        if (pollControllerRef.current === controller) {
          pollControllerRef.current = null;
        }
      }
    };

    void poll();
  };

  const startImport = async () => {
    if (state.step !== "review") return;

    const { url, analysis } = state;
    const pathsToImport =
      analysis.import_type === "type1"
        ? undefined
        : Array.from(selectedPaths);

    try {
      stopPolling();
      const res = await fetch("/api/projects/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url,
          import_type: analysis.import_type,
          selected_paths: pathsToImport,
        }),
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || "Import failed");
      }

      const { job_id } = await res.json();

      // Start polling
      pollJobStatus(job_id, url);
    } catch (error: any) {
      setState({
        step: "complete",
        success: false,
        message: error.message || "Failed to start import",
      });
    }
  };

  const pollJobStatus = async (jobId: string, repoUrl: string) => {
    stopPolling();
    const pollingToken = pollingTokenRef.current;

    const fetchCommentsSourceUrls = async (projectIds: string[]): Promise<CommentsSourceUrls[]> => {
      const entries = await Promise.all(
        projectIds.map(async (id) => {
          const response = await fetch(`/api/projects/${id}/comments/source-urls`);

          if (!response.ok) {
            throw new Error(`Failed to fetch KiCad URL helper for project ${id}`);
          }

          return response.json();
        })
      );

      return entries;
    };

    const poll = async () => {
      const controller = new AbortController();
      try {
        pollControllerRef.current = controller;
        const res = await fetch(`/api/projects/jobs/${jobId}`, { signal: controller.signal });
        if (pollingTokenRef.current !== pollingToken) return;
        if (!res.ok) throw new Error("Failed to get job status");

        const status: JobStatus = await res.json();
        if (pollingTokenRef.current !== pollingToken) return;

        setState({ step: "importing", url: repoUrl, jobId, status });

        if (status.status === "completed") {
          let commentsSourceUrls: CommentsSourceUrls[] | undefined = undefined;

          if (status.project_ids && status.project_ids.length > 0) {
            try {
              commentsSourceUrls = await fetchCommentsSourceUrls(status.project_ids);
            } catch (error) {
              console.warn("Unable to load comments source URLs", error);
            }
          }

          if (pollingTokenRef.current !== pollingToken) return;

          setState({
            step: "complete",
            success: true,
            message: `Successfully imported ${status.project_ids?.length || 1} project(s)`,
            commentsSourceUrls,
          });
          onImportComplete();
        } else if (status.status === "failed") {
          setState({
            step: "complete",
            success: false,
            message: status.error || "Import failed",
            commentsSourceUrls: undefined,
          });
        } else {
          // Continue polling
          pollTimeoutRef.current = window.setTimeout(() => {
            void poll();
          }, 1000);
        }
      } catch (error: any) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (pollingTokenRef.current !== pollingToken) return;
        setState({
          step: "complete",
          success: false,
          message: error.message || "Failed to check import status",
          commentsSourceUrls: undefined,
        });
      } finally {
        if (pollControllerRef.current === controller) {
          pollControllerRef.current = null;
        }
      }
    };

    void poll();
  };

  const toggleProjectSelection = (relativePath: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(relativePath)) {
        next.delete(relativePath);
      } else {
        next.add(relativePath);
      }
      return next;
    });
  };

  const selectAll = () => {
    if (state.step === "review") {
      const allPaths = state.analysis.projects.map((p) => p.relative_path);
      setSelectedPaths(new Set(allPaths));
    }
  };

  const deselectAll = () => {
    setSelectedPaths(new Set());
  };

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!isDialogSubmitShortcut(event)) {
      return;
    }

    if (state.step === "input") {
      if (mode === "github" && url.trim()) {
        event.preventDefault();
        void analyzeRepo();
      } else if (mode === "upload" && file) {
        event.preventDefault();
        void uploadArchive();
      }
      return;
    }

    if (state.step === "review") {
      const canImport = state.analysis.import_type === "type1" || selectedPaths.size > 0;
      if (!canImport) {
        return;
      }

      event.preventDefault();
      void startImport();
      return;
    }

    if (state.step === "complete") {
      event.preventDefault();
      handleClose();
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(open) => {
        if (!open) handleClose();
        else onOpenChange(true);
      }}
    >
      <DialogContent className="max-w-lg" onKeyDown={handleDialogKeyDown}>
        {state.step === "input" && (
          <>
            <DialogHeader>
              <DialogTitle>Import Project</DialogTitle>
              <DialogDescription>
                Import KiCAD projects from a GitHub repository or a local .zip archive.
              </DialogDescription>
            </DialogHeader>

            <div className="flex gap-2 py-1">
              <Button
                variant={mode === "github" ? "secondary" : "ghost"}
                size="sm"
                onClick={() => setMode("github")}
              >
                From GitHub
              </Button>
              <Button
                variant={mode === "upload" ? "secondary" : "ghost"}
                size="sm"
                onClick={() => setMode("upload")}
              >
                Upload .zip
              </Button>
            </div>

            {mode === "github" ? (
              <>
                <div className="grid gap-4 py-4">
                  <div className="grid grid-cols-4 items-center gap-4">
                    <Label htmlFor="url" className="text-right">
                      GitHub URL
                    </Label>
                    <Input
                      id="url"
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      placeholder="https://github.com/username/repo"
                      className="col-span-3"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.metaKey && !e.ctrlKey && url.trim()) {
                          e.preventDefault();
                          void analyzeRepo();
                        }
                      }}
                    />
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={handleClose}>
                    Cancel
                  </Button>
                  <Button onClick={analyzeRepo} disabled={!url.trim()}>
                    Analyze
                  </Button>
                </div>
              </>
            ) : (
              <>
                <div className="grid gap-4 py-4">
                  <div className="grid gap-2">
                    <Label htmlFor="archive">Project archive (.zip)</Label>
                    <Input
                      id="archive"
                      type="file"
                      accept=".zip,application/zip"
                      onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    />
                    {file && (
                      <p className="text-xs text-muted-foreground truncate">
                        Selected: {file.name}
                      </p>
                    )}
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="upload-name">Project name (optional)</Label>
                    <Input
                      id="upload-name"
                      value={uploadName}
                      onChange={(e) => setUploadName(e.target.value)}
                      placeholder="Defaults to the archive filename"
                    />
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={handleClose}>
                    Cancel
                  </Button>
                  <Button onClick={uploadArchive} disabled={!file}>
                    Upload
                  </Button>
                </div>
              </>
            )}
          </>
        )}

        {state.step === "uploading" && (
          <>
            <DialogHeader>
              <DialogTitle>Uploading Project</DialogTitle>
              <DialogDescription>Uploading {state.filename}…</DialogDescription>
            </DialogHeader>
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          </>
        )}

        {state.step === "analyzing" && (
          <>
            <DialogHeader>
              <DialogTitle>Analyzing Repository</DialogTitle>
              <DialogDescription>
                {state.status?.message || "Starting analysis..."}
              </DialogDescription>
            </DialogHeader>

            {!state.status ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : (
              <div className="space-y-4 py-4">
                <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
                  {/* Analysis involves cloning which has progress, so we can use state.status.percent if available */}
                  <div
                    className="h-full bg-primary transition-all duration-300"
                    style={{ width: `${state.status.percent || 0}%` }}
                  />
                </div>

                <div className="max-h-32 overflow-y-auto text-sm font-mono bg-muted p-2 rounded">
                  {state.status.logs?.slice(-5).map((log, i) => (
                    <div key={i} className="text-muted-foreground">
                      {log}
                    </div>
                  ))}
                  {(!state.status.logs || state.status.logs.length === 0) && (
                    <span className="text-muted-foreground italic">Starting analysis...</span>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {state.step === "review" && (
          <>
            <DialogHeader>
              <DialogTitle>
                {state.analysis.import_type === "type1"
                  ? "Single Project Detected"
                  : "Multiple Projects Detected"}
              </DialogTitle>
              <DialogDescription>
                {state.analysis.import_type === "type1"
                  ? `Found 1 KiCAD project at the root of ${state.analysis.repo_name}.`
                  : `Found ${state.analysis.projects.length} KiCAD projects in ${state.analysis.repo_name}. Select which to import.`}
              </DialogDescription>
            </DialogHeader>

            {state.analysis.import_type === "type2" && (
              <div className="flex items-center justify-between py-2">
                <span className="text-sm text-muted-foreground">
                  {selectedPaths.size} of {state.analysis.projects.length}{" "}
                  selected
                </span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={selectAll}>
                    Select All
                  </Button>
                  <Button variant="outline" size="sm" onClick={deselectAll}>
                    Deselect All
                  </Button>
                </div>
              </div>
            )}

            <div className="max-h-64 overflow-y-auto border rounded-md">
              {state.analysis.projects.map((project) => (
                <div
                  key={project.relative_path}
                  className="flex items-center gap-3 p-3 border-b last:border-b-0 hover:bg-muted/50"
                >
                  {state.analysis.import_type === "type2" && (
                    <Checkbox
                      checked={selectedPaths.has(project.relative_path)}
                      onCheckedChange={() =>
                        toggleProjectSelection(project.relative_path)
                      }
                    />
                  )}
                  {state.analysis.import_type === "type1" && (
                    <Check className="h-4 w-4 text-green-500" />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="font-medium truncate">{project.name}</p>
                    <p className="text-sm text-muted-foreground truncate">
                      {project.relative_path === "."
                        ? "Root directory"
                        : project.relative_path}
                    </p>
                  </div>
                  <div className="flex gap-2 text-xs text-muted-foreground">
                    {project.has_schematic && (
                      <span className="px-2 py-1 bg-secondary rounded">
                        SCH
                      </span>
                    )}
                    {project.has_pcb && (
                      <span className="px-2 py-1 bg-secondary rounded">
                        PCB
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                onClick={startImport}
                disabled={
                  state.analysis.import_type === "type2" &&
                  selectedPaths.size === 0
                }
              >
                Import
                {state.analysis.import_type === "type2" &&
                  selectedPaths.size > 0 && ` (${selectedPaths.size})`}
              </Button>
            </div>
          </>
        )}

        {state.step === "importing" && (
          <>
            <DialogHeader>
              <DialogTitle>Importing Projects</DialogTitle>
              <DialogDescription>{state.status.message}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary transition-all duration-300"
                  style={{ width: `${state.status.percent}%` }}
                />
              </div>
              <div className="max-h-32 overflow-y-auto text-sm font-mono bg-muted p-2 rounded">
                {state.status.logs?.slice(-5).map((log, i) => (
                  <div key={i} className="text-muted-foreground">
                    {log}
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {state.step === "complete" && (
          <>
            <DialogHeader>
              <DialogTitle>
                {state.success ? "Import Complete" : "Import Failed"}
              </DialogTitle>
              <DialogDescription>{state.message}</DialogDescription>
            </DialogHeader>
            <div className="flex items-center justify-center py-6">
              {state.success ? (
                <div className="h-16 w-16 rounded-full bg-green-100 flex items-center justify-center">
                  <Check className="h-8 w-8 text-green-600" />
                </div>
              ) : (
                <div className="h-16 w-16 rounded-full bg-red-100 flex items-center justify-center">
                  <AlertCircle className="h-8 w-8 text-red-600" />
                </div>
              )}
            </div>

            {state.success && state.commentsSourceUrls && state.commentsSourceUrls.length > 0 && (
              <div className="space-y-3 rounded-md border bg-muted/30 p-3">
                <p className="text-sm font-medium">KiCad REST URL Helpers</p>
                <p className="text-xs text-muted-foreground">
                  Enter these values in KiCad Comments Source Settings for REST mode.
                </p>
                <div className="max-h-44 overflow-y-auto space-y-3">
                  {state.commentsSourceUrls.map((entry) => (
                    <div key={entry.project_id} className="rounded-md border bg-background p-3 space-y-2">
                      <p className="text-xs font-medium">
                        {entry.project_name} ({entry.project_id})
                      </p>
                      <div className="space-y-1 text-xs font-mono text-muted-foreground break-all">
                        <p>List: {entry.list_url}</p>
                        <p>Patch: {entry.patch_url_template}</p>
                        <p>Reply: {entry.reply_url_template}</p>
                        <p>Delete: {entry.delete_url_template}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex justify-end">
              <Button onClick={handleClose}>Close</Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
