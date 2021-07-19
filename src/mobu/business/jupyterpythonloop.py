"""JupyterPythonLoop logic for mobu.

This business pattern will start jupyter and run some code
in a loop over and over again."""

import asyncio
from dataclasses import dataclass

from mobu.business.jupyterloginloop import JupyterLoginLoop

__all__ = ["JupyterPythonLoop"]

MAX_EXECUTIONS = 20
SLEEP_TIME = 1


@dataclass
class JupyterPythonLoop(JupyterLoginLoop):
    async def run(self) -> None:
        self.logger.info("Starting up...")

        self.start_event("hub_login")
        await self._client.hub_login()
        self.stop_current_event()
        self.start_event("ensure_lab")
        await self._client.ensure_lab()
        self.stop_current_event()
        while True:
            self.logger.info("create_kernel")
            self.start_event("create_kernel")
            kernel = await self._client.create_kernel()
            self.stop_current_event()

            for count in range(MAX_EXECUTIONS):
                self.start_event("execute_code")
                code_str = "print(2+2)"
                reply = await self._client.run_python(kernel, code_str)
                sw = self.get_current_event()
                if sw is not None:
                    sw.annotation = {"code": code_str, "result": reply}
                self.stop_current_event()
                self.logger.info(f"{code_str} -> {reply}")
                self.start_event("lab_wait")
                await asyncio.sleep(SLEEP_TIME)
                self.stop_current_event()
            self.logger.info("delete_kernel")
            self.start_event("delete_kernel")
            await self._client.delete_kernel(kernel)
            self.stop_current_event()
