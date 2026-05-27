# 跑 mypy --strict；errors > 0 时退出非 0。供 pre-commit / 手动 CI 调用。
# 用法（在 jx-agent/ 根执行）：
#   .\scripts\check_mypy.ps1
$ErrorActionPreference = "Stop"

$output = & python -m mypy src/sanshiliu 2>&1
$output | ForEach-Object { Write-Output $_ }

if ($output -match "Found \d+ errors") {
    Write-Error "mypy baseline 已漂移：禁止合并"
    exit 1
}
exit 0
