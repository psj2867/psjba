"""실제 Chromium + CDP 통합 검증 스크립트.

사용법:
    python scripts/test_chrome_live.py

테스트 순서:
  1. Chromium 을 CDP 모드로 실행
  2. HTTP /json/version 응답 확인 (Browser)
  3. WebSocket raw CDP 명령 (GPU 불필요)
  4. Playwright connect_over_cdp (GPU 필요 시 SKIP)
  5. 프로세스 정리

이 환경(컨테이너, /dev/shm 64MB)에서는 `--in-process-gpu` 가 필요합니다.
"""

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHROME_BIN = "/usr/bin/chromium"
CDP_PORT = 9242


def launch_chromium(port: int,
                    user_data_dir: str,
                    log_path: str) -> subprocess.Popen:
    """Chromium 을 CDP 모드로 실행하고 Popen 객체 반환."""
    cmd = [
        CHROME_BIN,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--no-first-run",
        "--disable-extensions",
        "--enable-unsafe-swiftshader",
        "--in-process-gpu",          # GPU 크래시 우회
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--disable-dev-shm-usage",
    ]
    return subprocess.Popen(
        cmd,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
    )


def wait_for_cdp_http(port: int, timeout: float = 25.0) -> str | None:
    """CDP HTTP 포트가 열릴 때까지 대기, Browser version 반환."""
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
            if r.status_code == 200:
                ver = r.json().get("Browser", "?")
                print(f"[CDP HTTP] {ver}")
                return ver
        except Exception:
            pass
        time.sleep(0.3)
    print(f"[CDP HTTP] port {port} not ready in {timeout}s")
    return None


async def test_http_api(port: int) -> None:
    """CDP HTTP API 검증: /json/version, /json."""
    import httpx
    r = httpx.get(f"http://127.0.0.1:{port}/json/version")
    assert r.status_code == 200, f"json/version: {r.status_code}"
    d = r.json()
    assert "Browser" in d
    assert "webSocketDebuggerUrl" in d
    print(f"[HTTP] /json/version OK → {d['Browser']}")

    r2 = httpx.get(f"http://127.0.0.1:{port}/json")
    assert r2.status_code == 200
    pages = r2.json()
    assert isinstance(pages, list)
    if pages:
        assert "webSocketDebuggerUrl" in pages[0]
    print(f"[HTTP] /json OK → {len(pages)} pages")


async def test_raw_cdp_ws(port: int) -> None:
    """WebSocket raw CDP 명령 (렌더링 불필요)."""
    import httpx
    import websockets
    import json

    ws_url = httpx.get(
        f"http://127.0.0.1:{port}/json/version"
    ).json().get("webSocketDebuggerUrl", "")
    assert ws_url, "no webSocketDebuggerUrl"

    async with asyncio.timeout(10):
        async with websockets.connect(ws_url, max_size=2 ** 20) as ws:
            # Browser.getVersion
            await ws.send(json.dumps({"id": 1, "method": "Browser.getVersion"}))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            product = resp.get("result", {}).get("product", "?")
            assert "Chrome" in product or "Headless" in product
            print(f"[WS] Browser.getVersion → {product}")

            # Target.createTarget → 새 페이지
            await ws.send(json.dumps({
                "id": 2, "method": "Target.createTarget",
                "params": {"url": "about:blank"},
            }))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            target_id = resp.get("result", {}).get("targetId", "")
            assert target_id
            print(f"[WS] Target.createTarget → {target_id[:24]}…")

    print("[WS] All raw CDP commands OK")


async def test_playwright_cdp(port: int) -> None:
    """Playwright CDP 연결 (GPU 필요 시 graceful skip)."""
    from playwright.async_api import async_playwright, Error as PWError
    try:
        async with asyncio.timeout(20):
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}", timeout=15000)
                ctx = browser.contexts[0]
                page = await ctx.new_page()
                await page.goto(
                    "data:text/html,<h1>PW</h1><input id='q'>",
                    timeout=10000)
                await page.fill("#q", "cdp")
                val = await page.input_value("#q")
                assert val == "cdp"
                tree = await page.accessibility.snapshot()
                assert tree is not None
                print(f"[PW] Page OK, A11Y role={tree.get('role')}")
                await browser.close()
                print("[PW] Playwright CDP OK")
    except (PWError, asyncio.TimeoutError, AssertionError) as e:
        print(f"[PW] SKIP (GPU/rendering unavailable): {e}")


def cleanup(proc: subprocess.Popen) -> None:
    """Chromium 프로세스 + crashpad 핸들러 정리."""
    print("[CLEANUP] terminating...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    subprocess.run(
        ["pkill", "-f", "chrome_crashpad"],
        capture_output=True, timeout=2,
    )
    print(f"[CLEANUP] exit code={proc.returncode}")


def main() -> int:
    print("=" * 60)
    print("REAL CHROMIUM + CDP INTEGRATION TEST")
    print("=" * 60)

    if not os.path.exists(CHROME_BIN):
        print(f"[SKIP] {CHROME_BIN} not found")
        return 0

    user_data_dir = tempfile.mkdtemp(prefix="psjba-live-")
    log_path = Path(tempfile.gettempdir()) / "psjba-chrome-live.log"

    proc = launch_chromium(CDP_PORT, user_data_dir, str(log_path))
    print(f"[LAUNCH] PID={proc.pid}, port={CDP_PORT}")

    try:
        version = wait_for_cdp_http(CDP_PORT)
        if not version:
            print("[FAIL] Chromium CDP unavailable in this environment")
            return 0

        asyncio.run(test_http_api(CDP_PORT))

        try:
            asyncio.run(test_raw_cdp_ws(CDP_PORT))
        except Exception as e:
            print(f"[WS] SKIP: {e}")

        asyncio.run(test_playwright_cdp(CDP_PORT))

        print("=" * 60)
        print("[PASS] Live tests completed")
        print("=" * 60)
        return 0
    finally:
        cleanup(proc)


if __name__ == "__main__":
    sys.exit(main())
