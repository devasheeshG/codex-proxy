# Path: app/utils/crypto.py
# Description: Symmetric (Fernet) encryption for OAuth tokens at rest.

from cryptography.fernet import Fernet

from app.config import get_settings

# Get the settings
settings = get_settings()


def _fernet() -> Fernet:
    if not settings.FERNET_KEY:
        raise RuntimeError(
            'FERNET_KEY is not set. Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.FERNET_KEY.encode())


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string, returning a URL-safe token string."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a value produced by `encrypt`."""
    return _fernet().decrypt(ciphertext.encode()).decode()
