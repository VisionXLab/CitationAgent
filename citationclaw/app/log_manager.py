import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set
from collections import deque
from fastapi import WebSocket


# Windows 默认控制台是 cp936 (GBK)，遇到 ⚠ 🔥 etc 会 UnicodeEncodeError 崩溃。
# 启动时尽量把 stdout/stderr 切到 UTF-8 errors=replace，这样 print 永远不崩。
# 若流不支持 reconfigure（比如已被重定向且不是 TextIOWrapper），静默跳过，
# 由 _log() 里的 try/except UnicodeEncodeError 作最后兜底。
def _best_effort_utf8_console():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_best_effort_utf8_console()


class LogManager:
    # 2026-04-21: ordered log-level numeric values for threshold filtering.
    # SUCCESS sits between INFO and WARNING so users who set min_level=
    # SUCCESS get "only green (PDF OK) + yellow (warnings) + red (errors),
    # no INFO noise". User request: make UI feel calm without constantly
    # seeing cascade-internal 'HTTP 403 / Connection error' at INFO level.
    _LEVEL_ORDER = {
        "DEBUG":   0,
        "INFO":    10,
        "SUCCESS": 20,
        "WARNING": 30,
        "ERROR":   40,
    }

    def __init__(self, max_logs: int = 1000):
        """
        日志管理器,负责日志记录和WebSocket广播

        Args:
            max_logs: 最大保留日志条数
        """
        self.logs = deque(maxlen=max_logs)
        self.websocket_connections: Set[WebSocket] = set()
        self.current_progress = {"current": 0, "total": 100, "percentage": 0}
        self._log_file: Optional[Path] = None
        self._log_fh = None  # file handle
        # 2026-04-21: minimum log level that will be persisted /
        # broadcast. `set_min_level()` accepts a string name; until
        # set it admits everything (INFO / SUCCESS / WARNING / ERROR).
        self._min_level_num: int = self._LEVEL_ORDER["INFO"]

    def set_min_level(self, level: str) -> None:
        """Set the threshold below which messages are silently dropped.

        Args:
            level: "DEBUG" / "INFO" / "SUCCESS" / "WARNING" / "ERROR".
                   Case-insensitive. Unknown values default to INFO.
        """
        self._min_level_num = self._LEVEL_ORDER.get(
            (level or "").upper().strip(),
            self._LEVEL_ORDER["INFO"],
        )

    # ── File logging ──────────────────────────────────────────────────

    def set_log_file(self, path: Path):
        """Start logging to a file. Call this when a new task starts.

        The log file is written in append mode with UTF-8 encoding.
        Each line: [TIMESTAMP] [LEVEL] message
        """
        self.close_log_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = path
            self._log_fh = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
            self._log_fh.write(f"\n{'='*70}\n")
            self._log_fh.write(f"CitationClaw log started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._log_fh.write(f"{'='*70}\n\n")
        except Exception as e:
            print(f"Warning: could not open log file {path}: {e}")
            self._log_fh = None

    def close_log_file(self):
        """Flush and close the current log file."""
        if self._log_fh:
            try:
                self._log_fh.flush()
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None
            self._log_file = None

    def _write_to_file(self, level: str, message: str):
        """Append one log line to the file (if open)."""
        if self._log_fh:
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                self._log_fh.write(f"[{ts}] [{level}] {message}\n")
            except Exception:
                pass  # Never let file I/O crash the pipeline

    # ── WebSocket management ──────────────────────────────────────────

    def add_websocket(self, websocket: WebSocket):
        """添加WebSocket连接"""
        self.websocket_connections.add(websocket)

    def remove_websocket(self, websocket: WebSocket):
        """移除WebSocket连接"""
        self.websocket_connections.discard(websocket)

    async def _broadcast(self, message: dict):
        """
        广播消息到所有连接的WebSocket

        Args:
            message: 要广播的消息
        """
        disconnected = set()
        for ws in self.websocket_connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"WebSocket发送失败: {e}")
                disconnected.add(ws)

        # 清理断开的连接
        self.websocket_connections -= disconnected

    def _schedule_broadcast(self, message: dict):
        """
        Schedule an async broadcast. Falls back to just appending to the deque
        when no event loop is running (e.g. during startup or from a sync context).
        """
        try:
            asyncio.create_task(self._broadcast(message))
        except RuntimeError:
            # No running event loop — silently skip broadcast.
            # The log entry is already in self.logs so it won't be lost.
            pass

    def _log(self, level: str, message: str):
        """
        记录日志

        Args:
            level: 日志级别(INFO, SUCCESS, WARNING, ERROR)
            message: 日志消息
        """
        # 2026-04-21: level-based filtering. Below threshold -> drop
        # entirely (no console, no file, no WebSocket, no history).
        # Rationale: cascade-internal INFO lines ("HTTP 403", "Connection
        # error", "Cloudflare 验证 — 请在浏览器...") make users feel the
        # pipeline is broken. Setting min_level=SUCCESS in config.json
        # collapses the UI to just green [PDF OK] + red [PDF失败] lines.
        level_num = self._LEVEL_ORDER.get(level.upper(), self._LEVEL_ORDER["INFO"])
        if level_num < self._min_level_num:
            return

        log_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": message
        }
        self.logs.append(log_entry)

        # 打印到控制台（Windows GBK 终端 + 无法 reconfigure 的流需要兜底）
        line = f"[{log_entry['timestamp']}] [{level}] {message}"
        try:
            print(line)
        except UnicodeEncodeError:
            # 最后防线：拿当前流 encoding（通常是 cp936）重新编码为 ASCII-safe
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(line.encode(enc, errors="replace").decode(enc, errors="replace"))
        except Exception:
            # 任何其他 IO 异常都不应影响流水线（比如 stdout 被关闭）
            pass

        # 写入日志文件（如果已开启）
        self._write_to_file(level, message)

        # 异步广播(不阻塞)
        self._schedule_broadcast({
            "type": "log",
            "data": log_entry
        })

    def info(self, message: str):
        """记录INFO级别日志"""
        self._log("INFO", message)

    def success(self, message: str):
        """记录SUCCESS级别日志"""
        self._log("SUCCESS", message)

    def warning(self, message: str):
        """记录WARNING级别日志"""
        self._log("WARNING", message)

    def error(self, message: str):
        """记录ERROR级别日志"""
        self._log("ERROR", message)

    def broadcast_event(self, event_type: str, payload: dict):
        """向所有 WebSocket 连接广播自定义事件（非日志型消息）"""
        self._schedule_broadcast({
            "type": event_type,
            "data": payload
        })

    def update_progress(self, current: int, total: int):
        """
        更新进度

        Args:
            current: 当前进度
            total: 总进度
        """
        percentage = int((current / total) * 100) if total > 0 else 0
        self.current_progress = {
            "current": current,
            "total": total,
            "percentage": percentage
        }

        # 异步广播进度
        self._schedule_broadcast({
            "type": "progress",
            "data": self.current_progress
        })

    def get_recent_logs(self, count: int = 100) -> List[dict]:
        """
        获取最近的日志

        Args:
            count: 返回的日志条数

        Returns:
            日志列表
        """
        return list(self.logs)[-count:]

    def clear_logs(self):
        """清空日志"""
        self.logs.clear()
