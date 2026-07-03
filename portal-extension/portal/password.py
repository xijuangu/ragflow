"""密码哈希工具 — bcrypt 直接调用(避免 passlib 与新版 bcrypt 兼容问题)。

独立模块,不依赖 models,避免循环导入。
"""

import bcrypt


def hash_password(plain: str) -> str:
    """明文密码 → bcrypt 哈希字符串。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码是否匹配 bcrypt 哈希。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
