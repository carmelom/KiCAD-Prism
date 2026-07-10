import os
from fastapi import HTTPException
from git import Repo
from git.exc import BadName, GitCommandError
from typing import Dict, Any
import datetime


def _open_repo(repo_path: str) -> Repo:
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail=f"Repository not found at {repo_path}")

    try:
        return Repo(repo_path)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Git error: {str(error)}") from error


def _serialize_commit(commit) -> Dict[str, str]:
    return {
        "hash": commit.hexsha[:7],
        "full_hash": commit.hexsha,
        "author": commit.author.name,
        "email": commit.author.email,
        "date": datetime.datetime.fromtimestamp(commit.committed_date).isoformat(),
        "message": commit.message.strip(),
    }


def _commit_has_path(commit, relative_path: str | None) -> bool:
    if not relative_path:
        return True
    try:
        commit.tree / relative_path
        return True
    except Exception:
        return False


def _resolve_commit(repo: Repo, ref: str | None):
    try:
        return repo.commit(ref or "HEAD")
    except BadName as error:
        raise HTTPException(status_code=404, detail=f"Git ref not found: {ref}") from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Git error: {str(error)}") from error


def _get_commits(repo_path: str, limit: int, relative_path: str = None, ref: str = None):
    repo = _open_repo(repo_path)
    iter_kwargs = {"max_count": limit}
    if ref:
        _resolve_commit(repo, ref)
        iter_kwargs["rev"] = ref
    if relative_path:
        iter_kwargs["paths"] = relative_path

    try:
        return [_serialize_commit(commit) for commit in repo.iter_commits(**iter_kwargs)]
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Git error: {str(error)}") from error


def get_commits_list_filtered(repo_path: str, relative_path: str = None, limit: int = 50, ref: str = None):
    """
    Get list of commits from repository, optionally filtered to a subdirectory.
    For Type-2 projects, relative_path scopes commits to the subproject.
    """
    return _get_commits(repo_path, limit, relative_path, ref)


def _count_tree_entries(commit, relative_path: str) -> int | None:
    try:
        target = commit.tree / relative_path
        if target.type == "tree":
            return len(list(target.traverse()))
    except Exception:
        return None
    return None


def _commit_touches_path(repo: Repo, commit, relative_path: str) -> bool:
    try:
        args = ["--no-commit-id", "--name-only", "-r", "-m"]
        if not commit.parents:
            args.append("--root")
        output = repo.git.diff_tree(*args, commit.hexsha, "--", relative_path)
        return bool(output.strip())
    except GitCommandError:
        return False


def _is_ancestor(repo: Repo, ancestor: str, descendant: str) -> bool:
    try:
        repo.git.merge_base("--is-ancestor", ancestor, descendant)
        return True
    except GitCommandError:
        return False


def _get_releases(repo_path: str, relative_path: str = None, ref: str = None):
    repo = _open_repo(repo_path)
    releases = []
    try:
        resolved_ref = _resolve_commit(repo, ref).hexsha if ref else None
        for tag in repo.tags:
            commit = tag.commit
            if resolved_ref and not _is_ancestor(repo, commit.hexsha, resolved_ref):
                continue
            if relative_path and not _commit_touches_path(repo, commit, relative_path):
                continue
            release = {
                "tag": tag.name,
                "commit_hash": commit.hexsha[:7],
                "date": datetime.datetime.fromtimestamp(commit.committed_date).isoformat(),
                "message": commit.message.strip(),
            }
            if relative_path:
                release["subproject_files_changed"] = _count_tree_entries(commit, relative_path)
            releases.append(release)

        releases.sort(key=lambda item: item["date"], reverse=True)
        return releases
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Git error: {str(error)}") from error


def get_releases_filtered(repo_path: str, relative_path: str = None, ref: str = None):
    """
    Get list of Git tags/releases from repository.
    For Type-2 projects, shows file count under relative_path for each tag.
    """
    return _get_releases(repo_path, relative_path, ref)


def get_file_from_commit_with_prefix(repo_path: str, commit_hash: str, file_path: str, relative_prefix: str = None) -> str:
    """
    Get file content from a specific commit.
    For Type-2 projects, relative_prefix is prepended to file_path.
    """
    try:
        repo = Repo(repo_path)
        commit = repo.commit(commit_hash)
        
        # Prepend relative_prefix for Type-2 projects
        full_path = file_path
        if relative_prefix:
            full_path = os.path.join(relative_prefix, file_path)
        
        try:
            blob = commit.tree / full_path
            content = blob.data_stream.read()
            return content.decode('utf-8')
        except KeyError:
            raise HTTPException(status_code=404, detail=f"File {file_path} not found in commit")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Binary file cannot be decoded")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Git error: {str(e)}")


def file_exists_in_commit_with_prefix(repo_path: str, commit_hash: str, file_path: str, relative_prefix: str = None) -> bool:
    """
    Check if a file exists in a specific commit.
    For Type-2 projects, relative_prefix is prepended to file_path.
    """
    try:
        repo = Repo(repo_path)
        commit = repo.commit(commit_hash)
        
        full_path = file_path
        if relative_prefix:
            full_path = os.path.join(relative_prefix, file_path)
        
        try:
            _ = commit.tree / full_path
            return True
        except KeyError:
            return False
    except:
        return False

def get_releases(repo_path: str, ref: str = None):
    """
    Get list of Git tags/releases from repository.
    """
    return _get_releases(repo_path, ref=ref)

