import json
from pathlib import Path

CURRENT_PATH = Path(__file__).parent


class Manifest:
    """读取组件自己的 manifest.json，避免依赖 HA 内部辅助函数。"""

    def __init__(self):
        self.manifest_path = CURRENT_PATH / "manifest.json"
        self.update()

    def update(self):
        try:
            with self.manifest_path.open(encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, ValueError):
            data = {}

        self.domain = data.get("domain")
        self.name = data.get("name")
        self.version = data.get("version")
        self.documentation = data.get("documentation")


manifest = Manifest()
