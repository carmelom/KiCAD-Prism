"""
Project Import Service for KiCAD Prism

Handles Type-1 (single project) and Type-2 (multiple projects) imports.
"""
import io
import os
import subprocess
import shutil
import tempfile
import uuid
import zipfile
import threading
from pathlib import Path
from typing import List, Optional, Dict
from dataclasses import dataclass
from git import Repo, RemoteProgress
from app.services import project_service, path_config_service
from app.services.workspace_service import workspace
from app.services.archive_utils import (
    MAX_UPLOAD_BYTES,
    find_content_root,
    safe_extract_zip,
    sanitize_project_name,
)


@dataclass
class DiscoveredProject:
    """A KiCAD project discovered within a repository."""
    name: str
    relative_path: str
    full_path: str
    has_schematic: bool
    has_pcb: bool


@dataclass
class AnalysisResult:
    """Result of analyzing a repository for import."""
    repo_name: str
    repo_url: str
    import_type: str  # "type1" or "type2"
    projects: List[DiscoveredProject]
    temp_path: Optional[str] = None  # For cleanup after analysis


# Global job store for import operations
jobs: Dict[str, dict] = {}


def _persist_job(job_id: str) -> None:
    job = jobs.get(job_id)
    if job:
        workspace.update_job(
            job_id,
            status=job.get("status", "running"),
            message=job.get("message", ""),
            percent=job.get("percent", 0),
            **{
                key: value
                for key, value in job.items()
                if key not in {"job_id", "status", "message", "percent"}
            },
        )


def has_ssh_key() -> bool:
    """Check if a default SSH key exists."""
    ssh_dir = Path.home() / ".ssh"
    key_types = ["id_ed25519", "id_rsa"]
    for kt in key_types:
        if (ssh_dir / kt).exists():
            return True
    return False


class CloneProgress(RemoteProgress):
    """Git progress callback for clone operations."""
    
    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id
    
    def update(self, op_code, cur_count, max_count=None, message=''):
        if self.job_id in jobs:
            job = jobs[self.job_id]
            percent = 0
            if max_count and max_count > 0:
                percent = min((cur_count / max_count) * 100, 99)
            job['percent'] = int(percent)
            job['message'] = message or f"Cloning... {int(percent)}%"
            if message:
                job['logs'].append(f"[GIT] {message}")
            _persist_job(self.job_id)


def is_excluded_directory(dir_name: str) -> bool:
    """Check if directory should be excluded from project discovery."""
    excluded = {
        'archive', 'archived', 'old', 'backup', 'backups',
        'obsolete', 'deprecated', 'trash', '.git', '__pycache__',
        'node_modules', '.venv', 'venv', '.env'
    }
    return dir_name.lower() in excluded or dir_name.startswith('.')


def discover_projects_from_repo(repo: Repo) -> List[DiscoveredProject]:
    """
    Discover KiCAD projects by inspecting the Git tree directly (no-checkout).
    Returns list of DiscoveredProject.
    """
    # Get all files in the repo recursively
    try:
        all_files = repo.git.ls_tree('-r', 'HEAD', '--name-only').splitlines()
    except Exception:
        # Fallback for empty repos or other issues
        return []
    
    # Map directory -> list of filenames
    dir_map = {}
    for fpath in all_files:
        p = Path(fpath)
        # Handle relative path correctly (relative to repo root)
        dir_path = p.parent.as_posix() # Use as_posix for consistency
        filename = p.name
        
        if dir_path not in dir_map:
            dir_map[dir_path] = []
        dir_map[dir_path].append(filename)
        
    projects = []
    for dir_path, filenames in dir_map.items():
        # Skip if any part of the path is excluded
        should_exclude = False
        parts = dir_path.split('/')
        if dir_path != ".":
            for part in parts:
                if is_excluded_directory(part):
                    should_exclude = True
                    break
        if should_exclude:
            continue
            
        pro_files = [f for f in filenames if f.endswith(".kicad_pro")]
        for pro_file in pro_files:
            has_sch = any(f.endswith(".kicad_sch") for f in filenames)
            has_pcb = any(f.endswith(".kicad_pcb") for f in filenames)
            
            projects.append(DiscoveredProject(
                name=Path(pro_file).stem,
                relative_path=dir_path if dir_path != "." else ".",
                full_path="", # No checkout path
                has_schematic=has_sch,
                has_pcb=has_pcb
            ))
            
    # Sort by path depth (shallow first) then by name
    projects.sort(key=lambda p: (0 if p.relative_path == "." else len(p.relative_path.split('/')), p.name.lower()))
    
    return projects


