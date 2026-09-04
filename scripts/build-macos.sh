#!/bin/bash
# Native macOS pilot build. No credentials are collected unless explicitly supplied.
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
clean=false
oauth_config=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean) clean=true; shift ;;
        --oauth-client-config)
            [[ $# -ge 2 && -n "$2" ]] || fail "--oauth-client-config requires a file path"
            oauth_config="$2"; shift 2 ;;
        --help|-h)
            echo 'Usage: ./scripts/build-macos.sh [--clean] [--oauth-client-config FILE]'
            exit 0 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

[[ "$(uname -s)" == Darwin ]] || fail "Build on macOS; cross-compilation is not supported."
macos_version="$(sw_vers -productVersion)"
architecture="$(uname -m)"
case "$architecture" in arm64|x86_64) ;; *) fail "Unsupported architecture: $architecture" ;; esac
# Refuse Rosetta so an Apple Silicon build cannot be mislabeled as native Intel.
translated="$(sysctl -in sysctl.proc_translated 2>/dev/null || true)"
[[ "$translated" != 1 ]] || fail "Use native arm64 Python and Terminal, outside Rosetta."

python="${PYTHON:-python3}"
command -v "$python" >/dev/null || fail "Python 3.11+ is required. Install .[dev] first."
"$python" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
python_arch="$("$python" -c 'import platform; print(platform.machine())')"
[[ "$python_arch" == "$architecture" ]] || fail "Python architecture does not match this Mac."
"$python" --version
"$python" -m PyInstaller --version
printf 'macOS: %s\nArchitecture: %s\n' "$macos_version" "$architecture"

# Resolve an explicit OAuth path relative to the caller, before changing directory.
if [[ -n "$oauth_config" ]]; then
    oauth_config="$("$python" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$oauth_config")"
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"
hygiene="$repo_root/scripts/verify_release_hygiene.py"
if [[ -n "$oauth_config" ]]; then
    "$python" "$hygiene" --validate-oauth-client "$oauth_config"
fi
verify_hygiene() {
    if [[ -n "$oauth_config" ]]; then
        "$python" "$hygiene" "$1" --allow-oauth-client
    else
        "$python" "$hygiene" "$1"
    fi
}
version="$("$python" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
source_sha="$(git rev-parse HEAD)"
printf 'Version: %s\nSource Git SHA: %s\n' "$version" "$source_sha"

# Keep all Mac outputs separate from the working Windows distribution.
for path in build dist build/macos dist/macos dist/macos/releases; do
    [[ ! -L "$repo_root/$path" ]] || fail "Output directory must not be a symlink: $path"
done
if "$clean"; then
    rm -rf "$repo_root/build/macos" "$repo_root/dist/macos"
fi
mkdir -p "$repo_root/build/macos" "$repo_root/dist/macos/releases"
work_root="$(mktemp -d "$repo_root/build/macos/run.XXXXXX")"
trap 'rm -rf "$work_root"' EXIT

"$python" -m PyInstaller --noconfirm --clean \
    "$repo_root/packaging/mailbox-rescue-macos.spec" \
    --distpath "$work_root/dist" --workpath "$work_root/build"
app="$work_root/dist/Mailbox Rescue.app"
[[ -x "$app/Contents/MacOS/Mailbox Rescue" ]] || fail "Expected app executable was not built."
[[ "$(lipo -archs "$app/Contents/MacOS/Mailbox Rescue")" == "$architecture" ]] || fail "Unexpected binary architecture."

stage="$work_root/stage/Mailbox Rescue"
mkdir -p "$stage"
# ditto preserves the framework symlinks required by PyInstaller macOS bundles.
ditto --norsrc --noextattr "$app" "$stage/Mailbox Rescue.app"
cp "$repo_root/START HERE.txt" "$stage/START HERE.txt"
cmp "$repo_root/START HERE.txt" "$stage/START HERE.txt"
if [[ -n "$oauth_config" ]]; then
    cp "$oauth_config" "$stage/client_secret.json"
    cmp "$oauth_config" "$stage/client_secret.json"
fi
verify_hygiene "$stage"

zip_name="Mailbox-Rescue-v${version}-macos-${architecture}.zip"
ditto -c -k --keepParent --norsrc --noextattr "$stage" "$work_root/$zip_name"
verify_hygiene "$work_root/$zip_name"
# Verify a fresh extraction, including symlink integrity and the ad-hoc signature.
# This is a packaging check, not a Finder/OAuth/export smoke test.
ditto -x -k "$work_root/$zip_name" "$work_root/extracted"
extracted="$work_root/extracted/Mailbox Rescue"
verify_hygiene "$extracted"
[[ -x "$extracted/Mailbox Rescue.app/Contents/MacOS/Mailbox Rescue" ]] || fail "ZIP lost executable permissions."
codesign --verify --deep --strict "$extracted/Mailbox Rescue.app"

rm -rf "$repo_root/dist/macos/Mailbox Rescue"
mv "$stage" "$repo_root/dist/macos/Mailbox Rescue"
artifact="$repo_root/dist/macos/releases/$zip_name"
mv -f "$work_root/$zip_name" "$artifact"
[[ -s "$artifact" ]] || fail "Release ZIP is missing or empty."
printf 'Release ZIP: %s\nArchitecture: %s\nSize (bytes): %s\nSource Git SHA: %s\n' \
    "$artifact" "$architecture" "$(stat -f%z "$artifact")" "$source_sha"
echo 'REAL MACOS PACKAGED SMOKE TEST: NOT PERFORMED by this build script.'
