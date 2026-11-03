"""Small PKI for gateway mTLS.

Two separate CAs on purpose:
* device CA  -> signs each client gateway's server certificate (SAN = <device_id>.devices.raj)
* bridge CA  -> signs the central MCP bridge's client certificate

Gateways only accept client certificates from the bridge CA, so a device certificate can never
be used to call another client's gateway. The bridge verifies the gateway certificate against the
device CA *and* the expected device hostname, so a mis-routed tunnel port fails the handshake.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def device_hostname(device_id: str) -> str:
    return f"{device_id}.devices.raj"


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_pem(k) -> bytes:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())


def cert_pem(c: x509.Certificate) -> bytes:
    return c.public_bytes(serialization.Encoding.PEM)


def fingerprint(pem: bytes) -> str:
    c = x509.load_pem_x509_certificate(pem)
    return hashlib.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest()


def make_ca(cn: str, days: int = 3650):
    k = new_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
         .not_valid_after(now + dt.timedelta(days=days))
         .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
         .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                      content_commitment=False, key_encipherment=False, data_encipherment=False,
                                      key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
         .sign(k, hashes.SHA256()))
    return k, c


def make_csr(key, cn: str) -> bytes:
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
           .sign(key, hashes.SHA256()))
    return csr.public_bytes(serialization.Encoding.PEM)


def sign_csr(ca_key, ca_cert: x509.Certificate, csr_pem: bytes, *, cn: str, server: bool,
             days: int = 825) -> bytes:
    """Issue a cert for the CSR's public key. The subject/SAN come from ``cn`` (server-chosen),
    never from the CSR, so a client cannot request someone else's identity."""
    csr = x509.load_pem_x509_csr(csr_pem)
    if not csr.is_signature_valid:
        raise ValueError("CSR signature invalid")
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
         .issuer_name(ca_cert.subject).public_key(csr.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
         .not_valid_after(now + dt.timedelta(days=days))
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), critical=False)
         .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                      data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                      crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server
                                               else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False))
    if server:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
    return cert_pem(b.sign(ca_key, hashes.SHA256()))


class ServerPKI:
    """Loads or creates the server's CAs, bridge client cert and job-signing key in ``root``."""

    def __init__(self, root: Path):
        from .contracts import generate_signing_key, load_private_key, private_key_pem, public_key_pem
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root
        if not (p / "device_ca.crt").exists():
            for name in ("device_ca", "bridge_ca"):
                k, c = make_ca(f"RAJ {name.replace('_', ' ')}")
                (p / f"{name}.key").write_bytes(key_pem(k))
                (p / f"{name}.crt").write_bytes(cert_pem(c))
            bk = new_key()
            bridge_ca_key = serialization.load_pem_private_key((p / "bridge_ca.key").read_bytes(), None)
            bridge_ca = x509.load_pem_x509_certificate((p / "bridge_ca.crt").read_bytes())
            (p / "bridge.key").write_bytes(key_pem(bk))
            (p / "bridge.crt").write_bytes(sign_csr(bridge_ca_key, bridge_ca, make_csr(bk, "raj-bridge"),
                                                    cn="raj-bridge", server=False))
            (p / "signing.key").write_bytes(private_key_pem(generate_signing_key()))
            for f in p.iterdir():
                if f.suffix == ".key":
                    f.chmod(0o600)
        self.signing_key = load_private_key((p / "signing.key").read_bytes())
        self.signing_pub_pem = public_key_pem(self.signing_key).decode()
        self.device_ca_key = serialization.load_pem_private_key((p / "device_ca.key").read_bytes(), None)
        self.device_ca = x509.load_pem_x509_certificate((p / "device_ca.crt").read_bytes())
        self.device_ca_pem = (p / "device_ca.crt").read_text()
        self.bridge_ca_pem = (p / "bridge_ca.crt").read_text()
        self.bridge_cert_path = str(p / "bridge.crt")
        self.bridge_key_path = str(p / "bridge.key")
        self.device_ca_path = str(p / "device_ca.crt")

    def issue_device_cert(self, device_id: str, csr_pem: bytes) -> bytes:
        return sign_csr(self.device_ca_key, self.device_ca, csr_pem, cn=device_hostname(device_id), server=True)


