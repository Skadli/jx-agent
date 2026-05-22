"""内置工具集；都通过 build_*_tool 工厂构造，外层注入运行时依赖（cwd / api_key）。"""

from sanshiliu.tools.builtin.bash import build_bash_exec_tool
from sanshiliu.tools.builtin.file_io import build_file_read_tool, build_file_write_tool
from sanshiliu.tools.builtin.web_search import build_web_search_tool

__all__ = [
    "build_bash_exec_tool",
    "build_file_read_tool",
    "build_file_write_tool",
    "build_web_search_tool",
]
