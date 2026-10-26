"""Local interactive account setup. No passwords in arguments, output or files."""
import argparse
import getpass
import os
from pathlib import Path
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway.accounts import WebAccounts
from gateway.store import Fault, Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--user-id', help='An existing active user ID; default is the single active administrator')
    parser.add_argument('--username', help='Login username; password is always entered interactively')
    parser.add_argument('--reset', action='store_true', help='Explicitly replace existing login credentials and revoke web sessions')
    args = parser.parse_args()
    state = args.state.resolve()
    if not state.is_dir() or not (state/'gateway.sqlite').is_file():
        raise SystemExit('Expected existing private state and database; no account was created.')
    if os.name != 'nt' and os.geteuid() != state.stat().st_uid:
        raise SystemExit('Run as the private state owner, for example with sudo -H -u <service-user>; avoid root-owned SQLite journals.')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('Use an interactive terminal so the password cannot be echoed.')
    store = Store(state/'gateway.sqlite', seed_demo=False)
    user_id = args.user_id
    if not user_id:
        admins = store.rows("SELECT id FROM users WHERE role='admin' AND active=1")
        if len(admins) != 1:
            raise SystemExit('Specify --user-id because there is not exactly one active administrator.')
        user_id = admins[0]['id']
    if store.one('SELECT 1 FROM login_accounts WHERE user_id=?', (user_id,)) and not args.reset:
        raise SystemExit('Login already configured; use --reset only for an intentional local recovery.')
    username = args.username or input('登录用户名（3–40位英文、数字、点、横线或下划线）: ')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('设置登录密码（至少15字符，输入不显示）: ')
        confirmation = getpass.getpass('再次输入密码: ')
    if password != confirmation:
        raise SystemExit('Passwords differ; nothing was changed.')
    WebAccounts(store).setup(user_id, username, password, reset=args.reset)
    print('WEB_LOGIN_ACCOUNT_READY: existing user ID, API Keys and usage were retained; password was not printed.')


if __name__ == '__main__':
    try:
        main()
    except Fault as error:
        raise SystemExit('STOP: '+error.code) from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit('Cancelled; no credentials printed.') from None
