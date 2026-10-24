from __future__ import annotations

from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

root = Path(__file__).resolve().parents[1] / ".local/tls"
root.mkdir(parents=True, exist_ok=True)
key_file, cert_file = root / "localhost.key", root / "localhost.crt"
if key_file.exists() or cert_file.exists():
    if not key_file.exists() or not cert_file.exists():
        raise SystemExit("Incomplete TLS configuration; existing files retained")
    print("Existing local TLS files retained")
else:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Render Control local development")])
    now = datetime.now(UTC)
    certificate = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=30)).add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]), critical=False).sign(key, hashes.SHA256())
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    print("Created project-local development TLS files; system trust store unchanged")
