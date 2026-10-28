"""Bounded password hashing using an OWASP-listed scrypt profile."""
import base64
import hashlib
import hmac
import re
import secrets
import threading
import unicodedata

from .store import Fault

N, R, P = 16384, 8, 5
HASH_SLOTS = threading.BoundedSemaphore(2)
COMMON_PASSWORDS = {'passwordpassword', 'password123456789', '123456789012345',
                    '1234567890123456', 'qwertyuiopasdfgh', 'adminadminadmin',
                    'letmeinletmeinletmein', 'iloveyouiloveyou', 'changemechangeme'}


def normalize_username(value):
    value = unicodedata.normalize('NFKC', value).strip().casefold()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,39}', value):
        raise Fault('invalid_username', 400)
    return value


def validate_password(value, username=''):
    value = unicodedata.normalize('NFC', value)
    if not 6 <= len(value) <= 128 or len(value.encode('utf-8')) > 512:
        raise Fault('password_length', 400)
    if (value.casefold() in COMMON_PASSWORDS or len(set(value)) == 1
            or value.casefold() == username.casefold()):
        raise Fault('password_too_common', 400)
    return value


def _derive(value, salt):
    if not HASH_SLOTS.acquire(blocking=False):
        raise Fault('authentication_busy', 429)
    try:
        return hashlib.scrypt(value.encode('utf-8'), salt=salt, n=N, r=R, p=P, dklen=32, maxmem=64*1024*1024)
    finally:
        HASH_SLOTS.release()


def hash_password(value, username=''):
    value = validate_password(value, username)
    salt = secrets.token_bytes(16)
    result = _derive(value, salt)
    return 'scrypt$16384$8$5$'+base64.b64encode(salt).decode()+'$'+base64.b64encode(result).decode()


def verify_password(value, encoded=None):
    value = unicodedata.normalize('NFC', value)
    if len(value) > 128 or len(value.encode('utf-8')) > 512:
        return False
    # Unknown usernames perform the same bounded KDF work as known usernames.
    salt, expected = bytes(16), bytes(32)
    valid_format = False
    if encoded:
        try:
            algorithm, n, r, p, salt_text, result_text = encoded.split('$')
            if (algorithm, n, r, p) == ('scrypt', '16384', '8', '5'):
                candidate_salt = base64.b64decode(salt_text, validate=True)
                candidate_result = base64.b64decode(result_text, validate=True)
                if len(candidate_salt) == 16 and len(candidate_result) == 32:
                    salt, expected, valid_format = candidate_salt, candidate_result, True
        except (ValueError, TypeError):
            pass
    actual = _derive(value, salt)
    return hmac.compare_digest(actual, expected) and valid_format
