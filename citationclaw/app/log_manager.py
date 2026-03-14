import asyncio
from datetime import datetime
from typing import List, Set
from collections import deque
from fastapi import WebSocket


class LogManager:
    def __init__(self, max_logs: int = 1000):
        """
        日志管理器,负责日志记录和WebSocket广播

        Args:
            max_logs: 最大保留日志条数
        """
        self.logs = deque(maxlen=max_logs)
        self.websocket_connections: Set[WebSocket] = set()
        self.current_progress = {"current": 0, "total": 100, "percentage": 0}

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

    def _log(self, level: str, message: str):
        """
        记录日志

        Args:
            level: 日志级别(INFO, SUCCESS, WARNING, ERROR)
            message: 日志消息
        """
        log_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": message
        }
        self.logs.append(log_entry)

        # 打印到控制台
        print(f"[{log_entry['timestamp']}] [{level}] {message}")

        # 异步广播(不阻塞)
        asyncio.create_task(self._broadcast({
            "type": "log",
            "data": log_entry
        }))

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
        asyncio.create_task(self._broadcast({
            "type": event_type,
            "data": payload
        }))

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
        asyncio.create_task(self._broadcast({
            "type": "progress",
            "data": self.current_progress
        }))

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
