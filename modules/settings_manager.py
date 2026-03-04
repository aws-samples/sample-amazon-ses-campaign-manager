#!/usr/bin/env python3
"""
Settings Manager Module
Handles application settings and configuration
"""

from typing import Dict, Any
from datetime import datetime

from textual.containers import Container, Horizontal, ScrollableContainer
from textual.widgets import Button, Static, Switch, Label, Select

from config.settings import settings
from modules.cache_manager import CacheManager
from modules.logger import get_logger
from modules.notification_helper import notify_verbose, notify_always


class SettingsManager:
    """Manages application settings and configuration."""
    
    def __init__(self, app):
        self.app = app
        self.settings = settings
    
    def get_debug_logging_enabled(self) -> bool:
        """Get debug logging setting."""
        return self.settings.get('app.debug_logging', False)
    
    def set_debug_logging_enabled(self, enabled: bool) -> None:
        """Set debug logging setting."""
        self.settings.set('app.debug_logging', enabled)
    
    def get_max_log_size_mb(self) -> float:
        """Get maximum log file size in MB."""
        return self.settings.get('app.max_log_size_mb', 5.0)
    
    def set_max_log_size_mb(self, size_mb: float) -> None:
        """Set maximum log file size in MB."""
        self.settings.set('app.max_log_size_mb', max(1.0, min(size_mb, 100.0)))  # Cap between 1-100 MB
    
    def get_max_backup_logs(self) -> int:
        """Get maximum number of backup logs to keep."""
        return self.settings.get('app.max_backup_logs', 5)
    
    def set_max_backup_logs(self, count: int) -> None:
        """Set maximum number of backup logs to keep."""
        self.settings.set('app.max_backup_logs', max(1, min(count, 20)))  # Cap between 1-20
    
    def get_verbose_notifications_enabled(self) -> bool:
        """Get verbose notifications setting."""
        return self.settings.get('app.verbose_notifications', False)
    
    def set_verbose_notifications_enabled(self, enabled: bool) -> None:
        """Set verbose notifications setting."""
        self.settings.set('app.verbose_notifications', enabled)
    
    def get_default_configuration_set(self) -> str:
        """Get default SES configuration set."""
        return self.settings.get('email.default_configuration_set', '')
    
    def set_default_configuration_set(self, config_set: str) -> None:
        """Set default SES configuration set."""
        self.settings.set('email.default_configuration_set', config_set)
    
    def get_max_retries(self) -> int:
        """Get maximum retry attempts for bulk email sending."""
        return self.settings.get('email.max_retries', 3)
    
    def set_max_retries(self, retries: int) -> None:
        """Set maximum retry attempts for bulk email sending."""
        self.settings.set('email.max_retries', max(0, min(retries, 10)))  # Cap between 0-10
    
    def get_base_retry_delay(self) -> float:
        """Get base retry delay in seconds for exponential backoff."""
        return self.settings.get('email.base_retry_delay', 1.0)
    
    def set_base_retry_delay(self, delay: float) -> None:
        """Set base retry delay in seconds for exponential backoff."""
        self.settings.set('email.base_retry_delay', max(0.1, min(delay, 10.0)))  # Cap between 0.1-10.0
    
    def get_aws_config(self) -> Dict[str, str]:
        """Get AWS configuration."""
        return self.settings.get_aws_config()
    
    def export_settings(self) -> Dict[str, Any]:
        """Export all settings."""
        return {
            'app_settings': {
                'debug_logging': self.get_debug_logging_enabled()
            },
            'aws_config': self.get_aws_config(),
            'export_timestamp': datetime.now().isoformat()
        }
    
    def reset_all_settings(self) -> None:
        """Reset all settings to defaults."""
        # Keep AWS config but reset other settings
        aws_config = self.get_aws_config()
        self.settings.data = {}
        if aws_config:
            self.settings.set_aws_config(aws_config.get('profile', ''), aws_config.get('region', ''))


