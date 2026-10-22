#!/usr/bin/env bash
# Read-only cloud-host survey. No installs, secrets, service changes or probes
# of model servers. Run from the cloud host's Bash, not from the GPU host.
set -eu
export LC_ALL=C

printf '\n[OS and architecture]\n'
if [ -r /etc/os-release ]; then
    awk -F= '$1 == "PRETTY_NAME" {print $0}' /etc/os-release
fi
uname -m

printf '\n[Runtime versions]\n'
for program in python3 uv nginx systemctl; do
    if command -v "$program" >/dev/null 2>&1; then
        case "$program" in
            nginx) nginx -v 2>&1 ;;
            systemctl) systemctl --version | head -n 1 ;;
            *) "$program" --version ;;
        esac
    else
        printf '%s: not found in PATH\n' "$program"
    fi
done

if command -v dpkg-query >/dev/null 2>&1; then
    printf '\n[Ubuntu package versions; security fixes may be backported]\n'
    dpkg-query -W -f='${binary:Package}\t${Version}\n' nginx nginx-common openssl python3.12 2>/dev/null || true
fi

printf '\n[Memory and disk capacity]\n'
free -h
df -h -- / /opt /var/lib
printf '\n[Listening TCP sockets; no process arguments]\n'
if command -v ss >/dev/null 2>&1; then
    ss -lnt
    if ss -H -lnt 'sport = :18765' | awk 'END {exit NR == 0 ? 1 : 0}'; then
        printf 'Candidate application port 18765: IN USE; choose another port.\n'
    else
        printf 'Candidate application port 18765: no listener observed.\n'
    fi
else
    printf 'ss unavailable; port availability is unverified.\n'
fi

printf '\n[New service namespace and Nginx activity]\n'
if getent passwd token-controller >/dev/null 2>&1; then
    printf 'Account token-controller exists; inspect ownership before reuse.\n'
else
    printf 'Account token-controller does not exist.\n'
fi
for candidate in /opt/token-controller /var/lib/token-controller /etc/token-controller /etc/systemd/system/token-controller.service /etc/nginx/sites-available/token-controller /etc/nginx/sites-enabled/token-controller; do
    if [ -e "$candidate" ] || [ -L "$candidate" ]; then
        printf 'EXISTS: %s (do not overwrite blindly)\n' "$candidate"
    else
        printf 'UNUSED: %s\n' "$candidate"
    fi
done
if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active nginx || true
    systemctl show nginx --property=ActiveState,SubState,MemoryCurrent,CPUUsageNSec --no-pager || true
fi
printf '\nRead-only survey complete. No site, service, firewall or model was changed.\n'
