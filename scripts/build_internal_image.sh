#!/usr/bin/env bash
# Build the SQAG internal image from one exact commit and print one JSON record.
#
#   archive  (laptop, Git Bash, in a clean worktree of the repo)
#     bash scripts/build_internal_image.sh archive --commit <40-hex sha> --out <dir>
#   build    (VPS, as the deploy user)
#     bash build_internal_image.sh build --commit <40-hex sha> --tar <path> --tar-sha256 <hex>
#
# Options for build:
#   --workdir <dir>       default /root/sqag-build-<sha>; must not exist yet
#   --helper-image <ref>  default docker.io/coollabsio/coolify-helper:1.0.19
#   --dry-run             validate arguments and print the planned commands; run nothing
#
# Logs go to stderr. The last line on stdout is the JSON record. No secrets are
# read or printed. The build follows the 9f67167 release build: git archive with
# no line-ending conversion, Nixpacks through the Coolify helper image, tag
# sqag-internal:<sha>, then offline checks inside the image with no network.
set -euo pipefail

DEFAULT_HELPER_IMAGE="docker.io/coollabsio/coolify-helper:1.0.19"
EXPECTED_FONTS=(
  "Calibri=Carlito / Regular"
  "Calibri:style=Bold=Carlito / Bold"
  "Arial=Liberation Sans / Regular"
  "Arial:style=Bold=Liberation Sans / Bold"
)

log() { printf '%s\n' "build_internal_image: $*" >&2; }
die() { log "error: $*"; exit 1; }

json_escape() {
  local s=${1-}
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

json_string() { printf '"%s"' "$(json_escape "${1-}")"; }

json_list() {
  local first=1 item
  printf '['
  for item in "$@"; do
    if [ "$first" -eq 0 ]; then printf ','; fi
    first=0
    json_string "$item"
  done
  printf ']'
}

sha256_of() { sha256sum "$1" | awk '{print $1}'; }

require_sha() {
  [[ "${1-}" =~ ^[0-9a-f]{40}$ ]] || die "--commit must be the full 40-character lowercase commit SHA"
}

usage() { sed -n '2,20p' "$0" >&2; exit 2; }

# Read the commit id that git archive stores in the tar's pax global header.
tar_commit_id() {
  head -c 1024 "$1" | tail -c 512 | tr -d '\000' | sed -n 's/^52 comment=\([0-9a-f]\{40\}\)$/\1/p' | head -n 1
}

cmd_archive() {
  local commit="" out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --commit) commit=${2-}; shift 2 ;;
      --out) out=${2-}; shift 2 ;;
      *) die "unknown archive option: $1" ;;
    esac
  done
  require_sha "$commit"
  [ -n "$out" ] || die "--out is required"
  git cat-file -e "${commit}^{commit}" 2>/dev/null || die "commit not found in this repository; fetch it first"
  mkdir -p "$out"
  local tar="$out/sqag-${commit:0:7}.tar"
  [ ! -e "$tar" ] || die "$tar already exists; use a fresh --out folder"
  git -c core.autocrlf=false archive --format=tar -o "$tar" "$commit"
  local header_commit file_count digest
  header_commit=$(tar_commit_id "$tar")
  [ "$header_commit" = "$commit" ] || die "archive header commit does not match --commit"
  file_count=$(tar -tf "$tar" | grep -vc '/$' || true)
  digest=$(sha256_of "$tar")
  printf '{"schema":"sqag-image-archive/v1","source_commit":%s,"tar":%s,"tar_sha256":%s,"tar_file_count":%s}\n' \
    "$(json_string "$commit")" "$(json_string "$tar")" "$(json_string "$digest")" "$file_count"
}

# Run one command string inside the image with no network. The image
# entrypoint is `bash -l -c`, so the command is passed as one string.
in_image() {
  docker run --rm --network none "$IMAGE_TAG" "$1"
}