def create_settings_tab_content():
    """Create the content for the settings tab."""
    from textual.widgets import Input, Collapsible
    
    # Return flat list with scrollable container for settings
    return [
        ScrollableContainer(
            Label("⚙️ Settings", classes="form-section-title"),
            
            # Application Settings Section
            Collapsible(
                Container(
                    Static(
                        "Configure application behavior and UI preferences.\n\n"
                        "• Verbose Notifications: Shows all UI notifications including tab switches and status updates",
                        classes="setting-description"
                    ),
                    Horizontal(
                        Label("Verbose Notifications:", classes="setting-label-wide"),
                        Switch(id="verbose-notifications-switch"),
                        classes="setting-row"
                    ),
                ),
                title="🔧 Application Settings",
                collapsed=True,
                id="app-settings-collapsible"
            ),
            
            # Email Settings Section
            Collapsible(
                ScrollableContainer(
                    Static(
                        "Configure default email sending preferences.\n\n"
                        "• Default Configuration Set: Pre-populate this value when sending emails\n\n"
                        "RETRY & EXPONENTIAL BACKOFF SETTINGS:\n"
                        "• Max Retries (0-10, default: 3): Number of retry attempts for transient errors\n"
                        "  - Retryable errors: Throttling, ServiceUnavailable, InternalError, RequestTimeout, HTTP 429/500/502/503/504\n"
                        "  - Non-retryable errors: InvalidParameterValue, MessageRejected, permanent failures\n"
                        "  - Set to 0 to disable retries (fail immediately on errors)\n\n"
                        "• Base Retry Delay (0.1-10.0s, default: 1.0): Initial delay before first retry\n"
                        "  - Uses exponential backoff: delay = base_delay × (2^attempt), capped at 30s\n"
                        "  - Throttling errors use 2× base delay to give AWS more recovery time\n"
                        "  - Example with base=1.0: 1s → 2s → 4s → 8s → 16s → 30s (capped)\n"
                        "  - Example with base=2.0 (throttle): 4s → 8s → 16s → 30s (capped)\n\n"
                        "BEST PRACTICES:\n"
                        "• Keep default (3 retries, 1.0s base) for most use cases\n"
                        "• Increase retries (5-7) for critical campaigns with unreliable networks\n"
                        "• Increase base delay (2-3s) if experiencing frequent throttling\n"
                        "• Disable retries (0) only for testing or when handling retries externally",
                        classes="setting-description"
                    ),
                    Horizontal(
                        Label("Default Config Set:", classes="setting-label"),
                        Select(
                            [("None", "")],
                            id="default-config-set-select",
                            classes="form-select",
                            allow_blank=False
                        ),
                        classes="setting-row"
                    ),
                    Horizontal(
                        Label("Max Retries (0-10):", classes="setting-label"),
                        Input(
                            placeholder="3",
                            id="max-retries-input",
                            classes="form-input"
                        ),
                        classes="setting-row"
                    ),
                    Horizontal(
                        Label("Base Retry Delay (0.1-10.0s):", classes="setting-label"),
                        Input(
                            placeholder="1.0",
                            id="base-retry-delay-input",
                            classes="form-input"
                        ),
                        classes="setting-row"
                    ),
                    Horizontal(
                        Button("Save", id="save-email-settings-btn", variant="success"),
                        Button("🔄 Refresh List", id="refresh-config-sets-btn", variant="default"),
                        classes="setting-actions"
                    ),
                    Static("", id="email-settings-status", classes="form-help-text"),
                    id="email-settings-scroll-container"
                ),
                title="📧 Email Settings",
                collapsed=True,
                id="email-settings-collapsible"
            ),
            
            # AWS Configuration Section
            Collapsible(
                Container(
                    Static("", id="aws-config-display", classes="config-display"),
                    Horizontal(
                        Button("Change AWS Profile", variant="default", id="change-aws-profile"),
                        classes="setting-actions"
                    ),
                ),
                title="☁️ AWS Configuration",
                collapsed=True,
                id="aws-config-collapsible"
            ),
            
            # Unsubscribe Configuration Section
            Collapsible(
                ScrollableContainer(
                    Static("", id="unsubscribe-config-display", classes="config-display"),
                    Horizontal(
                        Button("Generate Encryption Key", variant="primary", id="generate-unsub-key-btn"),
                        classes="setting-actions"
                    ),
                    Collapsible(
                        Container(
                            Horizontal(
                                Label("Base URL:", classes="setting-label"),
                                Input(
                                    placeholder="https://example.com/unsubscribe",
                                    id="unsub-base-url-input",
                                    classes="setting-input"
                                ),
                                classes="setting-row-input"
                            ),
                            Horizontal(
                                Label("Endpoint URL:", classes="setting-label"),
                                Input(
                                    placeholder="https://api.example.com/unsubscribe",
                                    id="unsub-endpoint-url-input",
                                    classes="setting-input"
                                ),
                                classes="setting-row-input"
                            ),
                            Horizontal(
                                Label("Mailto:", classes="setting-label"),
                                Input(
                                    placeholder="unsubscribe@example.com",
                                    id="unsub-mailto-input",
                                    classes="setting-input"
                                ),
                                classes="setting-row-input"
                            ),
                            Horizontal(
                                Button("Save Unsubscribe Settings", variant="success", id="save-unsub-settings-btn"),
                                classes="setting-actions"
                            ),
                        ),
                        title="⚙️ Configure URLs",
                        collapsed=True,
                        id="unsubscribe-urls-collapsible"
                    ),
                    id="unsubscribe-config-scroll-container"
                ),
                title="🔗 Unsubscribe Configuration",
                collapsed=True,
                id="unsubscribe-config-collapsible"
            ),
            
            # Cache Management Section
            Collapsible(
                Container(
                    Static("", id="cache-stats-display", classes="stats-display"),
                    Horizontal(
                        Button("View Cache Stats", variant="default", id="view-cache-stats"),
                        Button("Clear All Cache", variant="default", id="clear-all-cache"),
                        classes="setting-actions"
                    ),
                ),
                title="💾 Cache Management",
                collapsed=True,
                id="cache-management-collapsible"
            ),
            
            # Debug Log Management Section
            Collapsible(
                ScrollableContainer(
                    Static(
                        "Configure debug logging behavior and automatic file rotation.\n\n"
                        "📁 Log Location: logs/debug.log\n"
                        "📦 Backups: logs/debug_YYYYMMDD_HHMMSS.log\n\n"
                        "• Debug Logging: When enabled, writes detailed logs to file\n"
                        "• Max Log Size: Auto-rotates when current log reaches this size (1-100 MB)\n"
                        "• Max Backups: Number of backup files to keep (1-20, default 5)\n"
                        "  When creating a new backup, oldest backups beyond this limit are auto-deleted\n\n"
                        "ℹ️  No backups? Log hasn't reached max size yet (no rotation needed)",
                        classes="setting-description"
                    ),
                    Horizontal(
                        Label("Enable Debug Logging:", classes="setting-label-wide"),
                        Switch(id="debug-logging-switch"),
                        classes="setting-row"
                    ),
                    Horizontal(
                        Label("Max Log Size (MB):", classes="setting-label"),
                        Input(
                            placeholder="5.0",
                            id="max-log-size-input",
                            classes="form-input"
                        ),
                        classes="setting-row"
                    ),
                    Horizontal(
                        Label("Max Backups to Keep:", classes="setting-label"),
                        Input(
                            placeholder="5",
                            id="max-backup-logs-input",
                            classes="form-input"
                        ),
                        classes="setting-row"
                    ),
                    Horizontal(
                        Button("Save Settings", id="save-log-settings-btn", variant="success"),
                        classes="setting-actions"
                    ),
                    Static("", id="log-settings-status", classes="form-help-text"),
                    Static("", id="debug-log-info", classes="stats-display"),
                    Static("", id="backup-logs-info", classes="stats-display"),
                    Horizontal(
                        Button("Clear Current Log", variant="default", id="clear-debug-log"),
                        Button("View Log File", variant="default", id="view-log-file"),
                        Button("Refresh Info", variant="default", id="refresh-log-info"),
                        classes="setting-actions"
                    ),
                    id="debug-log-scroll-container"
                ),
                title="📝 Debug Log Management",
                collapsed=True,
                id="debug-log-collapsible"
            ),
            
            # Application Data Section (outside collapsible)
            Label("💼 Application Data", classes="form-subsection-title"),
            Horizontal(
                Button("Export Settings", variant="default", id="export-settings"),
                Button("Reset All Settings", variant="error", id="reset-settings"),
                classes="setting-actions"
            ),
            
            id="settings-scroll-container"
        )
    ]


