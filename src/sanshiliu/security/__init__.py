"""L8 安全权限层；与 Claude settings.json 协议对齐的 allow/deny + 用户确认状态机。"""

from sanshiliu.security.bash_classifier import classify as classify_bash
from sanshiliu.security.bash_classifier import label as bash_danger_label
from sanshiliu.security.path_guard import PathGuard
from sanshiliu.security.permission import PermissionManager
from sanshiliu.security.prompts import DenyAllConfirmer, ReplConfirmer, render_request
from sanshiliu.security.settings_loader import SettingsLoader, append_allow_pattern
from sanshiliu.security.types import (
    Confirmer,
    ConfirmRequest,
    ConfirmResponse,
    DangerLevel,
    DecisionKind,
    DecisionScope,
    DefaultMode,
    PermissionDecision,
    PermissionSettings,
    canonical_tool_alias,
)

__all__ = [
    "ConfirmRequest",
    "ConfirmResponse",
    "Confirmer",
    "DangerLevel",
    "DecisionKind",
    "DecisionScope",
    "DefaultMode",
    "DenyAllConfirmer",
    "PathGuard",
    "PermissionDecision",
    "PermissionManager",
    "PermissionSettings",
    "ReplConfirmer",
    "SettingsLoader",
    "append_allow_pattern",
    "bash_danger_label",
    "canonical_tool_alias",
    "classify_bash",
    "render_request",
]
