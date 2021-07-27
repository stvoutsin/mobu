"""NotebookRunner logic for mobu.

This business pattern will clone a Git repo full of notebooks, iterate through
the notebooks, and run them on the remote Jupyter lab.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import git

from ..jupyterclient import JupyterLabSession, NotebookException
from ..models.business import BusinessData
from .jupyterloginloop import JupyterLoginLoop

if TYPE_CHECKING:
    from typing import Any, Dict, Iterator, List, Optional

    from structlog import BoundLogger

    from ..models.business import BusinessConfig
    from ..models.user import AuthenticatedUser

__all__ = ["NotebookRunner"]


class NotebookRunner(JupyterLoginLoop):
    """Start a Jupyter lab and run a sequence of notebooks."""

    def __init__(
        self,
        logger: BoundLogger,
        business_config: BusinessConfig,
        user: AuthenticatedUser,
    ) -> None:
        super().__init__(logger, business_config, user)
        self.notebook: Optional[os.DirEntry] = None
        self.running_code: Optional[str] = None
        self._failed_notebooks: List[str] = []
        self._repo_dir = TemporaryDirectory()
        self._repo: Optional[git.Repo] = None
        self._notebook_iterator: Optional[Iterator[os.DirEntry]] = None

    async def run(self) -> None:
        self.logger.info("Starting up...")
        await self.startup()
        while True:
            self.logger.info("Starting next iteration")
            try:
                await self.lab_business()
                self.success_count += 1
            except NotebookException as e:
                running_code = self.running_code
                notebook_name = "no notebook"
                if self.notebook:
                    self._failed_notebooks.append(self.notebook.name)
                    notebook_name = self.notebook.name
                self.logger.error(f"Error running notebook: {notebook_name}")
                self.running_code = None
                self.failure_count += 1
                raise NotebookException(
                    f"Running {notebook_name}: '"
                    f"```{running_code}``` generated: ```{e}```"
                )
            except Exception:
                self.failure_count += 1
                raise

    async def startup(self) -> None:
        if not self._repo:
            self.clone_repo()
        self._notebook_iterator = os.scandir(self._repo_dir.name)
        self.logger.info("Repository cloned and ready")
        await super().startup()
        await self.initial_delete_lab()

    def clone_repo(self) -> None:
        url = self.config.repo_url
        branch = self.config.repo_branch
        path = self._repo_dir.name
        with self.timings.start("clone_repo"):
            self._repo = git.Repo.clone_from(url, path, branch=branch)

    async def initial_delete_lab(self) -> None:
        with self.timings.start("initial_delete_lab"):
            await self._client.delete_lab()

    async def lab_business(self) -> None:
        self._next_notebook()
        assert self.notebook

        await self.ensure_lab()
        await self.lab_settle()
        session = await self.create_session()

        self.logger.info(f"Starting notebook: {self.notebook.name}")
        cells = self.read_notebook(self.notebook.name, self.notebook.path)
        for count in range(self.config.notebook_iterations):
            iteration = f"{count + 1}/{self.config.notebook_iterations}"
            msg = f"Notebook '{self.notebook.name}' iteration {iteration}"
            self.logger.info(msg)
            await self.reauth_if_needed()

            for cell in cells:
                self.running_code = "".join(cell["source"])
                await self.execute_code(session, self.running_code)
                await self.execution_idle()

        self.running_code = None
        await self.delete_session(session)
        self.logger.info(f"Success running notebook: {self.notebook.name}")

    async def lab_settle(self) -> None:
        with self.timings.start("lab_settle"):
            await asyncio.sleep(self.config.settle_time)

    def read_notebook(self, name: str, path: str) -> List[Dict[str, Any]]:
        with self.timings.start(f"read_notebook:{name}"):
            notebook_text = Path(path).read_text()
            cells = json.loads(notebook_text)["cells"]
        return [c for c in cells if c["cell_type"] == "code"]

    async def create_session(self) -> JupyterLabSession:
        self.logger.info("create_session")
        notebook_name = self.notebook.name if self.notebook else None
        with self.timings.start("create_session"):
            session = await self._client.create_labsession(
                notebook_name=notebook_name,
            )
        return session

    async def execute_code(
        self, session: JupyterLabSession, code: str
    ) -> None:
        self.logger.info("Executing:\n%s\n", code)
        with self.timings.start("run_code", {"code": code}) as sw:
            reply = await self._client.run_python(session, code)
            sw.annotation["result"] = reply
        self.logger.info(f"Result:\n{reply}\n")

    async def delete_session(self, session: JupyterLabSession) -> None:
        self.logger.info(f"Deleting session {session}")
        with self.timings.start("delete_session"):
            await self._client.delete_labsession(session)

    def dump(self) -> BusinessData:
        data = super().dump()
        data.running_code = self.running_code
        data.notebook = self.notebook.name if self.notebook else None
        return data

    def _next_notebook(self) -> None:
        assert self._notebook_iterator
        try:
            self.notebook = next(self._notebook_iterator)
            while not self.notebook.path.endswith(".ipynb"):
                self.notebook = next(self._notebook_iterator)
        except StopIteration:
            self.logger.info(
                "Done with this cycle of notebooks, recreating lab."
            )
            self._notebook_iterator = os.scandir(self._repo_dir.name)
            self._next_notebook()
