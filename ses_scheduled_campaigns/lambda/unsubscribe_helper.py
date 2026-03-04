"""
Unsubscribe Helper for Lambda
Handles unsubscribe link generation and encryption
Based on ses_tui/modules/unsubscribe_handler.py
"""

import os
import base64
from typing import Optional, Dict
from cryptography.fernet import Fernet


def get_env_config() -> Dict[str, str]:
    """Get unsubscribe configuration from environment variables"""
    return {
        'encryption_key': os.environ.get('UNSUBSCRIBE_ENCRYPTION_KEY', ''),
        'base_url': os.environ.get('UNSUBSCRIBE_BASE_URL', ''),
        'endpoint_url': os.environ.get('UNSUBSCRIBE_ENDPOINT_URL', ''),
        'mailto': os.environ.get('UNSUBSCRIBE_MAILTO', '')
    }


def encrypt_email(email: str, encryption_key: str) -> Optional[str]:
    """
    Encrypt an email address for use in unsubscribe links
    
    Args:
        email: The email address to encrypt
        encryption_key: Fernet encryption key
        
    Returns:
        Base64-encoded encrypted email, or None if encryption fails
    """
    if not encryption_key:
        return None
    
    try:
        fernet = Fernet(encryption_key.encode('utf-8'))
        encrypted = fernet.encrypt(email.encode('utf-8'))
        # Make URL-safe
        encrypted_str = base64.urlsafe_b64encode(encrypted).decode('utf-8')
        return encrypted_str
    except Exception as e:
        print(f'Failed to encrypt email: {str(e)}')
        return None


def generate_unsubscribe_link(
    email: str,
    base_url: str,
    encryption_key: str,
    topic: Optional[str] = None
) -> Optional[str]:
    """
    Generate an unsubscribe link for an email address
    
    Args:
        email: The email address to generate link for
        base_url: The base URL for the unsubscribe endpoint
        encryption_key: Fernet encryption key
        topic: Optional topic/campaign identifier
        
    Returns:
        The complete unsubscribe URL, or None if generation fails
    """
    if not base_url or not encryption_key:
        return None
    
    encrypted_email = encrypt_email(email, encryption_key)
    if not encrypted_email:
        return None
    
    # Construct the unsubscribe URL
    url = f"{base_url}?user={encrypted_email}"
    if topic:
        url += f"&topic={topic}"
    
    return url


def generate_list_unsubscribe_headers(
    email: str,
    encryption_key: str,
    endpoint_url: str = '',
    mailto: str = '',
    topic: Optional[str] = None
) -> Optional[Dict[str, str]]:
    """
    Generate List-Unsubscribe headers for RFC 8058 compliance
    
    Args:
        email: The recipient email address
        encryption_key: Fernet encryption key
        endpoint_url: HTTPS unsubscribe endpoint URL
        mailto: Optional mailto address
        topic: Optional topic/campaign identifier
        
    Returns:
        Dictionary with List-Unsubscribe and List-Unsubscribe-Post headers, or None
    """
    if not endpoint_url and not mailto:
        return None
    
    if not encryption_key:
        return None
    
    encrypted_email = encrypt_email(email, encryption_key)
    if not encrypted_email:
        return None
    
    headers = {}
    unsubscribe_options = []
    
    if endpoint_url:
        # Add HTTPS unsubscribe URL
        url = f"{endpoint_url}?address={encrypted_email}"
        if topic:
            url += f"&topic={topic}"
        unsubscribe_options.append(f"<{url}>")
    
    if mailto:
        # Add mailto option
        subject = f"TopicUnsubscribe" if topic else "Unsubscribe"
        unsubscribe_options.append(f"<mailto:{mailto}?subject={subject}>")
    
    if unsubscribe_options:
        headers['List-Unsubscribe'] = ', '.join(unsubscribe_options)
        # Add List-Unsubscribe-Post header for one-click unsubscribe
        headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
    
    return headers if headers else None


def add_unsubscribe_to_template_data(
    template_data: Dict,
    email: str,
    unsubscribe_type: str = 'link',
    topic: Optional[str] = None
) -> Dict:
    """
    Add unsubscribe link to template data
    
    Args:
        template_data: The existing template data dictionary
        email: The recipient email address
        unsubscribe_type: Type of unsubscribe ('link' or 'headers' or 'both')
        topic: Optional topic/campaign identifier
        
    Returns:
        Updated template data with unsubscribe link
    """
    config = get_env_config()
    
    if unsubscribe_type in ['link', 'both']:
        if config['base_url'] and config['encryption_key']:
            unsubscribe_link = generate_unsubscribe_link(
                email,
                config['base_url'],
                config['encryption_key'],
                topic
            )
            if unsubscribe_link:
                template_data['unsubscribe_link'] = unsubscribe_link
    
    return template_data
