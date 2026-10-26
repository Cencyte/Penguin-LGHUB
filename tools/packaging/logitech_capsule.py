"""Private Logitech capsule decoding using public vendor keys and libcrypto.

The observed format uses AES-128-GCM's stream without storing a GCM tag.
Integrity therefore comes from the pinned whole-depot SHA-256 and the vendor
header/member SHA-256 values. Do not use this as a general authenticated cipher.
Keys and decoded vendor content must never be included in publication payloads.
"""
import base64
import ctypes as C
import ctypes.util
import hashlib
import json
import re
import urllib.request

from windows_media_acquire import OfficialRedirects, space_check

MAX_KEYS = 1024 * 1024
MAX_MEMBER = 64 * 1024**2


def key_fingerprint(record):
    identity = {k: record[k] for k in ('name', 'version', 'key')}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_keys(data, spec):
    if len(data) > MAX_KEYS:
        raise ValueError('Public key document exceeds limit')
    document = json.loads(data)
    if document['accessGroup'] != spec['access_group']:
        raise ValueError('Unexpected public access group')
    records = {}
    for record in document['keys']:
        name = record['name']
        if name in records:
            raise ValueError('Duplicate public key identifier')
        records[name] = record
    selected = {}
    for name, fingerprint in spec['required_keys'].items():
        record = records.get(name)
        if not record or key_fingerprint(record) != fingerprint or record['version'] != 1:
            raise ValueError(f'Missing or changed pinned public key: {name}')
        key = base64.b64decode(record['key'], validate=True)
        if len(key) != 16:
            raise ValueError('Unexpected capsule key length')
        selected[name] = key
    return selected


def public_keys(work, spec, fetch=False, opener=None):
    path = work / 'vendor-public-access.json'
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('Unexpected public-key cache type')
    if path.exists():
        if path.stat().st_size > MAX_KEYS:
            raise ValueError('Public key document exceeds limit')
        return validate_keys(path.read_bytes(), spec)
    if not fetch:
        raise ValueError('Run fetch first to acquire the pinned public keys from Logitech')
    def official(url):
        if url != spec['url'] or not re.fullmatch(
                r'https://updates\.ghub\.logitechg\.com/pipeline/v2/access/[0-9a-f-]{36}/content\.json', url):
            raise ValueError('Unexpected public-key URL')
        return url
    official(spec['url'])
    opener = opener or urllib.request.build_opener(OfficialRedirects(official))
    with opener.open(spec['url'], timeout=45) as response:
        official(response.url)
        if response.status != 200:
            raise ValueError('Public-key request failed')
        data = response.read(MAX_KEYS + 1)
    keys = validate_keys(data, spec)
    space_check(work, len(data))
    with path.open('xb') as out:
        out.write(data)
    path.chmod(0o600)
    return keys


def decrypt(data, content_sha256, key_id, keys):
    if len(data) > MAX_MEMBER or not re.fullmatch('[0-9a-f]{64}', content_sha256):
        raise ValueError('Invalid capsule size or content digest')
    if key_id not in keys:
        raise ValueError('Capsule requests an unpinned public key')
    iv = hashlib.pbkdf2_hmac('sha512', content_sha256.encode(), key_id.encode(), 1000, 32)
    library = ctypes.util.find_library('crypto')
    if not library:
        raise RuntimeError('OpenSSL libcrypto is required for encrypted resource depots')
    lib = C.CDLL(library)
    signatures = {
        'EVP_CIPHER_CTX_new': (C.c_void_p, []),
        'EVP_CIPHER_CTX_free': (None, [C.c_void_p]),
        'EVP_aes_128_gcm': (C.c_void_p, []),
        'EVP_DecryptInit_ex': (C.c_int, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]),
        'EVP_CIPHER_CTX_ctrl': (C.c_int, [C.c_void_p, C.c_int, C.c_int, C.c_void_p]),
        'EVP_DecryptUpdate': (C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(C.c_int), C.c_void_p, C.c_int]),
    }
    for name, (result, args) in signatures.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = result, args
    context = lib.EVP_CIPHER_CTX_new()
    if not context:
        raise RuntimeError('Cannot allocate cipher context')
    try:
        out, size = C.create_string_buffer(len(data) + 16), C.c_int()
        if not (lib.EVP_DecryptInit_ex(context, lib.EVP_aes_128_gcm(), None, None, None)
                and lib.EVP_CIPHER_CTX_ctrl(context, 9, len(iv), None)
                and lib.EVP_DecryptInit_ex(context, None, None, keys[key_id], iv)
                and lib.EVP_DecryptUpdate(context, out, C.byref(size), data, len(data))):
            raise ValueError('Capsule decryption failed')
        if size.value != len(data):
            raise ValueError('Unexpected decrypted size')
        return out.raw[:size.value]
    finally:
        lib.EVP_CIPHER_CTX_free(context)