class SettingsTabHandler:
    """Handles settings tab interactions."""
    
    def __init__(self, app, settings_manager: SettingsManager):
        self.app = app
        self.settings_manager = settings_manager
        self.cache_manager = CacheManager(settings)
    
    def update_settings_display(self) -> None:
        """Update the settings display with current values."""
        try:
            # Update debug logging switch
            try:
                debug_switch = self.app.query_one("#debug-logging-switch", Switch)
                debug_switch.value = self.settings_manager.get_debug_logging_enabled()
            except:
                pass  # Switch might not be mounted yet (inside collapsed section)  # nosec B110 - Intentional - widget may not be mounted
            
            # Update verbose notifications switch
            try:
                verbose_switch = self.app.query_one("#verbose-notifications-switch", Switch)
                verbose_switch.value = self.settings_manager.get_verbose_notifications_enabled()
            except:
                pass  # Switch might not be mounted yet  # nosec B110 - Intentional - widget may not be mounted
            
            # Update AWS config display
            try:
                aws_config = self.settings_manager.get_aws_config()
                aws_display = self.app.query_one("#aws-config-display", Static)
                if aws_config:
                    aws_text = f"Profile: {aws_config.get('profile', 'Not set')}\nRegion: {aws_config.get('region', 'Not set')}"
                else:
                    aws_text = "AWS configuration not set"
                aws_display.update(aws_text)
            except:
                pass  # Widget might not be mounted yet (inside collapsed section)  # nosec B110 - Intentional - widget may not be mounted
            
            # Update email settings (config set dropdown)
            try:
                self.update_email_settings_display()
            except:
                pass  # Widgets might not be mounted yet (inside collapsed section)  # nosec B110 - Intentional - widget may not be mounted
            
            # Update unsubscribe config display
            try:
                self.update_unsubscribe_config_display()
            except:
                pass  # Widgets might not be mounted yet (inside collapsed section)  # nosec B110 - Intentional - widget may not be mounted
            
            # Update cache stats display
            try:
                self.update_cache_stats_display()
            except:
                pass  # Widgets might not be mounted yet (inside collapsed section)  # nosec B110 - Intentional - widget may not be mounted
            
            # Update debug log info display
            try:
                self.update_debug_log_info_display()
            except:
                pass  # Widgets might not be mounted yet (inside collapsed section)  # nosec B110 - Intentional - widget may not be mounted
            
        except Exception as e:
            if self.settings_manager.get_debug_logging_enabled():
                self.app.notify(f"Debug: Error updating settings display: {str(e)}", severity="warning")
    
    def handle_debug_logging_toggle(self, enabled: bool) -> None:
        """Handle debug logging toggle."""
        self.settings_manager.set_debug_logging_enabled(enabled)
        status = "enabled" if enabled else "disabled"
        notify_verbose(self.app, f"Debug logging {status}", severity="information")
    
    def handle_verbose_notifications_toggle(self, enabled: bool) -> None:
        """Handle verbose notifications toggle."""
        self.settings_manager.set_verbose_notifications_enabled(enabled)
        status = "enabled" if enabled else "disabled"
        notify_verbose(self.app, f"Verbose notifications {status}", severity="information")
    
    
    def handle_export_settings(self) -> None:
        """Handle exporting settings."""
        try:
            import json
            settings_data = self.settings_manager.export_settings()
            filename = f"ses_manager_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(settings_data, f, indent=2)
            
            self.app.notify(f"Settings exported to {filename}", severity="success")
        except Exception as e:
            self.app.notify(f"Error exporting settings: {str(e)}", severity="error")
    
    def handle_reset_settings(self) -> None:
        """Handle resetting all settings."""
        self.settings_manager.reset_all_settings()
        self.update_settings_display()
        self.app.notify("All settings reset to defaults", severity="warning")
    
    def update_cache_stats_display(self) -> None:
        """Update the cache statistics display."""
        try:
            cache_stats_display = self.app.query_one("#cache-stats-display", Static)
            stats = self.cache_manager.get_cache_stats()
            
            cache_text = f"""Cache Status: {'Enabled' if stats['enabled'] else 'Disabled'}
Default TTL: {stats['default_ttl_minutes']} minutes
Total Cache Files: {stats['total_cache_files']}
Valid Cache Files: {stats['valid_cache_files']}
Expired Cache Files: {stats['expired_cache_files']}
Cache Size: {stats['cache_size_mb']} MB"""
            
            cache_stats_display.update(cache_text)
            
        except Exception:
            # Widget not mounted yet (inside collapsed section), this is expected
            pass  # nosec B110 - Intentional - widget may not be mounted
    
    def handle_view_cache_stats(self) -> None:
        """Handle viewing cache statistics."""
        self.update_cache_stats_display()
        notify_verbose(self.app, "Cache statistics refreshed", severity="information")
    
    def handle_clear_all_cache(self) -> None:
        """Handle clearing all cache."""
        try:
            self.cache_manager.invalidate_all_cache()
            self.update_cache_stats_display()
            self.app.notify("All cache cleared successfully", severity="success")
        except Exception as e:
            self.app.notify(f"Error clearing cache: {str(e)}", severity="error")
    
    def update_debug_log_info_display(self) -> None:
        """Update the debug log information display."""
        try:
            from textual.widgets import Input
            
            # Update inputs
            try:
                max_log_size_input = self.app.query_one("#max-log-size-input", Input)
                max_log_size_input.value = str(self.settings_manager.get_max_log_size_mb())
                
                max_backup_logs_input = self.app.query_one("#max-backup-logs-input", Input)
                max_backup_logs_input.value = str(self.settings_manager.get_max_backup_logs())
            except:
                pass  # Inputs might not be visible yet  # nosec B110 - Intentional - widget may not be mounted
            
            # Update main debug log info
            debug_log_display = self.app.query_one("#debug-log-info", Static)
            logger = get_logger()
            
            if logger:
                log_file_path = logger.get_log_file_path()
                log_file_size = logger.get_log_file_size()
                max_log_size = self.settings_manager.get_max_log_size_mb()
                debug_enabled = self.settings_manager.get_debug_logging_enabled()
                
                percentage = (log_file_size / max_log_size * 100) if max_log_size > 0 else 0
                
                debug_text = f"""Debug Logging: {'✓ Enabled' if debug_enabled else '✗ Disabled'}
Current Log File: {log_file_path}
Current Size: {log_file_size} MB / {max_log_size} MB ({percentage:.1f}%)
Status: {'Writing to file' if debug_enabled else 'Console only'}"""
                
                # Update backup logs info
                backup_logs_display = self.app.query_one("#backup-logs-info", Static)
                backup_files = logger.get_backup_log_files()
                
                if backup_files:
                    backup_text = f"\n📦 Backup Logs ({len(backup_files)} files):\n"
                    for backup in backup_files[:5]:  # Show max 5 most recent
                        backup_text += f"  • {backup['name']} ({backup['size_mb']} MB) - {backup['modified']}\n"
                    if len(backup_files) > 5:
                        backup_text += f"  ... and {len(backup_files) - 5} more"
                else:
                    backup_text = "\n📦 No backup logs found"
                
                backup_logs_display.update(backup_text)
            else:
                debug_text = "Debug logging system not initialized"
            
            debug_log_display.update(debug_text)
            
        except Exception:
            # Widget not mounted yet (inside collapsed section), this is expected
            pass  # nosec B110 - Intentional - widget may not be mounted
    
    def handle_clear_debug_log(self) -> None:
        """Handle clearing the debug log file."""
        try:
            logger = get_logger()
            if logger:
                logger.clear_log_file()
                self.update_debug_log_info_display()
                self.app.notify("Debug log file cleared successfully", severity="success")
            else:
                self.app.notify("Debug logging system not available", severity="error")
        except Exception as e:
            self.app.notify(f"Error clearing debug log: {str(e)}", severity="error")
    
    def handle_save_log_settings(self) -> None:
        """Handle saving log settings."""
        try:
            from textual.widgets import Input
            
            # Get inputs
            max_log_size_input = self.app.query_one("#max-log-size-input", Input)
            max_backup_logs_input = self.app.query_one("#max-backup-logs-input", Input)
            
            # Validate and parse
            error_messages = []
            
            # Parse max log size
            try:
                if max_log_size_input.value.strip():
                    max_log_size = float(max_log_size_input.value.strip())
                    if max_log_size < 1.0 or max_log_size > 100.0:
                        error_messages.append("Max log size must be between 1.0 and 100.0 MB")
                    else:
                        self.settings_manager.set_max_log_size_mb(max_log_size)
            except ValueError:
                error_messages.append("Max log size must be a valid number")
            
            # Parse max backup logs
            try:
                if max_backup_logs_input.value.strip():
                    max_backups = int(max_backup_logs_input.value.strip())
                    if max_backups < 1 or max_backups > 20:
                        error_messages.append("Max backups must be between 1 and 20")
                    else:
                        self.settings_manager.set_max_backup_logs(max_backups)
            except ValueError:
                error_messages.append("Max backups must be a valid integer")
            
            # If there are validation errors, show them and return
            if error_messages:
                status = self.app.query_one("#log-settings-status", Static)
                status.update("✗ " + "; ".join(error_messages))
                self.app.notify("✗ Validation errors: " + "; ".join(error_messages), severity="error")
                return
            
            # Update status
            status = self.app.query_one("#log-settings-status", Static)
            status.update(
                f"✓ Settings saved: Max size {self.settings_manager.get_max_log_size_mb()} MB, "
                f"Keep {self.settings_manager.get_max_backup_logs()} backups"
            )
            
            # Update display
            self.update_debug_log_info_display()
            
            self.app.notify("✓ Log settings saved successfully", severity="success")
            
        except Exception as e:
            self.app.notify(f"✗ Error saving log settings: {str(e)}", severity="error")
    
    def handle_refresh_log_info(self) -> None:
        """Handle refreshing log information display."""
        self.update_debug_log_info_display()
        notify_verbose(self.app, "Log information refreshed", severity="information")
    
    def handle_view_log_file(self) -> None:
        """Handle viewing the debug log file."""
        try:
            logger = get_logger()
            if logger:
                log_file_path = logger.get_log_file_path()
                import subprocess
                import sys
                
                # Try to open the log file with the default text editor
                if sys.platform == "darwin":  # macOS
                    subprocess.run(["open", "-t", log_file_path])  # nosec B603 - Controlled system command
                elif sys.platform == "win32":  # Windows
                    subprocess.run(["notepad", log_file_path])  # nosec B603 - Controlled system command
                else:  # Linux and others
                    subprocess.run(["xdg-open", log_file_path])  # nosec B603 - Controlled system command
                
                notify_verbose(self.app, f"Opened debug log file: {log_file_path}", severity="information")
            else:
                self.app.notify("Debug logging system not available", severity="error")
        except Exception as e:
            self.app.notify(f"Error opening log file: {str(e)}", severity="error")
    
    def update_unsubscribe_config_display(self) -> None:
        """Update the unsubscribe configuration display."""
        try:
            from textual.widgets import Input
            
            # Get current config
            encryption_key = settings.get('email.unsubscribe_encryption_key', '')
            base_url = settings.get('email.unsubscribe_base_url', '')
            endpoint_url = settings.get('email.unsubscribe_endpoint_url', '')
            mailto = settings.get('email.unsubscribe_mailto', '')
            
            has_key = bool(encryption_key)
            key_status = "✓ Set" if has_key else "✗ Not set"
            
            display_text = f"""Encryption Key: {key_status}
Base URL: {base_url if base_url else 'Not set'}
Endpoint URL: {endpoint_url if endpoint_url else 'Not set'}
Mailto: {mailto if mailto else 'Not set'}"""
            
            display_widget = self.app.query_one("#unsubscribe-config-display", Static)
            display_widget.update(display_text)
            
            # Update input fields with current values
            try:
                base_url_input = self.app.query_one("#unsub-base-url-input", Input)
                base_url_input.value = base_url
                
                endpoint_url_input = self.app.query_one("#unsub-endpoint-url-input", Input)
                endpoint_url_input.value = endpoint_url
                
                mailto_input = self.app.query_one("#unsub-mailto-input", Input)
                mailto_input.value = mailto
            except:
                pass  # Inputs might not be visible yet  # nosec B110 - Intentional - widget may not be mounted
            
        except Exception:
            # Widget not mounted yet (inside collapsed section), this is expected
            pass  # nosec B110 - Intentional - widget may not be mounted
    
    def handle_generate_unsub_key(self) -> None:
        """Generate a new encryption key for unsubscribe functionality."""
        try:
            from cryptography.fernet import Fernet
            
            # Generate new key
            key = Fernet.generate_key().decode('utf-8')
            
            # Save to settings (set() method automatically saves)
            self.settings_manager.settings.set('email.unsubscribe_encryption_key', key)
            
            # Update display
            self.update_unsubscribe_config_display()
            
            logger = get_logger()
            if logger:
                logger.info("Generated new unsubscribe encryption key")
            
            self.app.notify("✓ New encryption key generated successfully", severity="information")
            
        except Exception as e:
            logger = get_logger()
            if logger:
                logger.error(f"Error generating encryption key: {e}")
            self.app.notify(f"✗ Error generating key: {str(e)}", severity="error")
    
    def handle_save_unsub_settings(self) -> None:
        """Save unsubscribe settings from input fields."""
        try:
            from textual.widgets import Input
            
            # Get input values
            base_url = self.app.query_one("#unsub-base-url-input", Input).value.strip()
            endpoint_url = self.app.query_one("#unsub-endpoint-url-input", Input).value.strip()
            mailto = self.app.query_one("#unsub-mailto-input", Input).value.strip()
            
            # Validate inputs
            if not base_url:
                self.app.notify("✗ Base URL is required", severity="warning")
                return
            
            if not endpoint_url:
                self.app.notify("✗ Endpoint URL is required", severity="warning")
                return
            
            # Save settings (set() method automatically saves)
            self.settings_manager.settings.set('email.unsubscribe_base_url', base_url)
            self.settings_manager.settings.set('email.unsubscribe_endpoint_url', endpoint_url)
            if mailto:
                self.settings_manager.settings.set('email.unsubscribe_mailto', mailto)
            
            # Update display
            self.update_unsubscribe_config_display()
            
            logger = get_logger()
            if logger:
                logger.info("Saved unsubscribe settings")
            
            self.app.notify("✓ Unsubscribe settings saved successfully", severity="information")
            
        except Exception as e:
            logger = get_logger()
            if logger:
                logger.error(f"Error saving unsubscribe settings: {e}")
            self.app.notify(f"✗ Error saving settings: {str(e)}", severity="error")
    
    def update_email_settings_display(self) -> None:
        """Update the email settings display - populate config set dropdown."""
        try:
            from textual.widgets import Input
            
            # Get config set dropdown
            config_select = self.app.query_one("#default-config-set-select", Select)
            
            # Get config sets from cache or fetch if not cached
            config_sets = []
            if hasattr(self.app, 'ses_client') and self.app.ses_client:
                # Check cache first
                cached_data = self.cache_manager.get_cached_data("get_configuration_sets")
                
                if cached_data:
                    config_sets = cached_data
                else:
                    # Not in cache, fetch and cache it
                    config_sets = self.app.ses_client.get_configuration_sets()
                    if config_sets:
                        self.cache_manager.set_cached_data("get_configuration_sets", config_sets)
            
            # Get saved default
            saved_default = self.settings_manager.get_default_configuration_set()
            
            # Update dropdown options
            options = [("None", "")] + [(cs, cs) for cs in config_sets]
            config_select.set_options(options)
            
            # Set value to saved default if it exists
            if saved_default and saved_default in config_sets:
                config_select.value = saved_default
            
            # Update retry settings inputs
            try:
                max_retries_input = self.app.query_one("#max-retries-input", Input)
                max_retries_input.value = str(self.settings_manager.get_max_retries())
                
                base_delay_input = self.app.query_one("#base-retry-delay-input", Input)
                base_delay_input.value = str(self.settings_manager.get_base_retry_delay())
            except:
                pass  # Inputs might not be visible yet  # nosec B110 - Intentional - widget may not be mounted
            
        except Exception:
            # Widget not mounted yet (inside collapsed section), this is expected
            pass  # nosec B110 - Intentional - widget may not be mounted
    
    def handle_save_email_settings(self) -> None:
        """Handle saving email settings."""
        try:
            from textual.widgets import Input
            
            # Get selected config set value
            config_select = self.app.query_one("#default-config-set-select", Select)
            selected_config = config_select.value
            
            # Get retry settings
            max_retries_input = self.app.query_one("#max-retries-input", Input)
            base_delay_input = self.app.query_one("#base-retry-delay-input", Input)
            
            # Validate and parse retry settings
            error_messages = []
            
            # Parse max retries
            try:
                if max_retries_input.value.strip():
                    max_retries = int(max_retries_input.value.strip())
                    if max_retries < 0 or max_retries > 10:
                        error_messages.append("Max retries must be between 0 and 10")
                    else:
                        self.settings_manager.set_max_retries(max_retries)
            except ValueError:
                error_messages.append("Max retries must be a valid integer")
            
            # Parse base retry delay
            try:
                if base_delay_input.value.strip():
                    base_delay = float(base_delay_input.value.strip())
                    if base_delay < 0.1 or base_delay > 10.0:
                        error_messages.append("Base retry delay must be between 0.1 and 10.0 seconds")
                    else:
                        self.settings_manager.set_base_retry_delay(base_delay)
            except ValueError:
                error_messages.append("Base retry delay must be a valid number")
            
            # If there are validation errors, show them and return
            if error_messages:
                self.app.notify("✗ Validation errors: " + "; ".join(error_messages), severity="error")
                return
            
            # Save config set setting
            self.settings_manager.set_default_configuration_set(selected_config if selected_config else "")
            
            # Update status
            status = self.app.query_one("#email-settings-status", Static)
            status_parts = []
            
            if selected_config:
                status_parts.append(f"Config set: {selected_config}")
            else:
                status_parts.append("No default config set")
            
            status_parts.append(f"Max retries: {self.settings_manager.get_max_retries()}")
            status_parts.append(f"Base delay: {self.settings_manager.get_base_retry_delay()}s")
            
            status.update("✓ " + " | ".join(status_parts))
            
            self.app.notify("✓ Email settings saved successfully", severity="success")
            
        except Exception as e:
            self.app.notify(f"✗ Error saving email settings: {str(e)}", severity="error")
