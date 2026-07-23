"""Credential-reference resolution for the DeepSee connector.

Credentials are always referenced indirectly (e.g. ``env:CQL_DEEPSEE_TOKEN``)
and resolved at call time — secret values are never stored on connector
objects, never logged, and never appear in source (DEEPSEE_CONNECTOR.md
section 7, DECISIONS.md CS-10).
"""
from __future__ import annotations

import os

#: Default credential reference used by the mock deployment wiring.
DEFAULT_CREDENTIAL_REFERENCE = "env:CQL_DEEPSEE_TOKEN"

_ENV_SCHEME = "env:"


class CredentialResolutionError(Exception):
    """Raised when a credential reference cannot be resolved.

    The message names the reference, never the secret value.
    """


def resolve_credential(reference: str) -> str:
    """Resolve a credential *reference* to its secret value.

    Supported schemes:

    - ``env:<VAR>`` — read the value from environment variable ``<VAR>``.

    Raises:
        CredentialResolutionError: if the reference is malformed, uses an
            unsupported scheme, or the referenced source holds no value.
    """
    if not isinstance(reference, str) or not reference.strip():
        raise CredentialResolutionError(
            "Credential reference must be a non-empty string such as "
            f"'{DEFAULT_CREDENTIAL_REFERENCE}'."
        )
    if not reference.startswith(_ENV_SCHEME):
        raise CredentialResolutionError(
            f"Unsupported credential reference scheme in {reference!r}; "
            "only 'env:<VAR>' references are supported."
        )
    variable = reference[len(_ENV_SCHEME):].strip()
    if not variable:
        raise CredentialResolutionError(
            f"Credential reference {reference!r} names no environment "
            "variable; expected the form 'env:<VAR>'."
        )
    value = os.environ.get(variable)
    if value is None or value == "":
        raise CredentialResolutionError(
            f"Credential reference {reference!r} could not be resolved: "
            f"environment variable {variable!r} is not set. Set it to the "
            "DeepSee API token before starting the connector."
        )
    return value
