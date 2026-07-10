from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from git import Actor, Repo


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import git_service  # noqa: E402


AUTHOR = Actor("Prism Test", "prism@example.com")


def _commit_all(repo: Repo, message: str):
    repo.git.add(A=True)
    return repo.index.commit(message, author=AUTHOR, committer=AUTHOR)


class GitBranchViewingTests(unittest.TestCase):
    def test_lists_only_branches_containing_the_subproject_without_switching_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = Repo.init(root)
            (root / "README.md").write_text("root", encoding="utf-8")
            initial = _commit_all(repo, "initial")
            repo.create_head("without-project", initial)

            project_file = root / "boards" / "demo" / "README.md"
            project_file.parent.mkdir(parents=True)
            project_file.write_text("project", encoding="utf-8")
            project_commit = _commit_all(repo, "add project")
            repo.create_head("feature/project", project_commit)

            original_head = repo.head.commit.hexsha
            branches = git_service.get_branches(str(root), "boards/demo")

            self.assertEqual(repo.head.commit.hexsha, original_head)
            self.assertEqual(
                {branch["ref"] for branch in branches["branches"]},
                {repo.active_branch.name, "feature/project"},
            )
            self.assertEqual(branches["default_ref"], repo.active_branch.name)

    def test_lists_fetched_remote_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source"
            source = Repo.init(source_path)
            (source_path / "README.md").write_text("initial", encoding="utf-8")
            initial = _commit_all(source, "initial")
            source.create_head("remote-feature", initial)

            remote_path = root / "remote.git"
            source.clone(str(remote_path), bare=True)
            checkout_path = root / "checkout"
            checkout = Repo.clone_from(str(remote_path), checkout_path)
            checkout.remotes.origin.fetch()

            branches = git_service.get_branches(str(checkout_path))

            self.assertIn("origin/remote-feature", {branch["ref"] for branch in branches["branches"]})

    def test_scopes_history_and_distance_to_the_selected_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = Repo.init(root)
            (root / "README.md").write_text("initial", encoding="utf-8")
            initial = _commit_all(repo, "initial")
            repo.create_head("feature/project", initial)
            repo.git.checkout("feature/project")
            (root / "README.md").write_text("feature", encoding="utf-8")
            feature_commit = _commit_all(repo, "feature work")
            repo.git.checkout(repo.heads[0].name)

            commits = git_service.get_commits_list(str(root), ref="feature/project")

            self.assertEqual(commits[0]["full_hash"], feature_commit.hexsha)
            self.assertEqual(
                git_service.get_commit_distance(str(root), initial.hexsha, ref="feature/project"),
                1,
            )


if __name__ == "__main__":
    unittest.main()
