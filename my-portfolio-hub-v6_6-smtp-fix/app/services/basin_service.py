"""
app/services/basin_service.py — Basin Contact Form Service (v4.1)

Basin is a serverless form backend (https://usebasin.com).
Each tenant can configure their own Basin endpoint.
Submissions are forwarded server-side; the endpoint URL is never
exposed in client-side HTML.

Routing:
  provider == 'basin'    → forward submission to tenant's basin_endpoint
  provider == 'internal' → store in CMS Inquiry table (existing behaviour)

Basin endpoint format: https://usebasin.com/f/<form_id>
Basin accepts standard HTML form fields; returns JSON {success: true/false}.
"""
import logging
from typing import Optional

import requests
from flask import request as flask_request

from app.models.portfolio import Tenant

logger = logging.getLogger(__name__)

_BASIN_ACCEPT_URL_PREFIX = 'https://usebasin.com/f/'
_TIMEOUT = 10  # seconds


def validate_basin_endpoint(url: str) -> tuple[bool, str]:
    """
    Basic validation of a Basin endpoint URL.
    Does NOT submit a live test (Basin charges per submission).
    """
    if not url:
        return False, 'Basin endpoint URL is required.'
    url = url.strip()
    if not url.startswith(_BASIN_ACCEPT_URL_PREFIX):
        return False, f'Basin endpoint must start with {_BASIN_ACCEPT_URL_PREFIX}'
    path_part = url.removeprefix(_BASIN_ACCEPT_URL_PREFIX)
    if len(path_part) < 4:
        return False, 'Basin form ID appears too short. Check the URL.'
    return True, 'Endpoint URL looks valid.'


def submit_to_basin(
    basin_endpoint: str,
    name: str,
    email: str,
    subject: str,
    message: str,
    extra_fields: Optional[dict] = None,
) -> tuple[bool, str]:
    """
    Forward a contact form submission to a Basin endpoint.

    Returns (success: bool, error_message_or_empty: str).
    The endpoint URL is resolved server-side from the Tenant record —
    it is NEVER accepted from client input.

    Args:
        basin_endpoint: The tenant's validated Basin URL (from DB).
        name:    Submitter's name.
        email:   Submitter's email.
        subject: Message subject.
        message: Message body.
        extra_fields: Any additional fields to include.
    """
    if not basin_endpoint or not basin_endpoint.startswith(_BASIN_ACCEPT_URL_PREFIX):
        logger.error('basin_service: invalid endpoint (does not start with expected prefix)')
        return False, 'Invalid Basin endpoint configured for this tenant.'

    payload = {
        'name':    name[:200],
        'email':   email[:200],
        'subject': subject[:500],
        'message': message[:5000],
    }
    if extra_fields:
        for k, v in extra_fields.items():
            if k not in payload:  # Never overwrite core fields
                payload[k] = str(v)[:500]

    try:
        resp = requests.post(
            basin_endpoint,
            data=payload,
            headers={
                'Accept':    'application/json',
                'X-Source':  'Portfolio CMS',
            },
            timeout=_TIMEOUT,
        )
        body = resp.json()
        if resp.status_code in (200, 201) and body.get('success'):
            logger.info('basin_service: submitted to ***%s OK', basin_endpoint[-8:])
            return True, ''
        err = body.get('error') or body.get('message', f'HTTP {resp.status_code}')
        logger.warning('basin_service: submission failed: %s', err)
        return False, err
    except requests.Timeout:
        logger.error('basin_service: timeout after %ds', _TIMEOUT)
        return False, 'Basin service timed out.'
    except Exception as exc:
        logger.exception('basin_service: unexpected error: %s', exc)
        return False, str(exc)


def get_tenant_form_config(tenant: Tenant) -> dict:
    """
    Return contact form routing config for a tenant.

    Returns:
        {
            'provider': 'basin' | 'internal',
            'basin_endpoint': str | None,
            'basin_valid': bool,
        }
    """
    provider = getattr(tenant, 'form_provider', 'internal') or 'internal'
    endpoint = getattr(tenant, 'basin_endpoint', None) or None

    basin_valid = False
    if provider == 'basin' and endpoint:
        valid, _ = validate_basin_endpoint(endpoint)
        basin_valid = valid

    return {
        'provider':       provider,
        'basin_endpoint': endpoint,
        'basin_valid':    basin_valid,
    }