cmd_build() {
  local commit="" tar="" tar_sha="" workdir="" helper="$DEFAULT_HELPER_IMAGE" dry_run=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --commit) commit=${2-}; shift 2 ;;
      --tar) tar=${2-}; shift 2 ;;
      --tar-sha256) tar_sha=${2-}; shift 2 ;;
      --workdir) workdir=${2-}; shift 2 ;;
      --helper-image) helper=${2-}; shift 2 ;;
      --dry-run) dry_run=1; shift ;;
      *) die "unknown build option: $1" ;;
    esac
  done
  require_sha "$commit"
  [ -n "$tar" ] || die "--tar is required"
  [[ "$tar_sha" =~ ^[0-9a-f]{64}$ ]] || die "--tar-sha256 must be 64 lowercase hex characters"
  [[ "$helper" =~ ^[a-z0-9./-]+:[A-Za-z0-9._-]+(@sha256:[0-9a-f]{64})?$ ]] || die "--helper-image must name a tag"
  workdir=${workdir:-/root/sqag-build-${commit:0:7}}
  case "$workdir" in
    /|/root|/home|/var|/tmp|"") die "--workdir must be a dedicated folder" ;;
  esac
  IMAGE_TAG="sqag-internal:${commit}"
  local container="sqag-nixpacks-build-${commit:0:7}"
  local nixpacks=(docker run --rm --name "$container" -v "$workdir:$workdir" -v /var/run/docker.sock:/var/run/docker.sock
    -w "$workdir" --entrypoint nixpacks "$helper" build "$workdir" --name "$IMAGE_TAG")

  if [ "$dry_run" -eq 1 ]; then
    printf 'PLAN sha256sum %s (expect %s)\n' "$tar" "$tar_sha"
    printf 'PLAN check the archive header commit equals %s\n' "$commit"
    printf 'PLAN mkdir %s && tar -xf %s -C %s\n' "$workdir" "$tar" "$workdir"
    printf 'PLAN %s\n' "${nixpacks[*]}"
    printf 'PLAN docker image inspect %s\n' "$IMAGE_TAG"
    printf 'PLAN offline checks in %s with --network none: python version, server import, soffice, fc-match x%s, CR count\n' "$IMAGE_TAG" "${#EXPECTED_FONTS[@]}"
    return 0
  fi

  command -v docker >/dev/null || die "docker is not available"
  [ -f "$tar" ] || die "--tar file not found"
  [ ! -e "$workdir" ] || die "$workdir already exists; remove it or pass a fresh --workdir"
  local actual_sha header_commit
  actual_sha=$(sha256_of "$tar")
  [ "$actual_sha" = "$tar_sha" ] || die "tar sha256 does not match --tar-sha256"
  header_commit=$(tar_commit_id "$tar")
  [ "$header_commit" = "$commit" ] || die "archive header commit does not match --commit"

  mkdir -p "$workdir"
  tar -xf "$tar" -C "$workdir"
  local file_count expected_python
  file_count=$(find "$workdir" -type f | wc -l | tr -d ' ')
  expected_python=$(tr -d '[:space:]' < "$workdir/.python-version")
  log "unpacked $file_count files to $workdir; building $IMAGE_TAG with $helper"

  local build_log="$workdir.nixpacks.log"
  if ! "${nixpacks[@]}" > "$build_log" 2>&1; then
    die "nixpacks build failed; see $build_log"
  fi
  grep -q "Successfully Built" "$build_log" || die "nixpacks log has no success line; see $build_log"

  local inspect image_id image_created image_size
  inspect=$(docker image inspect --format '{{.Id}} {{.Created}} {{.Size}}' "$IMAGE_TAG")
  read -r image_id image_created image_size <<< "$inspect"
  local env_names=()
  while IFS= read -r line; do
    [ -n "$line" ] && env_names+=("${line%%=*}")
  done < <(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE_TAG")

  local failures=()
  local python_version import_ok soffice fcmatch cr_count soffice_version
  python_version=$(in_image 'python --version' 2>&1 | tail -n 1 | awk '{print $2}') || true
  [ "$python_version" = "$expected_python" ] || failures+=("python version is not $expected_python")
  import_ok=$(in_image 'cd /app && python -B -c "import sys;sys.path.insert(0,\"webapp\");import server;print(\"import-ok\")"' 2>/dev/null | tail -n 1) || true
  [ "$import_ok" = "import-ok" ] || failures+=("server import failed")
  soffice=$(in_image 'command -v soffice' 2>/dev/null | tail -n 1) || true
  [ -n "$soffice" ] || failures+=("soffice missing")
  soffice_version=$(in_image 'soffice --version | head -n 1' 2>/dev/null | tail -n 1) || true
  fcmatch=$(in_image 'command -v fc-match' 2>/dev/null | tail -n 1) || true
  [ -n "$fcmatch" ] || failures+=("fc-match missing")
  local font_results=() pair query expected got
  for pair in "${EXPECTED_FONTS[@]}"; do
    query=${pair%=*}
    expected=${pair##*=}
    got=$(in_image "fc-match -f '%{family[0]} / %{style[0]}' '$query'" 2>/dev/null | tail -n 1) || true
    font_results+=("$query => $got")
    [ "$got" = "$expected" ] || failures+=("fc-match $query is not $expected")
  done
  cr_count=$(in_image "tr -cd '\\r' < /app/webapp/server.py | wc -c" 2>/dev/null | tail -n 1 | tr -d ' ') || true
  [ "$cr_count" = "0" ] || failures+=("webapp/server.py has CR line endings")

  local result="pass"
  [ "${#failures[@]}" -eq 0 ] || result="fail"
  printf '{"schema":"sqag-image-build/v1","result":%s,"failures":%s,"source_commit":%s,"tar_sha256":%s,"tar_file_count":%s,' \
    "$(json_string "$result")" "$(json_list "${failures[@]+"${failures[@]}"}")" "$(json_string "$commit")" \
    "$(json_string "$actual_sha")" "$file_count"
  printf '"workdir":%s,"build_log":%s,"helper_image":%s,"image_tag":%s,"image_id":%s,"image_created":%s,"image_size":%s,' \
    "$(json_string "$workdir")" "$(json_string "$build_log")" "$(json_string "$helper")" "$(json_string "$IMAGE_TAG")" \
    "$(json_string "$image_id")" "$(json_string "$image_created")" "${image_size:-0}"
  printf '"python_version":%s,"server_import":%s,"soffice":%s,"soffice_version":%s,"fc_match":%s,"fonts":%s,"server_py_cr_count":%s,"image_env_names":%s}\n' \
    "$(json_string "$python_version")" "$(json_string "$import_ok")" "$(json_string "$soffice")" \
    "$(json_string "$soffice_version")" "$(json_string "$fcmatch")" "$(json_list "${font_results[@]}")" \
    "$(json_string "$cr_count")" "$(json_list "${env_names[@]+"${env_names[@]}"}")"
  [ "$result" = "pass" ]
}

case "${1-}" in
  archive) shift; cmd_archive "$@" ;;
  build) shift; cmd_build "$@" ;;
  *) usage ;;
esac
