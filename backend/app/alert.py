"""告警推送 - 飞书 webhook（M7）

- 配置：app_settings 表 alert_webhook（飞书机器人 webhook URL）+ alert_enabled
- 发送：POST 飞书自定义机器人 webhook（text 消息），异步线程不阻塞主流程
- 触发：自愈重启/放弃、实例降级等（由调用方主动调 send_alert）
"""
import logging
import threading

logger = logging.getLogger("alert")

_WEBHOOK_KEY = "alert_webhook"
_ENABLED_KEY = "alert_enabled"


def get_alert_config() -> dict:
    """读取告警配置"""
    from app.database import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings WHERE key IN (?, ?)",
                                (_WEBHOOK_KEY, _ENABLED_KEY)).fetchall()
        cfg = {r["key"]: r["value"] for r in rows}
        return {
            "webhook": cfg.get(_WEBHOOK_KEY, ""),
            "enabled": cfg.get(_ENABLED_KEY, "1") == "1",
        }
    except Exception:
        return {"webhook": "", "enabled": False}


def save_alert_config(webhook: str, enabled: bool):
    """保存告警配置"""
    from app.database import get_conn, now
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
            (_WEBHOOK_KEY, webhook.strip(), now()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?,?,?)",
            (_ENABLED_KEY, "1" if enabled else "0", now()),
        )


def send_alert(title: str, message: str, force: bool = False) -> bool:
    """发送飞书告警（异步线程，不阻塞）。返回是否已投递。"""
    cfg = get_alert_config()
    if not force and not cfg["enabled"]:
        return False
    webhook = cfg["webhook"]
    if not webhook:
        logger.warning("告警未发送：未配置 webhook（title=%s）", title)
        return False

    def _post():
        try:
            import httpx
            text = f"🔔 **{title}**\n{message}"
            payload = {"msg_type": "text", "content": {"text": text}}
            with httpx.Client(timeout=8) as c:
                r = c.post(webhook, json=payload)
            if r.status_code != 200:
                logger.warning("告警发送失败: HTTP %s %s", r.status_code, r.text[:200])
            else:
                logger.info("告警已发送: %s", title)
        except Exception as e:
            logger.warning("告警发送异常: %s", e)

    t = threading.Thread(target=_post, name="alert-send", daemon=True)
    t.start()
    return True
