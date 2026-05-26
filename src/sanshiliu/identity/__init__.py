"""L3 身份层；读 persona/core/*.md 拼装 PersonaSnapshot，支持热重载。

PR2 增 modules 子系统：PersonaModule（按需注入 system prompt 片段）。
"""

from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.module_activator import PersonaModuleActivator
from sanshiliu.identity.module_loader import PersonaModuleLoader
from sanshiliu.identity.module_types import PersonaModule
from sanshiliu.identity.types import CORE_DIRNAME, MODULES_DIRNAME, PersonaSnapshot
from sanshiliu.identity.watcher import PersonaWatcher

__all__ = [
    "CORE_DIRNAME",
    "MODULES_DIRNAME",
    "PersonaLoader",
    "PersonaModule",
    "PersonaModuleActivator",
    "PersonaModuleLoader",
    "PersonaSnapshot",
    "PersonaWatcher",
]
