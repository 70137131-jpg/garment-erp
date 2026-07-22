"""One password policy shared by API schemas and bootstrap provisioning."""

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 256

# A local deny-list catches the most routinely attacked credentials without
# sending candidate passwords to any third party. Deployments can add an
# offline breached-password corpus at the identity-provider layer.
_COMMON_PASSWORDS = {
    "123456789012345",
    "admin1234567890",
    "administrator123",
    "changeme1234567",
    "company123456789",
    "garment123456789",
    "letmein123456789",
    "password1234567",
    "qwerty123456789",
    "welcome12345678",
}


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters"
        )
    if password.casefold() in _COMMON_PASSWORDS:
        raise ValueError("Choose a password that is not commonly used")
    return password
