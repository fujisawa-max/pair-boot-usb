#!/bin/bash
set -euo pipefail
umask 077

usage() {
    echo "使用法: $0 [--tokens 件数] 出力先 共通DNS名 ノード名 [ノード名 ...]" >&2
    exit 2
}

token_count=1
if [[ ${1:-} == --tokens ]]; then
    (( $# >= 2 )) || usage
    token_count=$2
    shift 2
fi
if (( $# < 3 )) || [[ ! $token_count =~ ^([1-9][0-9]{0,2}|1000)$ ]]; then
    usage
fi

output=$1
dns_name=$2
shift 2

if [[ ! $dns_name =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]] ||
   (( ${#dns_name} > 253 )); then
    echo '共通DNS名には小文字の完全修飾DNS名を指定してください。' >&2
    exit 2
fi

declare -A seen=()
for node in "$@"; do
    if [[ ! $node =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ || ${seen[$node]+set} ]]; then
        echo "無効または重複したノード名: $node" >&2
        exit 2
    fi
    seen[$node]=1
done

mkdir -- "$output"
cd -- "$output"
mkdir -- nodes

# CA鍵の生成時と各ノードへの署名時にパスフレーズを対話入力する。
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 \
    -aes-256-cbc -out ca-signing.key.pem

openssl req -new -x509 -sha256 -days 365 \
    -key ca-signing.key.pem -out ca.pem \
    -subj '/CN=PAIR Lab Test CA' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign'

# 原文は学生への配布用ファイルにだけ保存する。
python3 - "$token_count" <<'PY'
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time

count = int(sys.argv[1])
expires_at = int(time.time()) + 7 * 24 * 60 * 60
if count > 1:
    Path('students').mkdir()

entries = []
for index in range(1, count + 1):
    token = secrets.token_urlsafe(32)
    filename = 'student-token.txt' if count == 1 else f'students/student-{index:03d}.txt'
    with Path(filename).open('x', encoding='ascii') as stream:
        stream.write(token + '\n')
    entries.append({
        'sha256': hashlib.sha256(token.encode('ascii')).hexdigest(),
        'expires_at': expires_at,
    })

metadata = entries[0] if count == 1 else {'tokens': entries}
with Path('token.json').open('x', encoding='ascii') as stream:
    json.dump(metadata, stream, indent=2)
    stream.write('\n')
PY

for node in "$@"; do
    dir="nodes/$node"
    mkdir -- "$dir"

    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
        -out "$dir/key.pem"

    openssl req -new -sha256 -key "$dir/key.pem" \
        -out "$node.csr.pem" -subj "/CN=$dns_name"

    serial=$(openssl rand -hex 16)
    openssl x509 -req -sha256 -days 30 \
        -in "$node.csr.pem" -CA ca.pem -CAkey ca-signing.key.pem \
        -set_serial "0x$serial" \
        -out "$dir/fullchain.pem" \
        -extfile <(printf '%s\n' \
            'basicConstraints=critical,CA:FALSE' \
            'keyUsage=critical,digitalSignature,keyEncipherment' \
            'extendedKeyUsage=serverAuth' \
            "subjectAltName=DNS:$dns_name")

    openssl verify -CAfile ca.pem -purpose sslserver \
        -verify_hostname "$dns_name" "$dir/fullchain.pem"

    install -m 0600 -- ca.pem "$dir/ca.pem"
    install -m 0600 -- token.json "$dir/token.json"
done
