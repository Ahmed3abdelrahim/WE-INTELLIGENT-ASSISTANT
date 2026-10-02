import httpx

from ..config import config


class ASRClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or config.ASR_URL).rstrip("/")

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        language: str | None = None,
        hotwords: str | None = None,
        beam_size: int = 1,
        timeout: float = 120.0,
    ) -> dict:
        files = {"file": (filename, audio_bytes)}
        data = {"beam_size": str(beam_size)}
        if language:
            data["language"] = language
        if hotwords:
            data["hotwords"] = hotwords
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/transcribe", files=files, data=data)
            resp.raise_for_status()
            return resp.json()

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.json() if resp.status_code == 200 else {"status": "error", "code": resp.status_code}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "detail": str(e)}