def analyze_repository(repo_url: str) -> AnalysisResult:
    """
    Analyze a repository to determine import type and discover projects.
    Performs a shallow clone to a temporary directory.
    """
    repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
    
    # Create temp directory for analysis
    temp_dir = tempfile.mkdtemp(prefix="kicad_analyze_")
    clone_path = Path(temp_dir) / repo_name
    
    try:
        # Shallow clone for analysis
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        # Trust On First Use (TOFU) for SSH
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        
        repo = Repo.clone_from(
            repo_url,
            str(clone_path),
            depth=1,
            single_branch=True,
            no_checkout=True,
            filter='blob:none',
            env=env
        )
        
        # Discover projects from tree
        projects = discover_projects_from_repo(repo)
        
        # Determine import type
        # Type-1: Single .kicad_pro at root (relative_path == ".")
        # Type-2: Multiple projects or project not at root
        import_type = "type2"
        if len(projects) == 1 and projects[0].relative_path == ".":
            import_type = "type1"
        
        return AnalysisResult(
            repo_name=repo_name,
            repo_url=repo_url,
            import_type=import_type,
            projects=projects,
            temp_path=temp_dir
        )
        
    except Exception:
        # Cleanup on error
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _run_analyze_job(job_id: str, repo_url: str):
    """
    Background job: Analyze repository.
    """
    job = jobs[job_id]
    
    try:
        job['logs'].append(f"Analyzing {repo_url}...")
        _persist_job(job_id)
        
        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        temp_dir = tempfile.mkdtemp(prefix="kicad_analyze_")
        clone_path = Path(temp_dir) / repo_name
        
        job['logs'].append("Cloning repository (blobless/no-checkout)...")
        
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        
        repo = Repo.clone_from(
            repo_url,
            str(clone_path),
            depth=1,
            single_branch=True,
            no_checkout=True,
            filter='blob:none',
            progress=CloneProgress(job_id),
            env=env
        )
        
        job['logs'].append("Discovering KiCAD projects from tree...")
        _persist_job(job_id)
        projects = discover_projects_from_repo(repo)
        
        import_type = "type2"
        if len(projects) == 1 and projects[0].relative_path == ".":
            import_type = "type1"
            
        job['logs'].append(f"Found {len(projects)} project(s). Type: {import_type}")
        
        # Store result in job
        job['result'] = {
            "repo_name": repo_name,
            "repo_url": repo_url,
            "import_type": import_type,
            "projects": [
                {
                    "name": p.name,
                    "relative_path": p.relative_path,
                    "has_schematic": p.has_schematic,
                    "has_pcb": p.has_pcb
                }
                for p in projects
            ],
            # We don't pass temp_path here as we'll cleanup immediately or handled differently
        }
        
        # Cleanup temp dir immediately since we have the metadata
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            
        job['status'] = 'completed'
        job['percent'] = 100
        job['message'] = "Analysis complete."
        _persist_job(job_id)
        
    except Exception as e:
        job['status'] = 'failed'
        job['error'] = str(e)
        job['logs'].append(f"Error: {str(e)}")
        _persist_job(job_id)


def cleanup_analysis_temp(analysis: AnalysisResult):
    """Clean up temporary directory used for analysis."""
    if analysis.temp_path and os.path.exists(analysis.temp_path):
        shutil.rmtree(analysis.temp_path, ignore_errors=True)


def resolve_cached_paths(project_path: str) -> dict:
    """Resolve and return cached path info for a project directory."""
    try:
        resolved = path_config_service.resolve_paths(project_path)
        sch = resolved.schematic
        pcb = resolved.pcb
        thumb = resolved.thumbnail_dir
        jobset = resolved.jobset_path
        # Make paths relative to project_path
        def _rel(abs_path):
            if not abs_path:
                return None
            try:
                return os.path.relpath(abs_path, project_path)
            except ValueError:
                return None
        # Thumbnail: resolve to first image if directory
        thumb_rel = None
        if thumb:
            if os.path.isfile(thumb):
                thumb_rel = _rel(thumb)
            elif os.path.isdir(thumb):
                for f in sorted(os.listdir(thumb)):
                    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        thumb_rel = _rel(os.path.join(thumb, f))
                        break
        design_dir = resolved.design_outputs_dir
        has_3d = False
        has_ibom = False
        if design_dir and os.path.isdir(design_dir):
            for f in os.listdir(design_dir):
                fl = f.lower()
                if fl.endswith(('.glb', '.step', '.stp')):
                    has_3d = True
                if 'ibom' in fl and fl.endswith('.html'):
                    has_ibom = True
            model_dir = os.path.join(design_dir, '3DModel')
            if os.path.isdir(model_dir):
                for f in os.listdir(model_dir):
                    if f.lower().endswith(('.glb', '.step', '.stp')):
                        has_3d = True
        return {
            'schematic_rel': _rel(sch),
            'pcb_rel': _rel(pcb),
            'thumbnail_rel': thumb_rel,
            'jobset_rel': _rel(jobset),
            'has_3d_model': has_3d,
            'has_ibom': has_ibom,
        }
    except Exception:
        return {}


