"""L3 身份层；读 persona/*.md 拼装 PersonaSnapshot，支持热重载。"""

from sanshiliu.identity.loader import PersonaLoader
from sanshiliu.identity.types import PERSONA_FILES, PersonaSnapshot
from sanshiliu.identity.watcher import PersonaWatcher

__all__ = ["PERSONA_FILES", "PersonaLoader", "PersonaSnapshot", "PersonaWatcher"]
