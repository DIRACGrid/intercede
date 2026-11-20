"""Backend credentials: typed, declared, supplied by a provider.

A consumer implements a ``CredentialProvider``, which returns the one credential
a backend asks for. Configuration never holds a secret: it only says how a backend
logs in, and the provider supplies the credential at run time.
"""

from __future__ import annotations

from intercede.auth.models import (
    BearerToken,
    Credential,
    CredentialRequirements,
    SSHPassword,
    SSHPrivateKey,
)
from intercede.auth.protocols import CredentialProvider
from intercede.auth.providers import StaticCredentials

__all__ = [
    "BearerToken",
    "Credential",
    "CredentialProvider",
    "CredentialRequirements",
    "SSHPassword",
    "SSHPrivateKey",
    "StaticCredentials",
]
