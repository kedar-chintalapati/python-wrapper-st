#!/usr/bin/env bash
# Builds a 64-bit Windows wheel without a Windows machine: cross-compiles lost.exe with MinGW inside
# a Fedora container, gathers the DLLs it needs, and packages them as a win_amd64 wheel.
# Usage, from the repository root (needs Docker): bash packaging/build-windows-wheel.sh
# Output: wheelhouse/lost_python_api-<version>-py3-none-win_amd64.whl
set -euo pipefail

FEDORA_IMAGE="fedora:41"
SYSROOT=/usr/x86_64-w64-mingw32/sys-root/mingw

if [ "${1:-}" != "--in-container" ]; then
    root="$(cd "$(dirname "$0")/.." && pwd)"
    mkdir -p "$root/wheelhouse"
    exec docker run --rm \
        -v "$root:/src:ro" -v "$root/wheelhouse:/out" \
        -e LOST_API_BUILD_JOBS="${LOST_API_BUILD_JOBS:-4}" -e HOST_UID="$(id -u)" \
        "$FEDORA_IMAGE" bash /src/packaging/build-windows-wheel.sh --in-container
fi

# --- inside the container -------------------------------------------------------------------

dnf install -y -q mingw64-gcc-c++ mingw64-cairo make groff vim-common eigen3-devel \
    python3-pip binutils > /dev/null

# Work on a copy so the mounted checkout stays untouched.
build=/tmp/build
mkdir -p "$build/documentation"
cp -r /src/src /src/vendor /src/python "$build/"
cp /src/Makefile /src/bright-star-catalog.tsv /src/setup.py /src/pyproject.toml \
    /src/MANIFEST.in /src/README.md /src/COMMANDS.md "$build/"
cp /src/documentation/*.man "$build/documentation/"
find "$build" \( -name '*.o' -o -name '*.d' -o -name '__pycache__' \) -prune -exec rm -rf {} +
# Eigen is header-only, so the Linux copy works for Windows too (LOST includes <eigen3/Eigen/...>).
cp -r /usr/include/eigen3 "$build/vendor/eigen3"

cd "$build"
CXXFLAGS="-D_USE_MATH_DEFINES" LDFLAGS="-static-libgcc -static-libstdc++" \
    make release CXX=x86_64-w64-mingw32-g++ -j"$LOST_API_BUILD_JOBS"

bundle=/tmp/bundle
mkdir -p "$bundle"
cp "$build/lost.exe" "$build/bright-star-catalog.tsv" "$bundle/"

# Copy every DLL lost.exe needs, recursively, from the MinGW sysroot. DLLs not found there are
# part of Windows itself (KERNEL32, msvcrt, ...).
declare -A seen=()
queue=("$bundle/lost.exe")
while [ "${#queue[@]}" -gt 0 ]; do
    file="${queue[0]}"
    queue=("${queue[@]:1}")
    while read -r dll; do
        key="${dll,,}"
        [ -n "${seen[$key]:-}" ] && continue
        seen[$key]=1
        found="$(find "$SYSROOT/bin" -maxdepth 1 -iname "$dll" | head -n 1)"
        if [ -n "$found" ]; then
            cp "$found" "$bundle/"
            queue+=("$bundle/$(basename "$found")")
        fi
    done < <(x86_64-w64-mingw32-objdump -p "$file" | sed -n 's/^\s*DLL Name: //p')
done
x86_64-w64-mingw32-strip --strip-unneeded "$bundle"/*.dll "$bundle"/lost.exe
echo "bundled: $(cd "$bundle" && ls | tr '\n' ' ')"

LOST_API_PREBUILT_BUNDLE="$bundle" LOST_API_PLAT_NAME=win_amd64 \
    python3 -m pip wheel "$build" --no-deps -w /out
chown "$HOST_UID:$HOST_UID" /out/*win_amd64.whl
