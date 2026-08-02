"""应用配置"""
import os
from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    # 模型存放目录（容器内挂载点 /models，本机开发时用环境变量覆盖）
    model_dir: str = os.environ.get("LLAMA_MODEL_DIR", "/models")

    # llama-server router 内部地址（同容器内进程）
    router_url: str = os.environ.get("LLAMA_ROUTER_URL", "http://127.0.0.1:8070")

    # 服务数据（SQLite 数据库文件）
    data_dir: str = os.environ.get("LLAMA_STUDIO_DATA", str(Path.home() / ".llama-studio"))

    # WebUI 自身端口
    webui_port: int = int(os.environ.get("WEBUI_PORT", "9100"))

    # router 最大同时驻留模型数
    models_max: int = int(os.environ.get("MODELS_MAX", "3"))

    @property
    def db_path(self) -> Path:
        p = Path(self.data_dir) / "studio.db"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
