"""写拒绝：封存周禁止一切会动格位/流程状态的写操作。

生成、对调确认、撤销类写操作共用同一道闸；HTTP 层把
SealedWriteError 翻成 409 week_sealed。
"""

SEALED = "sealed"


class SealedWriteError(Exception):
    """封存周上的写操作被拒绝。"""

    def __init__(self, week_id):
        self.week_id = week_id
        super().__init__(f"week {week_id} is sealed")


def is_sealed(week) -> bool:
    return week is not None and week["status"] == SEALED


def ensure_week_writable(week) -> None:
    """week 为 sqlite.Row 或 dict；封存即抛 SealedWriteError。"""
    if is_sealed(week):
        raise SealedWriteError(week["id"])
