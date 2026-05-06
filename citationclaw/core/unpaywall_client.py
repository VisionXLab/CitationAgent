"""Unpaywall API client for open-access PDF URL lookup by DOI."""
from typing import Optional

from citationclaw.core.http_utils import make_async_client


class UnpaywallClient:
    def __init__(self, email: str = "citationclaw@research.tool"):
        self._client = make_async_client(timeout=10.0)
        self._email = email

    async def lookup(self, doi: str) -> Optional[str]:
        """Return the best OA PDF URL for the given DOI, or None."""
        if not doi:
            return None
        doi_clean = (
            doi.replace("https://doi.org/", "")
            .replace("http://doi.org/", "")
            .strip()
            .lstrip("/")
        )
        if not doi_clean:
            return None
        try:
            resp = await self._client.get(
                f"https://api.unpaywall.org/v2/{doi_clean}?email={self._email}",
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            best = (resp.json().get("best_oa_location") or {}).get("url_for_pdf", "")
            return best or None
        except Exception:
            return None

    async def close(self):
        await self._client.aclose()