def generate_thumbnail_for_project(project_path: str, logs_list: Optional[List[str]] = None) -> bool:
    """
    Find the main .kicad_pcb file and run kicad-cli pcb render to generate a thumbnail.
    """
    try:
        resolved = path_config_service.resolve_paths(project_path)
        pcb_file = resolved.pcb
        if not pcb_file or not os.path.exists(pcb_file):
            if logs_list is not None:
                logs_list.append(f"No .kicad_pcb file found to generate thumbnail for {project_path}")
            return False
        
        # Check standard paths for kicad-cli
        cli_path = "kicad-cli"
        # Check environment variable
        for var in ("KICAD_CLI_PATH", "KICAD_CLI"):
            val = os.environ.get(var, "").strip()
            if val and os.path.exists(val):
                cli_path = val
                break
        else:
            # Check standard Mac path
            mac_path = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
            if os.path.exists(mac_path):
                cli_path = mac_path
            else:
                # Check PATH
                which_cli = shutil.which("kicad-cli")
                if which_cli:
                    cli_path = which_cli

        if logs_list is not None:
            logs_list.append(f"Generating thumbnail using {cli_path} for PCB: {pcb_file}")

        # Create assets/thumbnail directory
        thumbnail_dir = Path(project_path) / "assets" / "thumbnail"
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        output_path = thumbnail_dir / "thumbnail.png"
        
        cmd = [
            cli_path,
            "pcb",
            "render",
            "--quality", "high",
            "--floor",
            "--perspective",
            "--rotate", "-45,0,45",
            "--width", "800",
            "--height", "600",
            "-o", str(output_path),
            pcb_file
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False
        )
        
        if result.returncode != 0:
            if logs_list is not None:
                logs_list.append(f"kicad-cli render failed (code {result.returncode}): {result.stderr[:400]}")
            return False
            
        if logs_list is not None:
            logs_list.append(f"Successfully generated thumbnail at {output_path}")
        return True
        
    except Exception as e:
        if logs_list is not None:
            logs_list.append(f"Exception during thumbnail generation: {e}")
        return False



