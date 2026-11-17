# gcp_auth.py — Google credentials for the Dialogflow clients
#
# Default: Application Default Credentials (ADC), i.e. GOOGLE_APPLICATION_CREDENTIALS
# pointing at a key file, `gcloud auth application-default login`, or the
# attached service account on GCP. The client libraries handle that themselves.
#
# Hosts that cannot mount a key file (Railway, most PaaS) can instead put the
# service-account JSON *content* into GOOGLE_APPLICATION_CREDENTIALS_JSON.
# It takes precedence over ADC when set.

from __future__ import annotations

import json
import os
from typing import Any

CREDENTIALS_JSON_ENV = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


class CredentialsConfigError(RuntimeError):
    """GOOGLE_APPLICATION_CREDENTIALS_JSON is set but unusable."""


def credentials_from_env() -> Any | None:
    """Service-account credentials from GOOGLE_APPLICATION_CREDENTIALS_JSON.

    Returns None when the variable is unset/blank (callers then fall back to
    ADC). Raises CredentialsConfigError when it is set but is not a valid
    service-account key; the message never includes the variable's value.
    """
    raw = os.environ.get(CREDENTIALS_JSON_ENV, "").strip()
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        raise CredentialsConfigError(
            f"{CREDENTIALS_JSON_ENV} is not valid JSON. Paste the whole "
            "service-account key file content."
        ) from None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise CredentialsConfigError(
            f"{CREDENTIALS_JSON_ENV} must contain a service-account key "
            '(a JSON object with "type": "service_account").'
        )

    from google.oauth2 import service_account

    try:
        return service_account.Credentials.from_service_account_info(info, scopes=list(_SCOPES))
    except (ValueError, KeyError):
        raise CredentialsConfigError(
            f"{CREDENTIALS_JSON_ENV} is not a usable service-account key: missing "
            "fields (client_email, private_key, token_uri, ...) or a malformed "
            "private_key."
        ) from None


def client_kwargs() -> dict[str, Any]:
    """Extra keyword arguments for a google-cloud client constructor."""
    creds = credentials_from_env()
    return {"credentials": creds} if creds is not None else {}
