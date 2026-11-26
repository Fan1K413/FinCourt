"""Issue TLS credentials scoped to the private Northbound gateway."""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

GATEWAY = "fincourt-nexent-gateway"


def write_pem(path: Path, content: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reusable(cert_path: Path, key_path: Path, ca_path: Path, now: datetime) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert.verify_directly_issued_by(ca)
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return (
            min(cert.not_valid_after_utc, ca.not_valid_after_utc) > now + timedelta(days=21)
            and GATEWAY in names.get_values_for_type(x509.DNSName)
            and key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            == cert.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    except (OSError, ValueError, TypeError, x509.ExtensionNotFound):
        return False


def bootstrap(gateway: Path, trust: Path) -> None:
    gateway.mkdir(parents=True, exist_ok=True)
    trust.mkdir(parents=True, exist_ok=True)
    cert_path, key_path, ca_path = gateway / "server.pem", gateway / "server.key", trust / "ca.pem"
    now = datetime.now(timezone.utc)
    if reusable(cert_path, key_path, ca_path, now):
        return

    ca_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FinCourt Stack CA")])
    service_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, GATEWAY)])
    valid_from = now - timedelta(minutes=5)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from)
        .not_valid_after(now + timedelta(days=730))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(service_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from)
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(GATEWAY)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    write_pem(
        key_path,
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        0o600,
    )
    write_pem(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    write_pem(ca_path, ca.public_bytes(serialization.Encoding.PEM), 0o644)


if __name__ == "__main__":
    bootstrap(Path(sys.argv[1]), Path(sys.argv[2]))
