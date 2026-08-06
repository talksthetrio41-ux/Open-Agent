import asyncio
import json
import os
import sys
import time
import httpx
from httpx import ASGITransport, AsyncClient
from dotenv import load_dotenv

load_dotenv()

from qwen_web_client import QwenWebClient
from qwen_browser import QwenBrowserAutomator

RESULTS_FILE = "/kaggle/working/workspace/test_results.md"
ARTIFACT_RESULTS_FILE = "/root/.gemini/antigravity-ide/brain/cb7bed6c-aba3-4c94-94d3-944e1557afd9/test_results.md"

async def test_direct_sse_client():
    print("\n--- Running Test 1: Direct SSE HTTP Client (qwen_web_client.py) ---")
    start_time = time.time()
    client = QwenWebClient()
    messages = [{"role": "user", "content": "Write a 2-sentence explanation of quantum computing."}]

    chunks = []
    try:
        async for chunk in client.stream_chat(messages=messages, model="qwen3.8-max"):
            chunks.append(chunk)
            print(chunk, end="", flush=True)
        print()
        elapsed = round(time.time() - start_time, 2)
        full_text = "".join(chunks)
        return {
            "test_name": "Direct SSE HTTP Client (qwen_web_client.py)",
            "status": "PASSED" if full_text and "[HTTP" not in full_text else "FAILED",
            "model": "qwen3.8-max",
            "elapsed_sec": elapsed,
            "response_length": len(full_text),
            "response_text": full_text
        }
    except Exception as e:
        return {
            "test_name": "Direct SSE HTTP Client (qwen_web_client.py)",
            "status": "ERROR",
            "error": str(e)
        }


async def test_playwright_automator():
    print("\n--- Running Test 2: Playwright Browser Driver (qwen_browser.py) ---")
    start_time = time.time()
    automator = QwenBrowserAutomator(headless=True)
    chunks = []
    try:
        await automator.start()
        prompt = "List 3 primary benefits of using Python for AI development."
        async for chunk in automator.stream_chat_browser(prompt=prompt, timeout_sec=30):
            chunks.append(chunk)
            print(chunk, end="", flush=True)
        print()
        await automator.close()
        elapsed = round(time.time() - start_time, 2)
        full_text = "".join(chunks)
        return {
            "test_name": "Playwright Browser Driver (qwen_browser.py)",
            "status": "PASSED" if full_text and "[Error" not in full_text else "FAILED",
            "model": "Browser Default (Qwen)",
            "elapsed_sec": elapsed,
            "response_length": len(full_text),
            "response_text": full_text
        }
    except Exception as e:
        await automator.close()
        return {
            "test_name": "Playwright Browser Driver (qwen_browser.py)",
            "status": "ERROR",
            "error": str(e)
        }


async def test_fastapi_server_endpoint():
    print("\n--- Running Test 3: FastAPI Gateway Server Stream (/chat/stream) ---")
    from server import app
    start_time = time.time()
    chunks = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        req_payload = {
            "prompt": "What is 100 divided by 4? Reply with just the number.",
            "model": "qwen3.8-max"
        }
        headers = {"Authorization": f"Bearer {os.getenv('QWEN_TOKEN')}"}

        async with ac.stream("POST", "/chat/stream", json=req_payload, headers=headers) as response:
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
                full_text = "".join(chunks)
                return {
                    "test_name": "FastAPI Gateway Stream (/chat/stream)",
                    "status": "PASSED" if full_text and "[HTTP" not in full_text else "FAILED",
                    "model": "qwen3.8-max",
                    "elapsed_sec": elapsed,
                    "response_length": len(full_text),
                    "response_text": full_text
                }
            else:
                return {
                    "test_name": "FastAPI Gateway Stream (/chat/stream)",
                    "status": "FAILED",
                    "error": f"HTTP {response.status_code}"
                }


async def main():
    print("=" * 70)
    print("      Executing Comprehensive Qwen Streaming Test Suite")
    print("=" * 70)

    token = os.getenv("QWEN_TOKEN")
    print(f"Active Session Token: {token[:25]}..." if token else "NO TOKEN DETECTED!")

    res1 = await test_direct_sse_client()
    res2 = await test_playwright_automator()
    res3 = await test_fastapi_server_endpoint()

    results = [res1, res2, res3]

    # Generate Markdown Report
    report = []
    report.append("# Qwen Web Chat Streaming - Verification & Test Results\n")
    report.append(f"**Test Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    report.append(f"**Session Token Configured**: `{token[:20]}...{token[-15:] if token else ''}`\n")

    report.append("## Executive Summary\n")
    report.append("| Test Case | Method | Status | Time (s) | Response Length |")
    report.append("| :--- | :--- | :--- | :--- | :--- |")

    for r in results:
        status_icon = "✅ PASSED" if r["status"] == "PASSED" else "❌ " + r["status"]
        elapsed = r.get("elapsed_sec", "N/A")
        length = r.get("response_length", 0)
        report.append(f"| {r['test_name']} | Stream | {status_icon} | {elapsed} | {length} chars |")

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

    print(f"\n[+] Full test report saved to {RESULTS_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
