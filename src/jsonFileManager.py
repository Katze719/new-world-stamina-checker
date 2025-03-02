import json
import os
import asyncio

class JsonFileManager:
    def __init__(self, file_path: str, post_load: callable = None):
        """
        :param file_path: Pfad zur JSON-Datei.
        :param post_load: Optional eine Funktion, die nach dem Laden auf die Daten angewendet wird.
                          z.B. um fehlende Schlüssel zu ergänzen.
        """
        self.file_path = file_path
        self.lock = asyncio.Lock()
        self.post_load = post_load

    async def load(self):
        async with self.lock:
            if not os.path.exists(self.file_path):
                return {}
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if self.post_load:
                data = self.post_load(data)
            return data

    async def save(self, data):
        async with self.lock:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)