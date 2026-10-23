"""Backend credentials: typed, declared, supplied by a provider.

A consumer implements a ``CredentialProvider`` and returns ``CredentialSet``
values from it. Configuration never holds a secret: it only says which kinds
a backend needs, and the provider supplies them at run time.
"""

from __future__ import annotations

from intercede.auth.models import (
    BearerToken,
    Credential,
    CredentialRequirements,
    CredentialSet,
    SSHPassword,
    SSHPrivateKey,
    X509Proxy,
)
from intercede.auth.protocols import CredentialProvider
from intercede.auth.providers import StaticCredentials

__all__ = [
    "BearerToken",
    "Credential",
    "CredentialProvider",
    "CredentialRequirements",
    "CredentialSet",
    "SSHPassword",
    "SSHPrivateKey",
    "StaticCredentials",
    "X509Proxy",
]
