#!/usr/bin/env python3
"""
Settings management for SES Manager
Handles loading and saving application configuration
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path


class Settings:
    """Manages application settings with JSON persistence."""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(exist_ok=True)
        self.settings_file = self.config_dir / "settings.json"
        self._settings = self._load_settings()
    
    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from JSON file."""
        default_settings = {
            "aws": {
                "profile": "default",
                "region": "us-east-1",
                "last_used": None
            },
            "ui": {
                "theme": "dark",
                "last_tab": "templates"
            },
            "email": {
                "default_template_data": {
                    "name": "John",
                    "company": "Example Corp"
                },
                "unsubscribe_encryption_key": None,
                "unsubscribe_base_url": "",
                "unsubscribe_endpoint_url": "",
                "unsubscribe_mailto": ""
            },
            "ses": {
                "sending_rate": 1
            },
            "cache": {
                "enabled": True,
                "default_ttl_minutes": 15,
                "dashboard_ttl_minutes": 10,
                "templates_ttl_minutes": 30,
                "identities_ttl_minutes": 60,
                "configuration_sets_ttl_minutes": 120,
                "metrics_ttl_minutes": 5
            },
            "app": {
                "version": "1.0.0",
                "last_updated": None,
                "debug_logging": False,
                "verbose_notifications": False
            }
        }
        
        if not self.settings_file.exists():
            self._save_settings(default_settings)
            return default_settings
        
        try:
            with open(self.settings_file, 'r') as f:
                settings = json.load(f)
                # Merge with defaults to ensure all keys exist
                return self._merge_settings(default_settings, settings)
        except (json.JSONDecodeError, FileNotFoundError):
            # If file is corrupted, use defaults
            self._save_settings(default_settings)
            return default_settings
    
    def _merge_settings(self, defaults: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
        """Merge loaded settings with defaults to ensure all keys exist."""
        result = defaults.copy()
        for key, value in loaded.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
        return result
    
    def _save_settings(self, settings: Dict[str, Any]) -> None:
        """Save settings to JSON file."""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2, default=str)
        except Exception as e:
            print(f"Warning: Could not save settings: {e}")
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """Get a setting value using dot notation (e.g., 'aws.region')."""
        keys = key_path.split('.')
        value = self._settings
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def set(self, key_path: str, value: Any) -> None:
        """Set a setting value using dot notation (e.g., 'aws.region')."""
        keys = key_path.split('.')
        current = self._settings
        
        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        # Set the final key
        current[keys[-1]] = value
        
        # Update last_updated timestamp
        self.set_last_updated()
        
        # Save to file
        self._save_settings(self._settings)
    
    def set_aws_config(self, profile: str, region: str) -> None:
        """Set AWS configuration and update last used timestamp."""
        self.set('aws.profile', profile)
        self.set('aws.region', region)
        self.set('aws.last_used', datetime.now().isoformat())
    
    def get_aws_config(self) -> Dict[str, str]:
        """Get AWS configuration."""
        return {
            'profile': self.get('aws.profile', 'default'),
            'region': self.get('aws.region', 'us-east-1')
        }
    
    def set_last_updated(self) -> None:
        """Update the last updated timestamp."""
        self._settings['app']['last_updated'] = datetime.now().isoformat()
    
    def get_all(self) -> Dict[str, Any]:
        """Get all settings."""
        return self._settings.copy()
    
    def reset_to_defaults(self) -> None:
        """Reset all settings to defaults."""
        if self.settings_file.exists():
            self.settings_file.unlink()
        self._settings = self._load_settings()


# Global settings instance
settings = Settings()
