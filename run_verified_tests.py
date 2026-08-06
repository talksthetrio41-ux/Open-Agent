import asyncio
import json
import os
import sys
import time
import httpx
from httpx import ASGITransport, AsyncClient
from dotenv import load_dotenv

load_dotenv(dotenv_path="/kaggle/working/workspace/.env", override=True)
sys.path.append("/kaggle/working/workspace")

from qwen_browser import QwenBrowserAutomator

RESULTS_FILE = "/kaggle/working/workspace/test_results.md"
ARTIFACT_RESULTS_FILE = "/root/.gemini/antigravity-ide/brain/cb7bed6c-aba3-4c94-94d3-944e1557afd9/test_results.md"

async def test_playwright_direct():
    print("\n--- Running Test 1: Playwright Browser Streaming Engine ---")
    start_time = time.time()
    automator = QwenBrowserAutomator(headless=True)
    chunks = []
    try:
        await automator.start()
        # Navigate explicitly & wait network idle
        await automator._page.goto("https://chat.qwen.ai", wait_until="networkidle")
        await asyncio.sleep(2)

        prompt = "What is the capital of France? Reply in 1 short sentence."
        print(f"Prompt: '{prompt}'")
        print("Streaming output: ", end="", flush=True)

        async for chunk in automator.stream_chat_browser(prompt=prompt, timeout_sec=40):
            chunks.append(chunk)
            print(chunk, end="", flush=True)
        print()
        await automator.close()
        elapsed = round(time.time() - start_time, 2)
        full_text = "".join(chunks).strip()
        return {
            "test_name": "Playwright Browser Streaming Engine (qwen_browser.py)",
            "status": "PASSED" if full_text and "[Error" not in full_text else "FAILED",
            "model": "Flagship Qwen (Browser UI)",
            "elapsed_sec": elapsed,
            "response_length": len(full_text),
            "response_text": full_text
        }
    except Exception as e:
        await automator.close()
        return {
            "test_name": "Playwright Browser Streaming Engine (qwen_browser.py)",
            "status": "ERROR",
            "error": str(e)
        }

async def test_fastapi_playwright_endpoint():
    print("\n--- Running Test 2: FastAPI Playwright Stream Endpoint (/chat/playwright-stream) ---")
    from server import app
    start_time = time.time()
    chunks = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        req_payload = {
            "prompt": "What is 15 plus 35? Reply with just the number.",
            "model": "qwen-max"
        }

        async with ac.stream("POST", "/chat/playwright-stream", json=req_payload) as response:
            if response.status_code == 200:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            if "text" in data_json:
                                chunks.append(data_json["text"])
                                print(data_json["text"], end="", flush=True)
                        except Exception:
                            pass
                print()
                elapsed = round(time.time() - start_time, 2)
                full_text = "".join(chunks).strip()
                return {
                    "test_name": "FastAPI Playwright Gateway (/chat/playwright-stream)",
                    "status": "PASSED" if full_text else "FAILED",
                    "model": "qwen-max (Playwright Gateway)",
                    "elapsed_sec": elapsed,
                    "response_length": len(full_text),
                    "response_text": full_text
                }
            else:
                return {
                    "test_name": "FastAPI Playwright Gateway (/chat/playwright-stream)",
                    "status": "FAILED",
                    "error": f"HTTP {response.status_code}"
                }

async def main():
    print("=" * 75)
    print("      Executing Comprehensive Qwen Playwright Streaming Test Suite")
    print("=" * 75)

    token = os.getenv("QWEN_TOKEN", "").strip()
    print(f"Active User Session Token: {token[:30]}..." if token else "NO TOKEN DETECTED!")

    res1 = await test_playwright_direct()
    res2 = await test_fastapi_playwright_endpoint()

    results = [res1, res2]

    # Generate Markdown Report
    report = []
    report.append("# Qwen Web Chat Streaming - Playwright Verification & Test Results\n")
    report.append(f"**Test Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    report.append(f"**Authenticated Session Token**: `{token[:25]}...{token[-20:] if token else ''}`\n")

    report.append("## Executive Summary\n")
    report.append("| Test Case | Execution Mode | Status | Latency (s) | Response Length |")
    report.append("| :--- | :--- | :--- | :--- | :--- |")

    for r in results:
        status_icon = "✅ PASSED" if r["status"] == "PASSED" else "❌ " + r["status"]
        elapsed = r.get("elapsed_sec", "N/A")
        length = r.get("response_length", 0)
        report.append(f"| {r['test_name']} | Playwright | {status_icon} | {elapsed}s | {length} chars |")

    report.append("\n---\n")
    report.append("## Detailed Streamed Output Results\n")

    for idx, r in enumerate(results, 1):
        report.append(f"### Test {idx}: {r['test_name']}\n")
        report.append(f"- **Status**: `{r['status']}`")
        if "elapsed_sec" in r:
            report.append(f"- **Latency**: `{r['elapsed_sec']} seconds`")
        if "model" in r:
            report.append(f"- **Model**: `{r['model']}`")

        if "response_text" in r:
            report.append(f"\n**Streamed Response Output**:\n```text\n{r['response_text']}\n```\n")
        if "error" in r:
            report.append(f"\n**Error Log**:\n```text\n{r['error']}\n```\n")

    report_str = "\n".join(report)

    with open(RESULTS_FILE, "w") as f:
        f.write(report_str)
    with open(ARTIFACT_RESULTS_FILE, "w") as f:
        f.write(report_str)

    print(f"\n[+] Full test report saved to {RESULTS_FILE} and {ARTIFACT_RESULTS_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