def _run_import_job(job_id: str, repo_url: str, import_type: str, 
                    selected_paths: Optional[List[str]] = None):
    """
    Background job: Clone repository and register projects.
    """
    job = jobs[job_id]
    
    try:
        # Extract repo name
        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        
        # Determine target directory based on type
        if import_type == "type1":
            base_path = Path(project_service.PROJECTS_ROOT) / "type1"
        else:
            base_path = Path(project_service.PROJECTS_ROOT) / "type2"
        
        target_path = base_path / repo_name
        target_path_abs = str(target_path.resolve())
        
        # Check if already exists via workspace DB
        existing_repo = workspace.get_repository_by_url(repo_url)
        if existing_repo:
            job['status'] = 'failed'
            job['error'] = f"Repository '{repo_name}' is already imported"
            job['logs'].append(f"Error: Repository with URL {repo_url} already exists")
            _persist_job(job_id)
            return

        if target_path.exists():
            # Stranded directory with no DB entry — remove and re-clone
            job['logs'].append(f"Removing stranded directory: {target_path}")
            _persist_job(job_id)
            try:
                shutil.rmtree(target_path)
            except Exception as e:
                job['status'] = 'failed'
                job['error'] = f"Failed to remove stranded directory: {e}"
                _persist_job(job_id)
                return
        
        # Ensure base directory exists
        base_path.mkdir(parents=True, exist_ok=True)
        
        # Clone repository
        job['logs'].append(f"Cloning {repo_url}...")
        _persist_job(job_id)
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        # Trust On First Use (TOFU) for SSH
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        
        Repo.clone_from(
            repo_url,
            str(target_path),
            progress=CloneProgress(job_id),
            env=env
        )
        
        job['logs'].append("Clone complete. Registering projects...")
        _persist_job(job_id)
        
        # Register repository in workspace DB
        repo_id = workspace.register_repository(
            name=repo_name,
            url=repo_url,
            clone_path_abs=str(target_path),
            import_type='single' if import_type == 'type1' else 'multi',
        )
        
        imported_ids = []
        
        if import_type == "type1":
            # Generate thumbnail before resolving paths
            generate_thumbnail_for_project(str(target_path), job['logs'])
            cached = resolve_cached_paths(str(target_path))
            project_id = workspace.register_project(
                repo_id=repo_id,
                name=repo_name,
                relative_path='.',
                description=f"Project {repo_name}",
                **cached,
            )
            imported_ids.append(project_id)
            job['logs'].append(f"Registered Type-1 project: {project_id}")
            
        else:
            # Type-2: Register selected subprojects
            if not selected_paths:
                job['status'] = 'failed'
                job['error'] = "No projects selected for Type-2 import"
                _persist_job(job_id)
                return
            
            for rel_path in selected_paths:
                full_project_path = target_path / rel_path
                # Generate thumbnail before resolving paths
                generate_thumbnail_for_project(str(full_project_path), job['logs'])
                pro_files = list(full_project_path.glob("*.kicad_pro"))
                board_name = pro_files[0].stem if pro_files else os.path.basename(rel_path)
                cached = resolve_cached_paths(str(full_project_path))
                project_id = workspace.register_project(
                    repo_id=repo_id,
                    name=board_name,
                    relative_path=rel_path,
                    description=f"{repo_name} / {board_name}",
                    **cached,
                )
                imported_ids.append(project_id)
                job['logs'].append(f"Registered Type-2 subproject: {project_id}")
        
        job['project_ids'] = imported_ids
        job['status'] = 'completed'
        job['percent'] = 100
        job['message'] = f"Imported {len(imported_ids)} project(s)"
        job['logs'].append("Import completed successfully.")
        _persist_job(job_id)
        
    except Exception as e:
        job['status'] = 'failed'
        job['error'] = str(e)
        job['logs'].append(f"Error: {str(e)}")
        _persist_job(job_id)
        
        # Cleanup on failure
        if target_path.exists():
            try:
                shutil.rmtree(target_path)
            except:
                pass


def start_import_job(repo_url: str, import_type: str, 
                     selected_paths: Optional[List[str]] = None) -> str:
    """
    Start an asynchronous import job.
    Returns job ID for polling.
    """
    job_id = str(uuid.uuid4())
    
    jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "message": "Starting import...",
        "percent": 0,
        "project_ids": [],
        "error": None,
        "logs": [f"Starting import of {repo_url}"],
        "type": "import",
        "repo_url": repo_url,
        "import_type": import_type
    }
    workspace.create_job(
        job_id,
        "import",
        status=jobs[job_id]["status"],
        message=jobs[job_id]["message"],
        percent=jobs[job_id]["percent"],
        **{
            key: value
            for key, value in jobs[job_id].items()
            if key not in {"job_id", "status", "message", "percent"}
        },
    )
    
    thread = threading.Thread(
        target=_run_import_job,
        args=(job_id, repo_url, import_type, selected_paths)
    )
    thread.daemon = True
    thread.start()
    
    return job_id


def start_analyze_job(repo_url: str) -> str:
    """
    Start an asynchronous analysis job.
    Returns job ID.
    """
    job_id = str(uuid.uuid4())
    
    jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "message": "Starting analysis...",
        "percent": 0,
        "error": None,
        "logs": [f"Starting analysis of {repo_url}"],
        "type": "analyze",
        "repo_url": repo_url
    }
    workspace.create_job(
        job_id,
        "analyze",
        status=jobs[job_id]["status"],
        message=jobs[job_id]["message"],
        percent=jobs[job_id]["percent"],
        **{
            key: value
            for key, value in jobs[job_id].items()
            if key not in {"job_id", "status", "message", "percent"}
        },
    )
    
    thread = threading.Thread(
        target=_run_analyze_job,
        args=(job_id, repo_url)
    )
    thread.daemon = True
    thread.start()
    
    return job_id


def get_job_status(job_id: str) -> Optional[dict]:
    """Get the current status of an import or workflow job."""
    # Check import jobs first
    job = jobs.get(job_id)
    if job:
        return job
    
    # Then check workflow jobs from project_service
    return workspace.get_job(job_id) or project_service.jobs.get(job_id)


