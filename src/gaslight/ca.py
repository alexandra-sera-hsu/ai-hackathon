"""Certificate authority for MITM interception.

Generates a CA once and mints per-host leaf certificates on demand, caching
them in memory so repeated connections to the same host are cheap.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CA_NAME = "Gaslight MITM CA"


class CertAuthority:
    """Mints leaf certs signed by a locally-generated CA."""

    def __init__(self, store: Path):
        self.store = store
        self.store.mkdir(parents=True, exist_ok=True)
        self.ca_cert_path = store / "ca.pem"
        self.ca_key_path = store / "ca.key"
        self._lock = threading.Lock()
        self._leaves: dict[str, tuple[bytes, bytes]] = {}
        self._load_or_create()

    def _load_or_create(self) -> None:
        if self.ca_cert_path.exists() and self.ca_key_path.exists():
            self.ca_key = serialization.load_pem_private_key(
                self.ca_key_path.read_bytes(), password=None
            )
            self.ca_cert = x509.load_pem_x509_certificate(self.ca_cert_path.read_bytes())
            return

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            # Without keyCertSign some clients reject the CA outright.
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    key_encipherment=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )
        self.ca_key, self.ca_cert = key, cert
        self.ca_key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        self.ca_key_path.chmod(0o600)
        self.ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    @property
    def ca_pem(self) -> bytes:
        return self.ca_cert.public_bytes(serialization.Encoding.PEM)

    def leaf_for(self, host: str) -> tuple[bytes, bytes]:
        """Return (cert_chain_pem, key_pem) for host, minting on first use."""
        with self._lock:
            if host in self._leaves:
                return self._leaves[host]

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            try:
                san = x509.IPAddress(ipaddress.ip_address(host))
            except ValueError:
                san = x509.DNSName(host)
            now = dt.datetime.now(dt.timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host[:64])]))
                .issuer_name(self.ca_cert.subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=5))
                .not_valid_after(now + dt.timedelta(days=29))
                .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(
                    x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.3.6.1.5.5.7.3.1")]),
                    critical=False,
                )
                .sign(self.ca_key, hashes.SHA256())
            )
            chain = cert.public_bytes(serialization.Encoding.PEM) + self.ca_pem
            key_pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
            self._leaves[host] = (chain, key_pem)
            return chain, key_pem
