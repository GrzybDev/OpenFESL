from enum import StrEnum


class AdminAction(StrEnum):
    """What an admin did, as the log of their actions records it with its target and details."""

    SETTING = "setting"
    """Changed the setting named as the target; the details hold its value, none when reset."""
