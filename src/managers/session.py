import aiohttp


class SessionManager:
    def __init__(self, config) -> None:
        super().__init__()
        self.config = config

    def __getattr__(self, item):
        if item == "speechace":
            _session = self.__dict__.get("speechace")
            if _session is None or _session.closed:
                self.speechace = aiohttp.ClientSession(base_url="https://api.speechace.co",
                                                connector=aiohttp.TCPConnector(limit=100000),
                                                timeout=aiohttp.ClientTimeout(total=600))
                return self.speechace
                
        return self.__dict__[item]

    async def close(self):
        await self.sd.close()
