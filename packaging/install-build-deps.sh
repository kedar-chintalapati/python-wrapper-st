#!/usr/bin/env bash
# Installs what compiling LOST needs (a C++ compiler, make, cairo, Eigen, groff, xxd) on the
# build images cibuildwheel uses: manylinux (dnf), Debian/Ubuntu (apt) and macOS (Homebrew).
set -euo pipefail

if command -v dnf > /dev/null; then
    dnf install -y epel-release
    dnf config-manager --set-enabled powertools 2> /dev/null \
        || dnf config-manager --set-enabled crb 2> /dev/null || true
    dnf install -y cairo-devel eigen3-devel groff vim-common make gcc-c++
elif command -v apt-get > /dev/null; then
    apt-get update
    apt-get install -y g++ make groff xxd libcairo2-dev libeigen3-dev
elif command -v brew > /dev/null; then
    brew install cairo eigen groff
else
    echo "No supported package manager found; install cairo, Eigen, groff and xxd manually." >&2
    exit 1
fi
