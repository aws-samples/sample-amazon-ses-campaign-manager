#!/usr/bin/env python3
"""
Notification Helper Module
Provides conditional notification functionality based on verbosity settings
"""

from config.settings import settings


def should_show_verbose_notification() -> bool:
    """Check if verbose notifications are enabled."""
    return settings.get('app.verbose_notifications', False)


def notify_verbose(app, message: str, severity: str = "information") -> None:
    """
    Show notification only if verbose notifications are enabled.
    Use this for informational messages about UI state changes.
    
    Args:
        app: The Textual app instance
        message: The notification message
        severity: The notification severity (information, success, warning, error)
    """
    if should_show_verbose_notification():
        app.notify(message, severity=severity)


def notify_always(app, message: str, severity: str = "information") -> None:
    """
    Always show notification regardless of verbosity setting.
    Use this for important messages like errors, successful operations, etc.
    
    Args:
        app: The Textual app instance
        message: The notification message
        severity: The notification severity (information, success, warning, error)
    """
    app.notify(message, severity=severity)
