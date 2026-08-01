"""应用配置"""
import os
from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    # NUC12 上模型存放目录
    model_dir: str = os.environ.get("LLAMA_MODEL_DIR", "/home/zhangjiyu/models")

    # llama.cpp 官方 SYCL 镜像
    llama_image: str = "ghcr.io/ggml-org/llama.cpp:server-intel"

    # 服务数据（SQLite 数据库文件）
    data_dir: str = os.environ.get("LLAMA_STUDIO_DATA", str(Path.home() / ".llama-studio"))

    # 默认 API 端口范围
    port_min: int = 8000
    port_max: int = 8999

    # WebUI 自身端口（NUC12 上 9000 被 portainer 占用，用 9100）
    webui_port: int = 9100

    # 是否允许 GPU 热切换（多卡时指定设备）
    gpu_devices: list[str] = ["/dev/dri/card1", "/dev/dri/renderD129"]

    @property
    def db_path(self) -> Path:
        p = Path(self.data_dir) / "studio.db"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
