#!/usr/bin/env bash
# ============================================================================
# STTP 环境变量加载器（bash / Git Bash）
# 用法：  source env.sh
# ============================================================================
_sttp_env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.env"

if [ ! -f "$_sttp_env_file" ]; then
  echo "env.sh: 未找到 $_sttp_env_file" >&2
  return 1 2>/dev/null || exit 1
fi

while IFS= read -r _line || [ -n "$_line" ]; do
  case "$_line" in \#*|'') continue ;; esac
  case "$_line" in *=*) ;; *) continue ;; esac
  _k="${_line%%=*}"
  _v="${_line#*=}"
  _k="$(printf '%s' "$_k" | tr -d '[:space:]\r')"
  _v="$(printf '%s' "$_v" | tr -d '\r')"
  case "$_k" in
    NEO4J_URI|NEO4J_USER|NEO4J_PASSWORD|NEO4J_DATABASE|GRAPH_DATA_FILE|VIZ_PORT|STTP_PYTHON)
      export "$_k=$_v" ;;
  esac
done < "$_sttp_env_file"

unset _line _k _v _sttp_env_file
echo "[STTP] 环境变量已加载 -> NEO4J_URI=$NEO4J_URI  NEO4J_DATABASE=$NEO4J_DATABASE"
