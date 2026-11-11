#!/usr/bin/env bash
# Install the repository script without replacing a user's configuration.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법: ./install.sh [--init-config]

~/.local/bin/autocommit 를 이 저장소의 bin/autocommit 심볼릭 링크로 설치합니다.
주간 review timer(systemd/autocommit-review.*)도 링크하고 활성화합니다.
--init-config 는 ~/.config/autocommit/repos.conf 가 아직 없을 때만 예제 설정을 복사합니다.
EOF
}

init_config=0
case "${1:-}" in
  '') ;;
  --init-config) init_config=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source_file="$repo_dir/bin/autocommit"
target_dir="$HOME/.local/bin"
target="$target_dir/autocommit"
config_dir="$HOME/.config/autocommit"
config_file="$config_dir/repos.conf"
temporary=

cleanup() {
  [ -n "$temporary" ] && [ -L "$temporary" ] && rm -f -- "$temporary" || true
}
trap cleanup EXIT

[ -f "$source_file" ] || { echo "실행 파일이 없습니다: $source_file" >&2; exit 1; }
bash -n "$source_file"
mkdir -p "$target_dir"

if [ -d "$target" ]; then
  echo "설치 대상이 디렉터리입니다. 중단합니다: $target" >&2
  exit 1
fi

if [ -L "$target" ]; then
  current=$(readlink -f -- "$target" 2>/dev/null || true)
  if [ "$current" = "$source_file" ]; then
    echo "이미 설치됨: $target -> $source_file"
  else
    echo "설치 대상이 다른 심볼릭 링크입니다. 중단합니다: $target" >&2
    exit 1
  fi
elif [ -e "$target" ]; then
  [ -f "$target" ] || { echo "설치 대상이 일반 파일이 아닙니다. 중단합니다: $target" >&2; exit 1; }
  stamp=$(date +%Y%m%dT%H%M%S)
  backup="$target.backup-$stamp"
  while [ -e "$backup" ] || [ -L "$backup" ]; do
    stamp="${stamp}-$(od -An -N2 -tu2 /dev/urandom | tr -d ' ')"
    backup="$target.backup-$stamp"
  done
  cp -p -- "$target" "$backup"
  echo "기존 실행 파일 백업: $backup"
fi

if [ ! -L "$target" ]; then
  temporary="$target.tmp-$$"
  ln -s -- "$source_file" "$temporary"
  mv -Tf -- "$temporary" "$target"
  temporary=
  echo "설치됨: $target -> $source_file"
fi

# 주간 review timer. 저장소의 unit 파일을 링크하고, 이미 같은 링크면 그대로 둔다.
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
for unit in autocommit-review.service autocommit-review.timer; do
  unit_source="$repo_dir/systemd/$unit"
  unit_target="$unit_dir/$unit"
  if [ -L "$unit_target" ] && [ "$(readlink -f -- "$unit_target")" = "$unit_source" ]; then
    continue
  fi
  [ -e "$unit_target" ] || [ -L "$unit_target" ] && { echo "unit 대상이 이미 있습니다. 중단합니다: $unit_target" >&2; exit 1; }
  ln -s -- "$unit_source" "$unit_target"
  echo "unit 설치됨: $unit_target -> $unit_source"
done
if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable --now autocommit-review.timer
else
  echo "systemd user 세션이 없어 timer 활성화를 건너뜁니다: systemctl --user enable --now autocommit-review.timer" >&2
fi

if [ "$init_config" -eq 1 ]; then
  if [ -e "$config_file" ] || [ -L "$config_file" ]; then
    echo "기존 설정 유지: $config_file"
  else
    mkdir -p "$config_dir"
    cp -- "$repo_dir/examples/repos.conf" "$config_file"
    chmod 600 "$config_file"
    echo "예제 설정 생성: $config_file"
  fi
fi
