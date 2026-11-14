"""DEVELOPMENT ONLY. A "signer" for the encryption-provider API that needs no key service.

The base provider (g2p_encryption) cannot sign anything and the real one (g2p_encryption_keymanager) needs a MOSIP
Keymanager service. This adds a provider type that signs with an RSA key generated on first use and kept, unprotected,
in the database (ir.config_parameter). Anyone with database access can read that key. Never install this where real
money or real people's data are involved: use the Keymanager provider there.

Output follows the shape this repo's tests assume for the Keymanager (detached JWS "header..signature" when the
payload is not included). That shape was inferred, not checked against a running Keymanager.
"""
import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from odoo import fields, models

PARAM = "rpcms_dev_signer.private_key_pem"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class G2PEncryptionProviderDevSigner(models.Model):
    _inherit = "g2p.encryption.provider"

    type = fields.Selection(
        selection_add=[("rpcms_dev_rsa", "RPCMS dev signer (local key, NOT SECURE)")],
        ondelete={"rpcms_dev_rsa": "cascade"},
    )

    def _rpcms_dev_private_key(self):
        params = self.env["ir.config_parameter"].sudo()
        pem = params.get_param(PARAM)
        if not pem:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
            params.set_param(PARAM, pem)
        return serialization.load_pem_private_key(pem.encode(), password=None)

    def rpcms_dev_public_key_pem(self) -> str:
        """The public half, for a mock Bridge that verifies signatures."""
        return self._rpcms_dev_private_key().public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def jwt_sign_rpcms_dev_rsa(self, data, include_payload=True, include_certificate=False,
                               include_cert_hash=False, **kwargs) -> str:
        self.ensure_one()
        if isinstance(data, dict):
            data = json.dumps(data).encode()
        elif isinstance(data, str):
            data = data.encode()
        head = _b64(json.dumps({"alg": "RS256"}).encode())
        sig = _b64(self._rpcms_dev_private_key().sign(f"{head}.{_b64(data)}".encode(), padding.PKCS1v15(),
                                                       hashes.SHA256()))
        return f"{head}.{_b64(data)}.{sig}" if include_payload else f"{head}..{sig}"
