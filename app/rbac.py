"""角色与权限判定。

角色三档：
- owner：系统主人，由 settings.owner_email 锚定（邮箱权威，不依赖可被改的 DB 字段），
  拥有全部待遇（飞猪手动不限次、飞常准备用源、30 分钟每轮调度、管理员面板）。
- vip ：轻量会员——飞猪手动刷新不限次、自动调度 30 分钟一轮；不启用飞常准备用源
        （飞常准每月免费额度仅留给主人本人，按需求定）。
- normal：普通用户——飞猪手动刷新每日 10 次、自动调度隔轮（约 60 分钟），无备用源。

is_high_tier 表示享受“放宽手动刷新额度 + 高频调度”的高档用户（owner/vip）。
"""
from app.config import settings


def role_of(user) -> str:
    if user is None:
        return "normal"
    email = getattr(user, "email", None)
    if email and settings.owner_email and email == settings.owner_email:
        return "owner"
    db_role = getattr(user, "role", None)
    return "vip" if db_role == "vip" else "normal"


def is_owner(user) -> bool:
    return role_of(user) == "owner"


def is_vip(user) -> bool:
    return role_of(user) == "vip"


def is_high_tier(user) -> bool:
    return role_of(user) in ("owner", "vip")
