import httpx

from ..config import config


class TTSClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or config.TTS_URL).rstrip("/")

    async def synthesize(self, text: str, lang: str = "en", timeout: float = 60.0) -> bytes:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}/synthesize", json={"text": text, "lang": lang}
            )
            resp.raise_for_status()
            return resp.content

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.json() if resp.status_code == 200 else {"status": "error", "code": resp.status_code}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "detail": str(e)}
