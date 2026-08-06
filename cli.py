import argparse
import asyncio
import os
import sys
from dotenv import load_dotenv
from qwen_web_client import QwenWebClient
from qwen_browser import QwenBrowserAutomator

load_dotenv(dotenv_path="/kaggle/working/workspace/.env", override=True)

AVAILABLE_MODELS = [
    "qwen-max",
    "qwen-plus",
    "qwen-turbo",
    "qwen2.5-72b-instruct",
    "qwen2.5-coder-32b-instruct",
    "qvq-72b-preview"
]

async def interactive_cli(use_playwright: bool = True, headful: bool = False):
    mode_name = "PLAYWRIGHT BROWSER MODE" if use_playwright else "DIRECT HTTP API MODE"
    print("=" * 70)
    print(f"   Qwen Web Chat Streaming Interactive CLI\n   Mode: {mode_name}")
    print("=" * 70)

    automator = None
    client = None

    token = os.getenv("QWEN_TOKEN", "")
    cookie = os.getenv("QWEN_COOKIE", "")

    if use_playwright:
        print("\n[1/2] Starting Playwright Chromium browser driver...")
        automator = QwenBrowserAutomator(headless=not headful, token=token)
        await automator.start()
        print("[2/2] Playwright context initialized! Interactive chat is ready.")
        print("Type 'exit' or 'quit' to end.\n")
    else:
        if not token and not cookie:
            print("\n[Notice]: No QWEN_TOKEN or QWEN_COOKIE found in environment.")
            entered_token = input("Enter your Qwen session token (or press Enter for Playwright mode): ").strip()
            if entered_token:
                token = entered_token
            else:
                use_playwright = True
                print("\n[1/2] Starting Playwright Chromium browser driver...")
                automator = QwenBrowserAutomator(headless=not headful)
                await automator.start()
                print("[2/2] Playwright context initialized! Interactive chat is ready.")
                print("Type 'exit' or 'quit' to end.\n")

        if not use_playwright:
            client = QwenWebClient(token=token, cookies={"cookie_str": cookie} if cookie else None)
            print("[+] Direct SSE HTTP client ready! Type your prompt below:")
            print("Type '/new' or 'reset' for a new thread, 'exit' or 'quit' to end.\n")

    model = "qwen-max"
    conversation = []

    try:
        while True:
            try:
                prompt = input("\nYou > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting.")
                break

            if not prompt:
                continue
            if prompt.lower() in ("exit", "quit"):
                print("Goodbye!")
                break
            if prompt.lower() in ("new", "/new", "reset"):
                if use_playwright and automator:
                    await automator.reset_chat()
                conversation.clear()
                print("[+] Chat thread reset! Started a new conversation.")
                continue

            conversation.append({"role": "user", "content": prompt})

            print(f"\nQwen > ", end="", flush=True)

            full_reply = ""
            try:
                if use_playwright:
                    async for chunk in automator.stream_chat_browser(prompt=prompt):
                        print(chunk, end="", flush=True)
                        full_reply += chunk
                else:
                    async for chunk in client.stream_chat(messages=conversation, model=model):
                        print(chunk, end="", flush=True)
                        full_reply += chunk
                print() # newline
                if full_reply:
                    conversation.append({"role": "assistant", "content": full_reply})
            except Exception as e:
                print(f"\n[Streaming Error]: {e}")
    finally:
        if automator:
            await automator.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qwen Web Chat Streaming CLI")
    parser.add_argument("--api-mode", action="store_true", help="Run in raw HTTP API mode instead of Playwright browser mode")
    parser.add_argument("--headful", action="store_true", help="Run browser in headful mode (visible browser window)")
    args = parser.parse_args()

    use_playwright = not args.api_mode
    asyncio.run(interactive_cli(use_playwright=use_playwright, headful=args.headful))
