#!/bin/sh
# Root-owned forced command. Never eval SSH_ORIGINAL_COMMAND.
set -eu
case "${SSH_ORIGINAL_COMMAND-}" in
  status) exec /usr/bin/sudo -n /usr/local/sbin/tc-maintenance status ;;
  network-check) exec /usr/bin/sudo -n /usr/local/sbin/tc-maintenance network-check ;;
  tunnel-reconnect) exec /usr/bin/sudo -n /usr/local/sbin/tc-maintenance tunnel-reconnect ;;
  *) printf '%s\n' 'Allowed commands: status, network-check, tunnel-reconnect' >&2; exit 1 ;;
esac
