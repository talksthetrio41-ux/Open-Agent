import os
import re
import subprocess
import logging
from typing import Optional, Tuple

logger = logging.getLogger("AgentHarness")

SYSTEM_PROMPT = """You are an autonomous AI coding agent. You solve user coding tasks by executing bash commands and editing files inside a sandbox environment.

### Rules & Instructions:
1. Break down the task into step-by-step actions.
2. Briefly explain your thought process before executing a step.
3. If you need to execute bash commands (creating files, running code, running tests, checking directory contents), put the command in a single ```bash ... ``` block.
4. **IMPORTANT**: Only output ONE ```bash ... ``` code block per response turn.
5. After outputting a ```bash``` block, stop and wait for the harness to execute it and return the command output.
6. When the entire task is successfully completed and verified, include the text `<DONE>` in your final message.

### Example Interaction:
Thought: I need to write a Python script that calculates factorials and test it.
```bash
cat << 'EOF' > test_factorial.py
def factorial(n):
    return 1 if n <= 1 else n * factorial(n - 1)

print(factorial(5))
EOF
python3 test_factorial.py
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
            return match.group(1).strip()
            
        # 2. Qwen UI DOM rendered block: "bash\n1\n2\n3...\n<code_lines>"
        match_ui = re.search(r"(?:^|\n)(?:bash|sh)\n((?:\d+\n)+)(.*)", text, re.DOTALL)
        if match_ui:
            return match_ui.group(2).strip()
            
        return None

    def is_done(self, text: str) -> bool:
        """Checks if the response indicates completion with <DONE> tag."""
        return "<DONE>" in text

    def execute_command(self, command: str, timeout: int = 60) -> str:
        """Executes a bash command in the sandbox directory and returns stdout/stderr."""
        self.ensure_sandbox()
        logger.info(f"Executing command in sandbox ({self.sandbox_dir}):\n{command}")
        
        try:
            process = subprocess.run(
                command,
                shell=True,
                cwd=self.sandbox_dir,
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
