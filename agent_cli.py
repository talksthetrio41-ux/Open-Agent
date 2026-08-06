import argparse
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv(dotenv_path="/kaggle/working/workspace/.env", override=True)
sys.path.append("/kaggle/working/workspace")

from qwen_browser import QwenBrowserAutomator
from qwen_web_client import QwenWebClient
from harness import AgentHarness, SYSTEM_PROMPT

async def run_agent_loop(task: str, use_playwright: bool = True, headful: bool = False, max_steps: int = 15):
    print("=" * 70)
    print("   Qwen Autonomous Agentic Coding Harness")
    print(f"   Mode: {'PLAYWRIGHT BROWSER' if use_playwright else 'DIRECT HTTP API'}")
    print(f"   Task: {task}")
    print("=" * 70)

    harness = AgentHarness(sandbox_dir="./sandbox")
    automator = None
    client = None

    token = os.getenv("QWEN_TOKEN", "")
    cookie = os.getenv("QWEN_COOKIE", "")

    if use_playwright:
        print("\n[1/2] Starting Playwright Chromium browser driver...")
        automator = QwenBrowserAutomator(headless=not headful, token=token)
        await automator.start()
        print("[2/2] Browser initialized! Starting agentic loop...\n")
    else:
        client = QwenWebClient(token=token, cookies={"cookie_str": cookie} if cookie else None)
        print("[+] Direct API client ready! Starting agentic loop...\n")

    # Initial prompt wraps system prompt and task
    initial_prompt = f"{SYSTEM_PROMPT}\n\n### Task to solve:\n{task}"
    current_prompt = initial_prompt
    step = 0

    try:
        while step < max_steps:
            step += 1
            print(f"\n==================== STEP {step}/{max_steps} ====================")
            print("Agent Thinking & Streaming...\n")

            full_reply = ""
            try:
                if use_playwright:
                    async for chunk in automator.stream_chat_browser(prompt=current_prompt):
                        print(chunk, end="", flush=True)
                        full_reply += chunk
                else:
                    conversation = [{"role": "user", "content": current_prompt}]
                    async for chunk in client.stream_chat(messages=conversation, model="qwen-max"):
                        print(chunk, end="", flush=True)
                        full_reply += chunk
                print("\n")
            except Exception as e:
                print(f"\n[Streaming Error]: {e}")
                break

            if harness.is_done(full_reply):
                print("\n🎉 [Agent] Task completed successfully! (<DONE> detected)")
                break

            cmd = harness.extract_bash_command(full_reply)
            if cmd:
                print(f"\n⚙️ [Harness] Extracted Action:\n```bash\n{cmd}\n```")
                print("Running in sandbox...")
                output = harness.execute_command(cmd)
                print(f"\n📥 [Harness] Execution Output:\n{output}\n")
                
                # Feedback loop
                current_prompt = f"Command Output:\n```\n{output}\n```\nInspect the output and proceed to the next step. Remember to include <DONE> when finished."
            else:
                print("\n⚠️ [Harness] No ```bash``` block detected in response.")
                user_input = input("Enter feedback for agent (or press Enter to ask agent to continue): ").strip()
                if user_input:
                    current_prompt = user_input
                else:
                    current_prompt = "No command block was detected. Please provide your next bash action in a ```bash ... ``` block or output <DONE> if finished."
                    
    finally:
        if automator:
            await automator.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qwen Agentic Coding CLI")
    parser.add_argument("--task", type=str, help="Initial task description for the agent")
    parser.add_argument("--api-mode", action="store_true", help="Run via direct HTTP API instead of Playwright browser")
    parser.add_argument("--headful", action="store_true", help="Run browser in headful mode")
    parser.add_argument("--max-steps", type=int, default=15, help="Maximum number of loop iterations")
    args = parser.parse_args()

    task_input = args.task
    if not task_input:
        task_input = input("Enter the coding task for the agent: ").strip()
        
    if not task_input:
        print("Task cannot be empty. Exiting.")
        sys.exit(1)

    use_playwright = not args.api_mode
    asyncio.run(run_agent_loop(
        task=task_input,
        use_playwright=use_playwright,
        headful=args.headful,
        max_steps=args.max_steps
    ))
