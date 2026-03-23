"""
费用追踪器：追踪 ScraperAPI 积分消耗和 LLM API 额度变化
"""
import httpx
from typing import Optional


class CostTracker:
    """追踪单次运行的 API 费用消耗"""

    # ScraperAPI: $49/月 = 100,000 credits
    SCRAPER_COST_PER_CREDIT = 49.0 / 100_000  # USD

    # LLM API (api.gpt.ge): 1 实际额度 = 2 RMB, 额度单位 / 500000 = 实际额度
    LLM_QUOTA_DIVISOR = 500_000
    LLM_RMB_PER_UNIT = 2.0

    def __init__(self):
        self.reset()

    def reset(self):
        """重置所有计数器"""
        # ScraperAPI
        self.scraper_total_credits = 0
        self.scraper_request_count = 0

        # LLM API 额度（通过 /api/user/self 查询）
        self.llm_quota_before: Optional[int] = None
        self.llm_quota_after: Optional[int] = None
        self.llm_used_quota_before: Optional[int] = None
        self.llm_used_quota_after: Optional[int] = None

    def add_scraper_credits(self, credits: int):
        """记录一次 ScraperAPI 请求的积分消耗"""
        self.scraper_total_credits += credits
        self.scraper_request_count += 1

    async def query_llm_quota(self, base_url: str, access_token: str, user_id: str) -> Optional[dict]:
        """
        查询 LLM API 额度

        Args:
            base_url: API base URL (如 https://api.gpt.ge/v1/)
            access_token: 系统访问令牌
            user_id: 用户数字 ID

        Returns:
            {"quota": int, "used_quota": int} 或 None
        """
        if not access_token or not user_id:
            return None

        # 从 base_url 推断 API host (去掉 /v1/ 后缀)
        api_host = base_url.rstrip("/")
        if api_host.endswith("/v1"):
            api_host = api_host[:-3]

        url = f"{api_host}/api/user/self"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Api-User": str(user_id),
        }

        try:
            async with httpx.AsyncClient(verify=False, timeout=10) as client:
                r = await client.get(url, headers=headers)
                data = r.json()
                if data.get("success"):
                    return {
                        "quota": data["data"]["quota"],
                        "used_quota": data["data"]["used_quota"],
                    }
        except Exception:
            pass
        return None

    async def snapshot_before(self, base_url: str, access_token: str, user_id: str):
        """运行前快照 LLM 额度"""
        result = await self.query_llm_quota(base_url, access_token, user_id)
        if result:
            self.llm_quota_before = result["quota"]
            self.llm_used_quota_before = result["used_quota"]

    async def snapshot_after(self, base_url: str, access_token: str, user_id: str):
        """运行后快照 LLM 额度"""
        result = await self.query_llm_quota(base_url, access_token, user_id)
        if result:
            self.llm_quota_after = result["quota"]
            self.llm_used_quota_after = result["used_quota"]

    def get_summary(self) -> dict:
        """生成费用摘要"""
        summary = {
            "scraper_credits": self.scraper_total_credits,
            "scraper_requests": self.scraper_request_count,
            "scraper_cost_usd": round(self.scraper_total_credits * self.SCRAPER_COST_PER_CREDIT, 4),
        }

        # LLM 额度差值
        if self.llm_used_quota_before is not None and self.llm_used_quota_after is not None:
            raw_diff = self.llm_used_quota_after - self.llm_used_quota_before
            actual_units = raw_diff / self.LLM_QUOTA_DIVISOR
            summary["llm_quota_consumed_raw"] = raw_diff
            summary["llm_quota_consumed"] = round(actual_units, 4)
            summary["llm_cost_rmb"] = round(actual_units * self.LLM_RMB_PER_UNIT, 2)
            summary["llm_remaining_raw"] = self.llm_quota_after
            summary["llm_remaining"] = round(self.llm_quota_after / self.LLM_QUOTA_DIVISOR, 2)
            summary["llm_remaining_rmb"] = round(
                (self.llm_quota_after / self.LLM_QUOTA_DIVISOR) * self.LLM_RMB_PER_UNIT, 2
            )
            summary["llm_tracked"] = True
        else:
            summary["llm_tracked"] = False

        return summary
