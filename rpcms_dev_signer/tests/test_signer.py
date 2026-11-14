import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@tagged("post_install", "-at_install")
class TestDevSigner(TransactionCase):
    def setUp(self):
        super().setUp()
        self.provider = self.env.ref("rpcms_dev_signer.provider_dev_signer")

    def _verify(self, token, payload: bytes):
        head, middle, sig = token.split(".")
        pub = serialization.load_pem_public_key(self.provider.rpcms_dev_public_key_pem().encode())
        signed = f"{head}.{base64.urlsafe_b64encode(payload).decode().rstrip('=')}".encode()
        pub.verify(_unb64(sig), signed, padding.PKCS1v15(), hashes.SHA256())  # raises if invalid
        return middle

    def test_detached_signature_verifies_and_has_empty_payload_part(self):
        token = self.provider.jwt_sign('{"a":1}', include_payload=False)
        self.assertEqual(self._verify(token, b'{"a":1}'), "")

    def test_attached_signature_carries_payload(self):
        token = self.provider.jwt_sign(b"hello", include_payload=True)
        self.assertEqual(_unb64(self._verify(token, b"hello")), b"hello")

    def test_tampered_payload_fails(self):
        token = self.provider.jwt_sign("one", include_payload=False)
        with self.assertRaises(Exception):
            self._verify(token, b"two")

    def test_key_is_created_once_and_reused(self):
        first = self.provider.rpcms_dev_public_key_pem()
        self.env["g2p.encryption.provider"].invalidate_model()
        self.assertEqual(first, self.provider.rpcms_dev_public_key_pem())
