#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
# Internal CI-image construction helper, not an end-user or KWin installer.
set -euo pipefail

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ $# == 2 && ( $1 == --check || $1 == --install ) ]] ||
    fail 'usage: install-locked.sh --check|--install PACKAGES.lock'
mode=$1
file=$2
[[ -f $file && ! -L $file ]] || fail 'lock must be a regular, non-symlink file'
[[ $(stat -c %s "$file") -le 262144 ]] || fail 'lock is too large'

pattern='^([[:alnum:]][[:alnum:]_.+-]*)-([0-9]+):([[:alnum:]_.+~^]+)-([[:alnum:]_.+~^]+)\.(x86_64|noarch)$'
export LC_ALL=C
packages=()
declare -A seen=()
while IFS= read -r line || [[ -n $line ]]; do
    [[ -z $line || $line == \#* ]] && continue
    [[ $line =~ $pattern ]] || fail "invalid package pin: $line"
    key=${BASH_REMATCH[1]}.${BASH_REMATCH[5]}
    [[ -z ${seen[$key]-} ]] || fail "duplicate package/architecture: $key"
    seen[$key]=1
    packages+=("$line")
done < "$file"
[[ ${#packages[@]} -gt 0 && ${#packages[@]} -le 2000 ]] || fail 'empty or oversized package list'
expected=$(printf '%s\n' "${packages[@]}")
[[ $expected == "$(printf '%s\n' "${packages[@]}" | sort)" ]] || fail 'package pins must be sorted'

if [[ $mode == --check ]]; then
    printf '%s package pins validated\n' "${#packages[@]}"
    exit 0
fi

# This protects against accidental direct invocation. The actual isolation boundary
# is the reviewed Containerfile and its disposable build container, not this marker.
[[ ${FUSION_CI_IMAGE_BUILD:-} == 1 && $(id -u) == 0 ]] ||
    fail 'installation requires the explicit root container image-build guard'
dnf -y --setopt=install_weak_deps=False --setopt=pkg_gpgcheck=1 \
    '--setopt=*.pkg_gpgcheck=1' --setopt=localpkg_gpgcheck=1 install -- "${packages[@]}"
dnf clean all
actual=$(rpm -qa --qf '%{NAME}\t%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' |
    awk -F '\t' '$1 != "gpg-pubkey" {print $2}' | sort)
if [[ $actual != "$expected" ]]; then
    printf 'Actual package inventory differs from the lock:\n%s\n' "$actual" >&2
    exit 1
fi
mkdir -p /usr/share/plasma-fusion-kwin-builder
printf '%s\n' "$actual" > /usr/share/plasma-fusion-kwin-builder/packages.txt
sha256sum /usr/share/plasma-fusion-kwin-builder/packages.txt > \
    /usr/share/plasma-fusion-kwin-builder/packages.sha256
rpm -qa --qf '%{NAME}\t%{SOURCERPM}\t%{LICENSE}\n' | sort > \
    /usr/share/plasma-fusion-kwin-builder/package-sources.tsv