def get_commits_list(repo_path: str, limit: int = 50, ref: str = None):
    """
    Get list of commits from repository.
    """
    return _get_commits(repo_path, limit, ref=ref)


def get_commit_distance(repo_path: str, commit_hash: str, relative_path: str = None, ref: str = None) -> int:
    """
    Count commits between the requested commit and HEAD.
    When relative_path is provided, only count commits that affect that path.
    """
    try:
        repo = _open_repo(repo_path)
        repo.commit(commit_hash)
        target_ref = ref or "HEAD"
        repo.commit(target_ref)

        rev_list_args = ["--count", f"{commit_hash}..{target_ref}"]
        if relative_path:
            rev_list_args.extend(["--", relative_path])

        return int(repo.git.rev_list(*rev_list_args).strip() or "0")
    except BadName as error:
        raise HTTPException(status_code=404, detail=f"Commit not found: {commit_hash}") from error
    except GitCommandError as error:
        message = str(error).lower()
        if "bad revision" in message or "unknown revision" in message:
            raise HTTPException(status_code=404, detail=f"Commit not found: {commit_hash}") from error
        raise HTTPException(status_code=500, detail=f"Git error: {str(error)}") from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Git error: {str(error)}") from error


def get_branches(repo_path: str, relative_path: str = None) -> dict[str, Any]:
    """
    Return local and remote branch refs without changing the working tree.
    For Type-2 projects, branches that do not contain the subproject path are omitted.
    """
    repo = _open_repo(repo_path)
    branches: list[dict[str, Any]] = []
    seen_refs: set[str] = set()

    try:
        active_branch = repo.active_branch.name if not repo.head.is_detached else None
    except (TypeError, ValueError):
        active_branch = None

    def add_branch(*, name: str, ref: str, source: str, is_current: bool = False) -> None:
        if ref in seen_refs:
            return
        commit = _resolve_commit(repo, ref)
        if not _commit_has_path(commit, relative_path):
            return
        seen_refs.add(ref)
        branches.append(
            {
                "name": name,
                "ref": ref,
                "source": source,
                "is_current": is_current,
                "hash": commit.hexsha[:7],
                "commit": commit.hexsha,
                "author": commit.author.name,
                "email": commit.author.email,
                "date": datetime.datetime.fromtimestamp(commit.committed_date).isoformat(),
                "message": commit.message.strip(),
            }
        )

    for branch in repo.heads:
        add_branch(
            name=branch.name,
            ref=branch.name,
            source="local",
            is_current=branch.name == active_branch,
        )

    for remote in repo.remotes:
        for remote_ref in remote.refs:
            if remote_ref.remote_head == "HEAD":
                continue
            add_branch(
                name=remote_ref.remote_head,
                ref=remote_ref.name,
                source="remote",
            )

    branches.sort(
        key=lambda item: (
            0 if item["is_current"] else 1,
            0 if item["source"] == "local" else 1,
            item["name"].casefold(),
            item["ref"].casefold(),
        )
    )
    default_branch = next((item for item in branches if item["is_current"]), None) or (branches[0] if branches else None)
    return {
        "branches": branches,
        "current_branch": active_branch,
        "default_ref": default_branch["ref"] if default_branch else None,
    }

def get_file_from_commit(repo_path: str, commit_hash: str, file_path: str) -> str:
    """
    Get file content from a specific commit.
    Returns file content as string.
    """
    try:
        repo = Repo(repo_path)
        commit = repo.commit(commit_hash)
        
        try:
            blob = commit.tree / file_path
            content = blob.data_stream.read()
            return content.decode('utf-8')
        except KeyError:
            raise HTTPException(status_code=404, detail=f"File {file_path} not found in commit")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Binary file cannot be decoded")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Git error: {str(e)}")

def file_exists_in_commit(repo_path: str, commit_hash: str, file_path: str) -> bool:
    """
    Check if a file exists in a specific commit.
    """
    try:
        repo = Repo(repo_path)
        commit = repo.commit(commit_hash)
        try:
            _ = commit.tree / file_path
            return True
        except KeyError:
            return False
    except:
        return False


def sync_with_remote(repo_path: str) -> Dict[str, Any]:
    """
    Sync local repository with remote by performing a git pull.
    
    This fetches and merges the latest changes from the remote tracking branch.
    
    Returns:
        Dict with sync status information including:
        - success: bool
        - previous_commit: str
        - current_commit: str
        - commits_pulled: int
        - message: str
    """
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail=f"Repository not found at {repo_path}")
    
    try:
        repo = Repo(repo_path)
        
        # Get current HEAD before sync
        previous_commit = repo.head.commit.hexsha
        
        # Perform git pull
        origin = repo.remotes.origin
        
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        # Trust On First Use (TOFU) for SSH
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        
        pull_info = origin.pull(env=env)
        
        # Get new HEAD after sync
        current_commit = repo.head.commit.hexsha
        
        # Count how many commits were pulled
        commits_pulled = 0
        if previous_commit != current_commit:
            try:
                commits_pulled = len(list(repo.iter_commits(f'{previous_commit}..{current_commit}')))
            except Exception:
                commits_pulled = 1  # At least one if heads differ
        
        return {
            "success": True,
            "previous_commit": previous_commit[:7],
            "current_commit": current_commit[:7],
            "commits_pulled": commits_pulled,
            "message": f"Successfully pulled {commits_pulled} commit(s) from remote."
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")
