import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from erieiron_common import common, aws_utils
from erieiron_common.common import run_cmd


class GitWrapper:
    def __init__(self, source_root: Path = None):
        if not source_root:
            source_root = Path(tempfile.TemporaryDirectory().name)
            source_root.mkdir(parents=True, exist_ok=False)
        else:
            source_root = Path(source_root)
            source_root.mkdir(parents=True, exist_ok=True)
        
        self.source_root = source_root
        self._venv_initialized = False
    
    def exec(self, *commands) -> 'GitWrapper':
        github_token = get_github_token()
        if not github_token:
            raise Exception("unable to fetch github token from aws secrets")
        
        cmd = ["git"] + list(commands)
        
        try:
            env = os.environ.copy()
            env["GITHUB_TOKEN"] = github_token
            
            cmd = common.strings(cmd)
            
            print(" ".join(cmd))
            result = subprocess.run(
                cmd,
                env=env,
                cwd=str(self.source_root.absolute()) if self.source_root.exists() else None,
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            std_out = e.stdout.strip()
            std_err = e.stderr.strip()
            
            if "nothing to commit" not in std_out:
                logging.error(f"stdout:\n{std_out}")
                logging.error(f"stderr:\n{std_err}")
                
                raise Exception(
                    f"Command failed: {common.safe_join(cmd)}\n"
                    f"stdout:\n{std_out}\n"
                    f"stderr:\n{std_err}"
                )
            else:
                logging.info(std_out)
                logging.info(std_err)
        else:
            logging.info(result.stdout)
            logging.info(result.stderr)
        
        self._last_result = result
        return self
    
    def clone_to_new_repo(self, source_repo, target_repo) -> 'GitWrapper':
        self.clone(source_repo)
        self.exec("remote", "remove", "origin")
        self.exec("remote", "add", "origin", self.wrap_url_with_token(target_repo))
        return self
    
    def clone(self, source_repo) -> 'GitWrapper':
        self.cleanup()
        # self.source_root.mkdir(parents=True)
        return self.exec("clone", self.wrap_url_with_token(source_repo), self.source_root)
    
    def pull(self) -> 'GitWrapper':
        # Detect local changes
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.source_root),
            capture_output=True,
            text=True,
            check=True
        )
        status_output = status_result.stdout.strip()
        has_local_changes = len(status_output) > 0
        
        # Stash local changes if any
        if has_local_changes:
            self.exec("stash", "push", "-m", "auto-stash-before-pull")
        
        # Perform a normal pull (no custom merge strategy)
        self.exec("pull", "--no-edit")
        
        # Try to reapply stashed changes
        if has_local_changes:
            try:
                self.exec("stash", "apply")
                self.exec("stash", "drop")
            except Exception:
                # If stash apply produced conflicts, prefer local edits
                self.exec("checkout", "--ours", ".")
                self.exec("add", ".")
                # Do not drop the stash since it was not cleanly applied
        
        return self
    
    def exists(self, repo_url):
        try:
            self.exec("ls-remote", self.wrap_url_with_token(repo_url))
            return True
        except Exception:
            return False
    
    def push(self) -> 'GitWrapper':
        return self.exec("push", "-u", "origin", "main")
    
    def commit(self, commit_msg: str) -> 'GitWrapper':
        return self.exec("commit", "-m", commit_msg)
    
    def add_files(self) -> 'GitWrapper':
        return self.exec("add", ".")
    
    def add_commit_push(self, commit_msg: str) -> 'GitWrapper':
        self.add_files()
        self.commit(commit_msg)
        self.push()
        try:
            pass
        
        except subprocess.CalledProcessError:
            logging.info(f"No changes to commit.  {commit_msg} not committed")
        
        return self
    
    def cleanup(self):
        try:
            shutil.rmtree(self.source_root)
        except Exception as e:
            logging.exception(e)
    
    def create_repo(self, github_repo_url):
        repo_name = github_repo_url.split("/")[-1]
        response = requests.post(
            f"https://api.github.com/orgs/erieironllc/repos",
            headers={
                "Authorization": f"token {get_github_token()}",
                "Accept": "application/vnd.github+json"
            },
            json={
                "name": repo_name,
                "private": True,
                "auto_init": False
            }
        )
        
        if response.status_code == 201:
            print(f"✅ Created repo: {response.json()['html_url']}")
            return response.json()
        else:
            raise Exception(
                f"❌ Failed to create repo: {response.status_code} - {response.text}"
            )
    
    def wrap_url_with_token(self, repo_url: str) -> str:
        token = get_github_token()
        if not token:
            raise Exception("GITHUB_TOKEN not set in environment")
        return repo_url.replace("https://", f"https://{token}@")
    
    def source_exists(self) -> bool:
        return (self.source_root / ".git").exists()
    
    @property
    def venv_path(self) -> Path:
        return self.source_root / "venv"
    
    def run_python(self, python_cmd: list[str], env: dict = None) -> subprocess.CompletedProcess:
        if not self._venv_initialized:
            self.mk_venv()
        
        return run_cmd(
            self.source_root,
            [self.venv_path / "bin" / "python"] + common.ensure_list(python_cmd),
            env
        )
    
    def mk_venv(self) -> Path:
        venv_path = self.venv_path
        pip_executable = venv_path / "bin" / "pip" if os.name != "nt" else venv_path / "Scripts" / "pip.exe"
        
        if not pip_executable.exists():
            run_cmd(
                self.source_root,
                ["python3.11", "-m", "venv", "--without-pip", str(venv_path)]
            )
            
            # Bootstrap pip into the venv without triggering ensurepip --upgrade
            venv_python = venv_path / ("Scripts" / Path("python.exe") if os.name == "nt" else "bin/python")
            if os.name != "nt":
                # POSIX: use curl pipeline as requested
                run_cmd(
                    self.source_root,
                    [
                        "bash",
                        "-lc",
                        f"curl -sS https://bootstrap.pypa.io/get-pip.py | {venv_python}"
                    ]
                )
            else:
                # Windows: use PowerShell to download then execute with the venv's python
                run_cmd(
                    self.source_root,
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            "iwr -UseBasicParsing https://bootstrap.pypa.io/get-pip.py -OutFile get-pip.py; "
                            f"& '{venv_python}' get-pip.py"
                        ),
                    ],
                )
        
        run_cmd(
            self.source_root,
            [str(pip_executable), "install", "-r", "requirements.txt"]
        )
        
        self._venv_initialized = True
        return venv_path
    
    @staticmethod
    def get_latest_commit(repo_url: str | None = None) -> tuple[str, str]:
        remote_url = repo_url.strip() if repo_url else None
        if not remote_url:
            raise Exception("Git remote origin URL is not configured or provided")
        
        cleaned = remote_url.strip()
        if not cleaned:
            raise Exception("no remote url")
        
        github_token = get_github_token()
        if not github_token:
            raise Exception("unable to fetch github token from aws secrets")
        
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        
        path_fragment = None
        
        if cleaned.startswith("git@github.com:"):
            path_fragment = cleaned.split(":", 1)[1]
        elif "github.com/" in cleaned:
            path_fragment = cleaned.split("github.com/", 1)[1]
        elif "github.com:" in cleaned:
            path_fragment = cleaned.split("github.com:", 1)[1]
        
        if not path_fragment:
            raise Exception("no path fragment")
        
        path_fragment = path_fragment.lstrip("/")
        owner, repo = path_fragment.split("/", 2)
        
        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits",
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
            params={"per_page": 1},
            timeout=10
        )
        
        if response.status_code != 200:
            error_message = (
                f"GitHub API returned {response.status_code} for {owner}/{repo}: {response.text}"
            )
            raise Exception(error_message)
        
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise Exception(f"GitHub API response for {owner}/{repo} did not include commits")
        
        commit_obj = payload[0]
        commit_sha = commit_obj.get("sha")
        commit_message = ""
        
        commit_details = commit_obj.get("commit") or {}
        if isinstance(commit_details, dict):
            commit_message = commit_details.get("message") or ""
        
        if not commit_sha:
            raise Exception("GitHub API response missing commit SHA")
        
        return commit_sha, commit_message


def get_github_token():
    return aws_utils.get_secret("github-token").get("token")