def sync_project(project_id: str) -> dict:
    """
    Sync a project with its remote repository.
    For Type-1: pulls the project repo.
    For Type-2: pulls the parent repo.
    """
    row = workspace.get_project_by_id(project_id)
    if not row:
        return {"status": "error", "message": "Project not found"}

    import_type = row.get('import_type') or 'single'
    sync_path = row.get('parent_repo_path') if import_type == 'multi' else row.get('path')

    if not sync_path or not os.path.exists(sync_path):
        return {"status": "error", "message": f"Project path not found: {sync_path}"}

    try:
        repo = Repo(sync_path)
        origin = repo.remote('origin')
        
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        
        fetch_info = origin.fetch(env=env)
        origin.pull(env=env)

        # Refresh cached paths after sync
        path_config_service.clear_config_cache()
        project_path = row.get('path', '')
        if project_path and os.path.isdir(project_path):
            # Generate/refresh thumbnail on sync
            generate_thumbnail_for_project(project_path)
            cached = resolve_cached_paths(project_path)
            workspace.update_project(project_id, **cached)

        # Update repo last_synced_at
        workspace.update_repository_synced(row.get('repo_id', ''))
        
        return {
            "status": "success",
            "message": f"Synced {len(fetch_info)} ref(s)",
            "path": sync_path
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Upload import (web client)
# ---------------------------------------------------------------------------
def import_uploaded_archive(
    archive_bytes: bytes,
    filename: str,
    display_name: Optional[str] = None,
) -> dict:
    """Import a KiCad project from an uploaded .zip archive.

    Unpacks safely, initializes a git repo (so commit-based features work like a
    cloned project), discovers project(s), moves them into the projects store, and
    registers them in the workspace DB. Returns the created project id(s).
    """
    if len(archive_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("Uploaded archive exceeds the maximum allowed size")
    if not zipfile.is_zipfile(io.BytesIO(archive_bytes)):
        raise ValueError("Uploaded file is not a valid .zip archive")

    name = sanitize_project_name(display_name or filename)
    temp_dir = tempfile.mkdtemp(prefix="kicad_upload_")
    target_path: Optional[Path] = None

    try:
        extract_dir = Path(temp_dir) / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            safe_extract_zip(zf, extract_dir)

        content_root = find_content_root(extract_dir)

        # Initialize a git repo + initial commit so commit-scoped endpoints
        # (history, per-commit file reads) behave like a cloned project.
        repo = Repo.init(str(content_root))
        with repo.config_writer() as cw:
            cw.set_value("user", "email", "prism@localhost")
            cw.set_value("user", "name", "KiCAD Prism")
        repo.git.add("-A")
        repo.index.commit("Initial import from uploaded archive")

        projects = discover_projects_from_repo(repo)
        if not projects:
            raise ValueError("No KiCad project (.kicad_pro) found in the uploaded archive")

        import_type = "type2"
        if len(projects) == 1 and projects[0].relative_path == ".":
            import_type = "type1"

        base_path = Path(project_service.PROJECTS_ROOT) / ("type1" if import_type == "type1" else "type2")
        base_path.mkdir(parents=True, exist_ok=True)

        # Ensure a unique destination directory / name.
        unique_name = name
        counter = 1
        while (base_path / unique_name).exists():
            counter += 1
            unique_name = f"{name}-{counter}"
        target_path = base_path / unique_name
        shutil.move(str(content_root), str(target_path))

        repo_id = workspace.register_repository(
            name=unique_name,
            url=f"upload://{unique_name}",
            clone_path_abs=str(target_path),
            import_type="single" if import_type == "type1" else "multi",
        )

        imported_ids: List[str] = []
        if import_type == "type1":
            cached = _resolve_cached_paths(str(target_path))
            project_id = workspace.register_project(
                repo_id=repo_id,
                name=unique_name,
                relative_path=".",
                description=f"Uploaded project {unique_name}",
                **cached,
            )
            imported_ids.append(project_id)
        else:
            for proj in projects:
                rel_path = proj.relative_path
                full_project_path = target_path / rel_path if rel_path != "." else target_path
                pro_files = list(full_project_path.glob("*.kicad_pro"))
                board_name = pro_files[0].stem if pro_files else proj.name
                cached = _resolve_cached_paths(str(full_project_path))
                project_id = workspace.register_project(
                    repo_id=repo_id,
                    name=board_name,
                    relative_path=rel_path,
                    description=f"{unique_name} / {board_name}",
                    **cached,
                )
                imported_ids.append(project_id)

        return {
            "status": "completed",
            "import_type": import_type,
            "repo_id": repo_id,
            "project_ids": imported_ids,
            "name": unique_name,
            "message": f"Imported {len(imported_ids)} project(s) from archive",
        }

    except Exception:
        # Roll back any moved content on failure.
        if target_path is not None and target_path.exists():
            shutil.rmtree(target_path, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
