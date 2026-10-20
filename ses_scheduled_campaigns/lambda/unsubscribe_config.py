"""
Unsubscribe configuration for Lambda
Loads the Fernet key and URLs without importing cryptography, so the scheduler
can validate configuration without the cryptography layer
"""

import hashlib
import os
from typing import Dict, Optional

import boto3

_encryption_key = None


def get_encryption_key() -> str:
    """
    Fernet key from SSM Parameter Store (SecureString) when configured,
    otherwise from the UNSUBSCRIBE_ENCRYPTION_KEY environment variable
    """
    global _encryption_key

    if _encryption_key is None:
        parameter_name = os.environ.get('UNSUBSCRIBE_ENCRYPTION_KEY_PARAM', '')
        if parameter_name:
            ssm = boto3.client('ssm')
            _encryption_key = ssm.get_parameter(Name=parameter_name, WithDecryption=True)['Parameter']['Value']
        else:
            _encryption_key = os.environ.get('UNSUBSCRIBE_ENCRYPTION_KEY', '')

    return _encryption_key


def get_env_config() -> Dict[str, str]:
    """Get unsubscribe configuration from environment variables / SSM"""
    return {
        'encryption_key': get_encryption_key(),
        'base_url': os.environ.get('UNSUBSCRIBE_BASE_URL', ''),
        'endpoint_url': os.environ.get('UNSUBSCRIBE_ENDPOINT_URL', ''),
        'mailto': os.environ.get('UNSUBSCRIBE_MAILTO', '')
    }


def key_fingerprint(encryption_key: str) -> str:
    """Short one-way fingerprint for checking two components use the same key"""
    if not encryption_key:
        return ''
    return hashlib.sha256(encryption_key.encode('utf-8')).hexdigest()[:16]


def check_unsubscribe_config(unsubscribe_type: str, client_fingerprint: Optional[str] = None) -> Optional[str]:
    """
    Validate that this stack can generate the requested unsubscribe links

    Args:
        unsubscribe_type: 'link', 'headers' or 'both'
        client_fingerprint: key_fingerprint() of the key Amazon SES Campaign Manager uses

    Returns:
        An error message, or None if the configuration is usable
    """
    try:
        config = get_env_config()
    except Exception as e:
        return f'Unsubscribe is enabled but the encryption key could not be loaded: {str(e)}'

    if not config['encryption_key']:
        return ('Unsubscribe is enabled but no unsubscribe encryption key is configured in the '
                'scheduled campaigns stack (ses_scheduled_campaigns/config.json)')

    if client_fingerprint and client_fingerprint != key_fingerprint(config['encryption_key']):
        return ('The unsubscribe encryption key in the scheduled campaigns stack does not match the one in '
                'Amazon SES Campaign Manager settings, so your unsubscribe endpoint could not decrypt these '
                'links. Copy the key from config/settings.json into ses_scheduled_campaigns/config.json and redeploy.')

    if unsubscribe_type in ['link', 'both'] and not config['base_url']:
        return 'Unsubscribe links are enabled but unsubscribeBaseUrl is not configured in the stack'

    if unsubscribe_type in ['headers', 'both'] and not (config['endpoint_url'] or config['mailto']):
        return ('List-Unsubscribe headers are enabled but neither unsubscribeEndpointUrl nor '
                'unsubscribeMailto is configured in the stack')

    return None
