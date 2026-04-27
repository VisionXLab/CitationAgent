"""
CitationClaw v2 — 论文被引画像分析🦞

Usage:
    citationclaw          # start web server at http://127.0.0.1:8000
    citationclaw --port 8080
    citationclaw --no-browser
"""

import argparse
import socket
import sys
import threading
import time
import webbrowser
import urllib.request
import urllib.error


# 2026-04-20: The banner print below contains the 🦞 emoji, which is
# not GBK-encodable. On Chinese Windows the default console is GBK
# (cp936), and printing the banner raises `UnicodeEncodeError` before
# uvicorn ever starts -- the server exits with a traceback and `--no-
# browser` users never see the modal at all.
#
# log_manager.py has a similar `_best_effort_utf8_console()` for the
# long-lived logger, but that only runs after log_manager is imported
# (which happens inside uvicorn.run, AFTER this module's banner print).
# Do the same best-effort reconfigure here at the earliest possible
# point so the banner + any early error messages survive.
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    _reconfigure = getattr(_stream, "reconfigure", None) if _stream else None
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _port_in_use(host: str, port: int) -> bool:
    """Return True if the port is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """Block until the server is accepting HTTP connections, or timeout."""
    deadline = time.monotonic() + timeout
    url = f"http://{host}:{port}/api/task/status"
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def main():
    parser = argparse.ArgumentParser(
        prog="citationclaw",
        description="CitationClaw v2 — 论文被引画像分析🦞",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as e:
        print("=" * 60)
        print("错误: 缺少依赖包!")
        print(f"详细信息: {e}")
        print("\n请先安装依赖:")
        print("  pip install citationclaw")
        print("=" * 60)
        sys.exit(1)

    # Check port availability before starting
    if _port_in_use(args.host, args.port):
        print(f"\n  错误: 端口 {args.port} 已被占用。")
        print(f"  请尝试使用其他端口:  citationclaw --port {args.port + 1}")
        print(f"  或先关闭占用该端口的程序。\n")
        sys.exit(1)

    print(f"\n  CitationClaw v2 🦞  →  http://{args.host}:{args.port}\n")

    if not args.no_browser:
        def _open_browser():
            if _wait_for_server(args.host, args.port, timeout=15.0):
                try:
                    webbrowser.open(f"http://{args.host}:{args.port}")
                except Exception:
                    pass
            else:
                print("  Warning: server did not become ready in time; skipping browser open.")
        threading.Thread(target=_open_browser, daemon=True).start()

    try:
        uvicorn.run(
            "citationclaw.app.main:app",
            host=args.host,
            port=args.port,
            log_level="warning",
        )
    except KeyboardInterrupt:
        print("\nCitationClaw v2 stopped.")


if __name__ == "__main__":
    main()
