#!/usr/bin/env bash
set -Eeuo pipefail

root=$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
report="$root/mac-test-report.txt"
: > "$report"
exec > >(tee -a "$report") 2>&1

steps=('Prerequisites' 'Homebrew packages' 'Build environment' 'Build wheel'
       'Repair wheel' 'Verify bundling' 'Install wheel' 'Self-test'
       'Example' 'Unit tests')
step=0
wheel=''
tmp=''

finish() {
    local status=$? i result
    printf '\nSummary\n'
    for i in "${!steps[@]}"; do
        result='SKIPPED'
        if (( i < step )); then
            result='PASS'
        elif (( i == step && status != 0 )); then
            result='FAIL'
        fi
        printf '  %s  %s\n' "$result" "${steps[i]}"
    done
    if [[ -n "$wheel" ]]; then
        printf 'Wheel: %s\nPath: %s\n' "${wheel##*/}" "$wheel"
    else
        printf 'Wheel: unavailable\n'
    fi
    printf 'Send back mac-test-report.txt and the .whl file in wheelhouse/\n'
    if [[ -n "$tmp" ]]; then
        rm -rf -- "$tmp" || printf 'Could not remove temporary folder %s\n' "$tmp" >&2
    fi
}
trap finish EXIT
trap 'if (( BASH_SUBSHELL == 0 )); then printf "FAIL: %s (line %s)\n" "${steps[step]}" "$LINENO" >&2; fi' ERR

fail() {
    printf '%s\n' "$*" >&2
    return 1
}

start_step() {
    printf '\n[%s]\n' "${steps[step]}"
}

date
sw_vers || true
uname -m
python3 --version || true
brew --version | sed -n '1p' || true
clang --version | sed -n '1p' || true

start_step
[[ "$(uname -s)" == Darwin ]] || fail 'Run this script on a Mac with macOS 13 or newer.'
xcode-select -p || fail 'Install Xcode command line tools: xcode-select --install'
command -v brew >/dev/null || fail 'Install Homebrew and follow its PATH setup instructions: https://brew.sh'
if ! command -v python3 >/dev/null ||
   ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    fail 'Install Python 3.10 or newer: brew install python'
fi
[[ -f "$root/setup.py" ]] || fail 'Place this script in packaging/ inside the extracted project.'
arch=$(uname -m)
macos_version=$(sw_vers -productVersion)
step=$((step + 1))

start_step
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1
missing=()
for formula in cairo eigen groff; do
    if [[ -z "$(brew list --versions "$formula" 2> /dev/null || true)" ]]; then
        missing+=("$formula")
    fi
done
if [[ -n "${missing[0]:-}" ]]; then
    brew install "${missing[@]}"
fi
brew_prefix=$(brew --prefix)
step=$((step + 1))

start_step
build="$root/.mac-build"
dist="$build/dist"
wheelhouse="$root/wheelhouse"
build_python="$build/venv/bin/python"
mkdir -p "$dist" "$wheelhouse"
unset PYTHONPATH PYTHONHOME
python3 -m venv --clear "$build/venv"
"$build_python" -m pip install delocate
tmp=$(mktemp -d /tmp/lost-mac-test.XXXXXXXX)
tmp=$(CDPATH= cd -P -- "$tmp" && pwd)
case "$tmp/" in
    "$root/"*) fail 'The temporary test folder must be outside the project.' ;;
esac
step=$((step + 1))

start_step
shopt -s nullglob
for old_wheel in "$dist/"*.whl; do
    rm -- "$old_wheel"
done
CXXFLAGS="-I$brew_prefix/include" LDFLAGS="-L$brew_prefix/lib" \
    MACOSX_DEPLOYMENT_TARGET="${macos_version%%.*}.0" LOST_API_BUILD_JOBS=4 \
    "$build_python" -m pip wheel "$root" --no-deps -w "$dist"
built_wheels=("$dist/"*.whl)
[[ -n "${built_wheels[0]:-}" && -z "${built_wheels[1]:-}" ]] || fail 'Expected exactly one built wheel.'
step=$((step + 1))

start_step
touch "$tmp/before-repair"
"$build/venv/bin/delocate-wheel" --require-archs "$arch" -w "$wheelhouse" -v "${built_wheels[0]}"
find "$wheelhouse" -maxdepth 1 -type f -name '*.whl' -newer "$tmp/before-repair" -print0 > "$tmp/wheels"
repaired_wheels=()
while IFS= read -r -d '' path; do
    repaired_wheels+=("$path")
done < "$tmp/wheels"
[[ -n "${repaired_wheels[0]:-}" && -z "${repaired_wheels[1]:-}" ]] || fail 'Expected exactly one newly repaired wheel in wheelhouse/.'
wheel=${repaired_wheels[0]}
step=$((step + 1))

check_links() {
    local binary=$1
    # otool -L lists a dylib's own install ID first. Read it with -D
    # so the dependency checks can skip that entry for each architecture.
    : > "$tmp/install-ids"
    if [[ "$binary" == *.dylib ]]; then
        otool -arch all -D "$binary" | tee "$tmp/install-ids"
    fi
    otool -arch all -L "$binary" | tee "$tmp/links"
    awk '
        FILENAME == ARGV[1] {
            sub(/^[[:space:]]+/, "")
            if ($0 !~ /:$/ && NF) ids[$0] = 1
            next
        }
        /^[^[:space:]]/ { first = 1; next }
        /^[[:space:]]/ {
            sub(/^[[:space:]]+/, "")
            sub(/ \(compatibility version .*$/, "")
            own_id = first && ($0 in ids)
            first = 0
            if (!own_id && $0 !~ /^(\/usr\/lib\/|\/System\/|@(loader_path|rpath|executable_path)\/)/) {
                printf "Disallowed dependency: %s\n", $0
                bad = 1
            }
        }
        END { exit bad }
    ' "$tmp/install-ids" "$tmp/links"
}

start_step
unzipped="$tmp/unzipped"
unzip -q "$wheel" -d "$unzipped"
lost="$unzipped/lost_api/_bundled/lost"
[[ -f "$lost" && -x "$lost" ]] || fail 'Bundled lost is missing or lacks execute permission.'
check_links "$lost"
find "$unzipped" -name '*.dylib' -print0 > "$tmp/dylibs"
while IFS= read -r -d '' dylib; do
    check_links "$dylib"
done < "$tmp/dylibs"
mkdir "$tmp/run"
cd "$tmp/run"
"$lost" 2>&1 | tee "$tmp/usage"
grep -iq '^[[:space:]]*usage:' "$tmp/usage" || fail 'Bundled lost did not print a usage line.'
step=$((step + 1))

start_step
python3 -m venv --clear "$build/test-venv"
test_python="$build/test-venv/bin/python"
"$test_python" -m pip install "${wheel}[images]"
step=$((step + 1))

start_step
"$test_python" -m lost_api 2>&1 | tee "$tmp/self-test"
grep -Fq '(bundled)' "$tmp/self-test" || fail 'The self-test did not report using bundled lost.'
step=$((step + 1))

start_step
cp "$root/python/examples/example.py" .
"$test_python" example.py
step=$((step + 1))

start_step
cd "$root"
"$test_python" -m unittest discover -s python/tests -t python/tests
step=$((step + 1))
