import os
import re
import subprocess
import logging
from typing import Optional, Tuple

logger = logging.getLogger("AgentHarness")

SYSTEM_PROMPT = """You are an autonomous AI coding and execution agent with FULL shell and terminal access. You solve user tasks by executing bash commands, managing files, cloning repositories, installing packages, downloading datasets/models, and running scripts.

### Available Capabilities & Tools:
1. **Repository & Code Management**: `git clone`, `git checkout`, creating/editing files, directory navigation.
2. **Package & Environment Management**: `pip install`, `npm install`, `apt-get`, environment inspection.
3. **Data & Model Downloads**: `curl`, `wget`, `kaggle` CLI, `huggingface-cli`, `unzip`, `tar`.
4. **Environment Tokens**: All environment tokens (e.g. `GITHUB_TOKEN`, `KAGGLE_USERNAME`, `KAGGLE_KEY`, `HF_TOKEN`) from the environment/.env are passed through directly to your commands.
5. **Full Shell Capabilities**: You can execute any valid bash/shell command.

### Rules & Instructions:
1. Break down the task into clear, step-by-step actions.
2. Briefly explain your thought process before executing a step.
3. Put the bash command(s) for each step inside a single ```bash ... ``` code block.
4. **CRITICAL**: Only output ONE ```bash ... ``` code block per response turn.
5. After outputting a ```bash``` block, stop and wait for the harness to execute it and return the command output.
6. When the entire task is successfully completed and verified, include the text `<DONE>` in your final message.

### Example Interaction:
Thought: I will clone the repository and install its dependencies.
```bash
git clone https://github.com/example/repo.git
cd repo && pip install -r requirements.txt
```
"""

class AgentHarness:
    def __init__(self, sandbox_dir: str = "./sandbox"):
        self.sandbox_dir = os.path.abspath(sandbox_dir)
        self.ensure_sandbox()

    def ensure_sandbox(self):
        """Creates the sandbox directory if it does not exist."""
        if not os.path.exists(self.sandbox_dir):
            os.makedirs(self.sandbox_dir, exist_ok=True)

    def extract_bash_command(self, text: str) -> Optional[str]:
        """Extracts bash code block from text, supporting standard markdown and Qwen UI DOM renderings."""
        if not text:
            return None
            
        # Normalize non-breaking spaces (\xa0) to standard spaces
        text = text.replace("\xa0", " ")
        
        # 1. Standard markdown ```bash ... ``` block
        match = re.search(r"```(?:bash|sh)?\n(.*?)```", text, re.DOTALL)
        if match:
            raw_code = match.group(1).strip()
            lines = [l for l in raw_code.split("\n") if not l.strip().isdigit() and l.strip() not in ("bash", "sh")]
            return "\n".join(lines).strip()
            
        # 2. Qwen UI DOM rendered block: "bash\n1\n2\n3...\n<code_lines>"
        match_ui = re.search(r"(?:^|\n)(?:bash|sh)\n((?:\d+\n)+)(.*)", text, re.DOTALL)
        if match_ui:
            raw_code = match_ui.group(2).strip()
            lines = []
            for line in raw_code.split("\n"):
                stripped = line.strip()
                if stripped.isdigit() or stripped in ("bash", "sh"):
                    continue
                lines.append(line)
            return "\n".join(lines).strip()
            
        return None

    def is_done(self, text: str) -> bool:
        """Checks if the response indicates completion with <DONE> tag."""
        return "<DONE>" in text

    def execute_command(self, command: str, timeout: int = 60) -> str:
        """Executes a bash command in the sandbox directory and returns stdout/stderr."""
        self.ensure_sandbox()
        logger.info(f"Executing command in sandbox ({self.sandbox_dir}):\n{command}")
        
        try:
            # Pass full os.environ so process has access to KAGGLE_KEY, HF_TOKEN, GITHUB_TOKEN, etc.
            env = os.environ.copy()
            process = subprocess.run(
                command,
                shell=True,
                cwd=self.sandbox_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            output = ""
            if process.stdout:
                output += process.stdout
            if process.stderr:
                output += "\n[stderr]\n" + process.stderr
                
            if process.returncode != 0:
                output += f"\n[Exit Code: {process.returncode}]"
                
            if not output.strip():
                output = "(Command executed successfully with no output)"
                
            # Truncate output if excessively long to prevent token overflow
            if len(output) > 8000:
                output = output[:4000] + "\n\n... [Output Truncated] ...\n\n" + output[-4000:]
                
            return output
            
        except subprocess.TimeoutExpired:
            return f"[Execution Error: Command timed out after {timeout} seconds]"
        except Exception as e:
            return f"[Execution Error: {str(e)}]"
