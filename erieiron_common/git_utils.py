import glob
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from erieiron_common import common
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

    def exec(self, *commands) -> 'GitWrapper':
        cmd = ["git"] + list(commands)
        
        try:
            env = os.environ.copy()
            if os.getenv("GITHUB_TOKEN"):
                env["GITHUB_TOKEN"] = os.getenv("GITHUB_TOKEN")
            
            result = subprocess.run(
                cmd,
                env=env,
                cwd=self.source_root,
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            logging.error(f"stdout:\n{e.stdout.strip()}")
            logging.error(f"stderr:\n{e.stderr.strip()}")
            raise Exception(
                f"Command failed: {common.safe_join(cmd)}\n"
                f"stdout:\n{e.stdout.strip()}\n"
                f"stderr:\n{e.stderr.strip()}"
            )
        else:
            logging.info(result.stdout)
            logging.info(result.stderr)
        
        return self
    
    def clone_to_new_repo(self, source_repo, target_repo) -> 'GitWrapper':
        self.clone(source_repo)
        self.exec("remote", "remove", "origin")
        self.exec("remote", "add", "origin", self.wrap_url_with_token(target_repo))
        return self
    
    def clone(self, source_repo) -> 'GitWrapper':
        return self.exec("clone", self.wrap_url_with_token(source_repo), self.source_root)
    
    def pull(self) -> 'GitWrapper':
        return self.exec("pull")
    
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
                "Authorization": f"token {os.getenv('GITHUB_TOKEN')}",
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
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise Exception("GITHUB_TOKEN not set in environment")
        return repo_url.replace("https://", f"https://{token}@")
    
    def source_exists(self) -> bool:
        return len(glob.glob(os.path.join(self.source_root, f"*.py"))) > 0
    
    def mk_venv(self) -> Path:
        venv_path = self.source_root / "venv"
        time.sleep(1)
        
        run_cmd(
            self.source_root,
            ["python3.11", "-m", "venv", str(venv_path)]
        )
        
        pip_executable = venv_path / "bin" / "pip" if os.name != "nt" else venv_path / "Scripts" / "pip.exe"
        cmd = [str(pip_executable), "install", "-r", "requirements.txt"]
        
        run_cmd(
            self.source_root,
            [str(pip_executable), "install", "-r", "requirements.txt"]
        )
        
        return venv_path
    
   
