#!/bin/sh
set -eu

# Per-container lab TLS. The 2024 clients already use an unverified SSL context.
keytool -genkeypair -alias pilab -keyalg RSA -keysize 2048 \
    -storetype PKCS12 -keystore /tmp/pilab.p12 -storepass pilab-lab \
    -dname 'CN=pilab-lab' -validity 1 -noprompt
exec java -jar /app/app.jar \
    --server.port=8080 \
    --server.ssl.key-store-type=PKCS12 \
    --server.ssl.key-store=file:/tmp/pilab.p12 \
    --server.ssl.key-store-password=pilab-lab
