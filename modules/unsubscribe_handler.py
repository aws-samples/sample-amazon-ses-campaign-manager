#!/usr/bin/env python3
"""
Unsubscribe Handler Module
Handles email unsubscribe functionality including encryption and header generation
"""

from typing import Dict, Optional, Tuple
from cryptography.fernet import Fernet
import base64

from config.settings import settings
from modules.logger import get_logger


class UnsubscribeHandler:
    """Manages unsubscribe functionality for email campaigns."""
    
    def __init__(self, settings_instance=None):
        self.settings = settings_instance or settings
        self.logger = get_logger()
    
    def generate_encryption_key(self) -> str:
        """Generate a new Fernet encryption key."""
        key = Fernet.generate_key()
        key_str = key.decode('utf-8')
        
        if self.logger:
            self.logger.debug("Generated new encryption key for unsubscribe functionality", "UNSUBSCRIBE")
        
        return key_str
    
    def get_encryption_key(self) -> Optional[str]:
        """Get the encryption key from settings."""
        return self.settings.get('email.unsubscribe_encryption_key')
    
    def set_encryption_key(self, key: str) -> None:
        """Save the encryption key to settings."""
        self.settings.set('email.unsubscribe_encryption_key', key)
        
        if self.logger:
            self.logger.debug("Saved encryption key to settings", "UNSUBSCRIBE")
    
    def encrypt_email(self, email: str) -> Optional[str]:
        """
        Encrypt an email address for use in unsubscribe links.
        
        Args:
            email: The email address to encrypt
            
        Returns:
            Base64-encoded encrypted email, or None if encryption fails
        """
        key = self.get_encryption_key()
        if not key:
            if self.logger:
                self.logger.error("No encryption key configured for unsubscribe", "UNSUBSCRIBE")
            return None
        
        try:
            fernet = Fernet(key.encode('utf-8'))
            encrypted = fernet.encrypt(email.encode('utf-8'))
            # Make URL-safe by using base64 URL-safe encoding
            encrypted_str = base64.urlsafe_b64encode(encrypted).decode('utf-8')
            
            # Note: Per-email logging disabled to prevent massive log files with bulk sends
            # if self.logger:
            #     self.logger.debug(f"Encrypted email address for unsubscribe: {email[:3]}***", "UNSUBSCRIBE")
            
            return encrypted_str
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to encrypt email: {str(e)}", "UNSUBSCRIBE")
            return None
    
    def generate_unsubscribe_link(
        self, 
        email: str, 
        base_url: Optional[str] = None,
        topic: Optional[str] = None
    ) -> Optional[str]:
        """
        Generate an unsubscribe link for an email address.
        
        Args:
            email: The email address to generate link for
            base_url: The base URL for the unsubscribe endpoint (from settings if not provided)
            topic: Optional topic/campaign identifier
            
        Returns:
            The complete unsubscribe URL, or None if generation fails
        """
        if not base_url:
            base_url = self.settings.get('email.unsubscribe_base_url')
        
        if not base_url:
            if self.logger:
                self.logger.error("No unsubscribe base URL configured", "UNSUBSCRIBE")
            return None
        
        encrypted_email = self.encrypt_email(email)
        if not encrypted_email:
            return None
        
        # Construct the unsubscribe URL
        url = f"{base_url}?user={encrypted_email}"
        if topic:
            url += f"&topic={topic}"
        
        # Note: Per-email logging disabled to prevent massive log files with bulk sends
        # if self.logger:
        #     self.logger.debug(f"Generated unsubscribe link for {email[:3]}***", "UNSUBSCRIBE")
        
        return url
    
    def generate_list_unsubscribe_headers(
        self, 
        email: str, 
        topic: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """
        Generate List-Unsubscribe headers for RFC 8058 compliance.
        
        Args:
            email: The recipient email address
            topic: Optional topic/campaign identifier
            
        Returns:
            Dictionary with List-Unsubscribe and List-Unsubscribe-Post headers, or None if generation fails
        """
        # Get configuration
        unsubscribe_url = self.settings.get('email.unsubscribe_endpoint_url')
        mailto_address = self.settings.get('email.unsubscribe_mailto')
        
        if not unsubscribe_url and not mailto_address:
            if self.logger:
                self.logger.error("No unsubscribe URL or mailto configured for List-Unsubscribe headers", "UNSUBSCRIBE")
            return None
        
        encrypted_email = self.encrypt_email(email)
        if not encrypted_email:
            return None
        
        headers = {}
        
        # Build List-Unsubscribe header
        unsubscribe_options = []
        
        if unsubscribe_url:
            # Add HTTPS unsubscribe URL
            url = f"{unsubscribe_url}?address={encrypted_email}"
            if topic:
                url += f"&topic={topic}"
            unsubscribe_options.append(f"<{url}>")
        
        if mailto_address:
            # Add mailto option
            subject = f"TopicUnsubscribe" if topic else "Unsubscribe"
            unsubscribe_options.append(f"<mailto:{mailto_address}?subject={subject}>")
        
        if unsubscribe_options:
            headers['List-Unsubscribe'] = ', '.join(unsubscribe_options)
            # Add List-Unsubscribe-Post header for one-click unsubscribe
            headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
            
            # Note: Per-email logging disabled to prevent massive log files with bulk sends
            # if self.logger:
            #     self.logger.debug(f"Generated List-Unsubscribe headers for {email[:3]}***", "UNSUBSCRIBE")
        
        return headers if headers else None
    
    def add_unsubscribe_to_template_data(
        self, 
        template_data: Dict, 
        email: str,
        unsubscribe_type: str = 'link',
        topic: Optional[str] = None
    ) -> Dict:
        """
        Add unsubscribe link to template data.
        
        Args:
            template_data: The existing template data dictionary
            email: The recipient email address
            unsubscribe_type: Type of unsubscribe ('link' or 'headers' or 'both')
            topic: Optional topic/campaign identifier
            
        Returns:
            Updated template data with unsubscribe link
        """
        if unsubscribe_type in ['link', 'both']:
            unsubscribe_link = self.generate_unsubscribe_link(email, topic=topic)
            if unsubscribe_link:
                template_data['unsubscribe_link'] = unsubscribe_link
                
                # Note: Per-email logging disabled to prevent massive log files with bulk sends
                # if self.logger:
                #     self.logger.debug(f"Added unsubscribe_link to template data for {email[:3]}***", "UNSUBSCRIBE")
        
        return template_data
    
    def validate_configuration(self) -> Tuple[bool, str]:
        """
        Validate unsubscribe configuration.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if encryption key exists
        if not self.get_encryption_key():
            return False, "No encryption key configured. Generate one in Settings."
        
        # Check if at least one unsubscribe method is configured
        has_link = bool(self.settings.get('email.unsubscribe_base_url'))
        has_headers = bool(
            self.settings.get('email.unsubscribe_endpoint_url') or 
            self.settings.get('email.unsubscribe_mailto')
        )
        
        if not has_link and not has_headers:
            return False, "No unsubscribe URL or mailto configured. Configure in Settings."
        
        return True, ""
    
    def get_unsubscribe_config(self) -> Dict:
        """Get current unsubscribe configuration."""
        return {
            'encryption_key_set': bool(self.get_encryption_key()),
            'base_url': self.settings.get('email.unsubscribe_base_url', ''),
            'endpoint_url': self.settings.get('email.unsubscribe_endpoint_url', ''),
            'mailto': self.settings.get('email.unsubscribe_mailto', ''),
        }
