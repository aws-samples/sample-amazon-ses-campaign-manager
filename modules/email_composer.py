#!/usr/bin/env python3
"""
Email Composer Module
Comprehensive email composition interface for single and bulk email sending with unsubscribe support and progress tracking
"""

import json
import asyncio
from typing import Dict, List, Any
from pathlib import Path

from textual.containers import Container, Horizontal, ScrollableContainer
from textual.widgets import (
    Button, Input, TextArea, Select, Label, Static, 
    ProgressBar, Collapsible
)
from datetime import datetime

from config.settings import settings
from modules.bulk_email_sender import BulkEmailSender
from modules.unsubscribe_handler import UnsubscribeHandler
from modules.csv_validator import CSVValidator
from modules.logger import get_logger
from modules.notification_helper import notify_verbose, notify_always


def generate_campaign_id() -> str:
    """
    Generate a unique campaign ID using timestamp and random suffix.
    
    Format: {unix_timestamp}-{4_char_random}
    Example: 1736605200-a7f3
    
    Returns:
        Unique campaign ID string
    """
    import secrets
    timestamp = int(datetime.now().timestamp())
    random_suffix = secrets.token_hex(2)  # 2 bytes = 4 hex characters
    return f"{timestamp}-{random_suffix}"


class EmailComposer:
    """Email composer with single/bulk modes and unsubscribe support."""
    
    def __init__(self, app, ses_client, email_sender, templates_manager):
        self.app = app
        self.ses_client = ses_client
        self.email_sender = email_sender
        self.templates_manager = templates_manager
        self.unsubscribe_handler = UnsubscribeHandler()
        self.bulk_sender = BulkEmailSender(ses_client)
        
        # Get logger instance
        logger = get_logger()
        self.csv_validator = CSVValidator(logger=logger, ses_client=ses_client)
        
        self.identities = []
        self.configuration_sets = []
        self.current_mode = 'single'  # 'single' or 'bulk'
        self.max_send_rate = 100  # Default max rate, will be updated from account details
        
        # Bulk sending state
        self.bulk_sending = False
        self.bulk_paused = False
        self.bulk_cancelled = False
        self.bulk_results = []
        self.bulk_csv_path = None
        self.pause_event = asyncio.Event()
        self.pause_event.set()  # Start in unpaused state
        
        # Time tracking for bulk sends
        self.bulk_start_time = None
        self.bulk_end_time = None
        self.bulk_pause_start_time = None
        self.bulk_total_paused_seconds = 0
    
    def create_form_content(self, templates: List[str], identities: List[str], configuration_sets: List[str]):
        """Create the enhanced form content with mode selection."""
        self.identities = identities
        self.configuration_sets = configuration_sets
        
        # Check if scheduled campaigns is deployed
        scheduled_deployed = False
        if hasattr(self.app, 'scheduled_campaigns_manager'):
            scheduled_deployed = self.app.scheduled_campaigns_manager.is_deployed
        
        # Build mode options
        mode_options = [
            ("Single Email", "single"),
            ("Bulk Email (CSV)", "bulk")
        ]
        
        if scheduled_deployed:
            mode_options.append(("Scheduled Campaign", "scheduled"))
        
        # Create all widgets as children of ScrollableContainer
        return [ScrollableContainer(
            # Header
            Label("📧 Send Email", classes="form-section-title"),
            
            # Mode Selection
            Horizontal(
                Label("Mode:", classes="form-label"),
                Select(
                    mode_options,
                    id="email-mode-selector",
                    classes="form-select",
                    value="single"
                ),
                classes="form-row"
            ),
            
            # Common Fields Section
            Label("📝 Email Configuration", classes="form-subsection-title"),
            
            # From Identity
            Horizontal(
                Label("From Identity:", classes="form-label"),
                Select(
                    [(identity, identity) for identity in identities] if identities else [("No identities", "")],
                    id="from-identity-enhanced",
                    classes="form-select"
                ),
                classes="form-row"
            ),
            
            # Custom email for domain identities
            Horizontal(
                Label("From Email:", id="custom-email-label-enhanced", classes="form-label"),
                Input(
                    placeholder="username (for domain identities)",
                    id="custom-email-enhanced",
                    classes="form-input"
                ),
                classes="form-row",
                id="custom-email-row-enhanced"
            ),
            
            # Template Selection
            Horizontal(
                Label("Email Template:", classes="form-label"),
                Select(
                    [(t.get('TemplateName', ''), t.get('TemplateName', '')) for t in templates] if templates else [("No templates", "")],
                    id="email-template-enhanced",
                    classes="form-select"
                ),
                classes="form-row"
            ),
            
            # Single Email Fields (shown when mode is single)
            Container(
                Label("👤 Single Email Recipients", classes="form-subsection-title"),
                
                Horizontal(
                    Label("To:", classes="form-label"),
                    Input(
                        placeholder="recipient@example.com",
                        id="to-email-single",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Horizontal(
                    Label("Template Data (JSON):", classes="form-label"),
                    TextArea(
                        '{"name": "John", "company": "Example Corp"}',
                        id="template-data-single",
                        classes="form-textarea"
                    ),
                    classes="form-row"
                ),
                
                id="single-email-fields",
                classes="mode-fields"
            ),
            
            # Bulk Email Fields (shown when mode is bulk)
            Container(
                Label("📊 Bulk Email Configuration", classes="form-subsection-title"),
                
                Horizontal(
                    Label("CSV File Path:", classes="form-label"),
                    Input(
                        placeholder="/path/to/recipients.csv",
                        id="csv-file-path",
                        classes="form-input"
                    ),
                    Button("Browse", id="browse-csv-btn", variant="default"),
                    Button("View Report", id="view-csv-report-btn", variant="default", disabled=True),
                    classes="form-row"
                ),
                
                Label(
                    "CSV Format: To_Address, sub_name, sub_company, etc.",
                    classes="form-help-text"
                ),
                
                Horizontal(
                    Label("Campaign Name:", classes="form-label"),
                    Input(
                        placeholder="e.g., newsletter, promo2024",
                        id="campaign-name-bulk",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Label(
                    "Campaign name will be added as 'campaign' tag (spaces/special chars auto-converted to underscores)",
                    classes="form-help-text"
                ),
                
                Horizontal(
                    Label("Campaign Description:", classes="form-label"),
                    Input(
                        placeholder="Optional description for this campaign",
                        id="campaign-description-bulk",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Horizontal(
                    Label("Campaign Creator:", classes="form-label"),
                    Input(
                        placeholder="Optional creator name/email",
                        id="campaign-creator-bulk",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                id="bulk-email-fields",
                classes="mode-fields"
            ),
            
            # Scheduled Campaign Fields (only schedule time, reuses bulk fields above)
            Container(
                Label("📅 Schedule Configuration", classes="form-subsection-title"),
                
                Horizontal(
                    Label("Schedule Date & Time:", classes="form-label"),
                    Input(
                        placeholder="YYYY-MM-DD HH:MM (24-hour format, e.g., 2025-12-25 14:30)",
                        id="schedule-datetime",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Label(
                    "⚠️ Must be a future date/time. Campaign will execute automatically at this time.",
                    classes="form-help-text"
                ),
                
                Label(
                    "ℹ️ Sending Rate (TPS): Configured in AWS Lambda settings for 'EmailSender' function.\n"
                    "Default: 1 TPS. To change: AWS Console → Lambda → EmailSender → Configuration → Concurrency.\n"
                    "Formula: Reserved Concurrency × 20 = Target TPS (e.g., 5 concurrency = 100 TPS)",
                    classes="form-help-text",
                    id="scheduled-tps-info"
                ),
                
                id="scheduled-campaign-fields",
                classes="mode-fields"
            ),
            
            # Optional Fields (collapsible) - Common to both modes
            Collapsible(
                Horizontal(
                    Label("CC:", classes="form-label"),
                    Input(
                        placeholder="cc1@example.com, cc2@example.com",
                        id="cc-emails-enhanced",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Horizontal(
                    Label("BCC:", classes="form-label"),
                    Input(
                        placeholder="bcc1@example.com, bcc2@example.com",
                        id="bcc-emails-enhanced",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Horizontal(
                    Label("Configuration Set:", classes="form-label"),
                    Select(
                        [("None", "")] + [(cs, cs) for cs in configuration_sets] if configuration_sets else [("None", "")],
                        id="config-set-enhanced",
                        classes="form-select",
                        value=self._get_default_config_set(configuration_sets)
                    ),
                    classes="form-row"
                ),
                
                Horizontal(
                    Label("SES Tags (JSON):", classes="form-label"),
                    TextArea(
                        '{"BusinessUnit": "Marketing_UK"}',
                        id="ses-tags-enhanced",
                        classes="form-textarea"
                    ),
                    classes="form-row"
                ),
                
                Label(
                    "Note: SES tags 'campaign_id' and 'campaign_name' are reserved and auto-added by the system.",
                    classes="form-help-text"
                ),
                
                title="⚙️ Optional Settings",
                collapsed=True,
                id="optional-settings-collapsible"
            ),
            
            # Bulk-only Optional Fields (collapsible)
            Collapsible(
                Horizontal(
                    Label("Base Template Data (JSON):", classes="form-label"),
                    TextArea(
                        '{}',
                        id="template-data-bulk",
                        classes="form-textarea"
                    ),
                    classes="form-row"
                ),
                
                Label(
                    "Base data will be merged with CSV substitutions (optional)",
                    classes="form-help-text"
                ),
                
                Horizontal(
                    Label("Emails Per Second:", classes="form-label"),
                    Input(
                        placeholder="Auto (uses account max)",
                        id="emails-per-second",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Label(
                    "Leave empty for automatic rate based on your Amazon SES MaxSendRate.\n"
                    "ℹ️ NOTE: Configured rate controls concurrent tasks, not exact TPS. Due to async efficiency,\n"
                    "actual sending rate will be ~10-15% higher (e.g., rate=90 → actual ~100-105/sec).\n"
                    "RECOMMENDED: Set to 85-90% of MaxSendRate to account for this boost and prevent throttling.\n"
                    "Monitor '⚠️ Throttled' counter during send - if throttling occurs, reduce rate by 10-20%.",
                    classes="form-help-text",
                    id="rate-limit-help"
                ),
                
                title="⚙️ Bulk Email Settings",
                collapsed=True,
                id="bulk-optional-settings-collapsible",
                classes="mode-fields"
            ),
            
            # Unsubscribe Settings (collapsible)
            Collapsible(
                Horizontal(
                    Label("Unsubscribe Type:", classes="form-label"),
                    Select(
                        [
                            ("None", "none"),
                            ("Link in Email", "link"),
                            ("List-Unsubscribe Headers", "headers"),
                            ("Both", "both")
                        ],
                        id="unsubscribe-type-select",
                        classes="form-select",
                        value="both"
                    ),
                    classes="form-row"
                ),
                
                Horizontal(
                    Label("Unsubscribe Category:", classes="form-label"),
                    Input(
                        placeholder="e.g., newsletter, promotions (optional)",
                        id="campaign-topic",
                        classes="form-input"
                    ),
                    classes="form-row"
                ),
                
                Label(
                    "⚠️ Configure unsubscribe URLs and encryption key in Settings",
                    classes="form-warning-text"
                ),
                
                Label(
                    "ℹ️ Category helps track which type of emails users unsubscribe from. 'Link in Email' adds {{unsubscribe_link}} to template data. 'Headers' adds List-Unsubscribe headers. 'Both' does both.",
                    classes="form-help-text"
                ),
                
                title="🔗 Unsubscribe Settings",
                collapsed=True,
                id="unsubscribe-settings-collapsible"
            ),
            
            # Bulk Progress Section (shown only in bulk mode, at the bottom)
            Container(
                Label("📈 Sending Progress", classes="form-subsection-title"),
                ProgressBar(
                    total=100,
                    show_eta=False,
                    show_percentage=True,
                    id="bulk-progress-bar"
                ),
                Label("Ready to send", id="bulk-progress-label", classes="progress-label"),
                Static("", id="bulk-time-display", classes="stats-display"),
                Static("", id="bulk-stats-display", classes="stats-display"),
                Container(
                    Horizontal(
                        Button("📧 Send Email", variant="primary", id="send-email-enhanced-btn-bulk"),
                        Button("⏸️ Pause", variant="warning", id="pause-bulk-btn", disabled=True),
                        Button("🛑 Cancel", variant="error", id="cancel-bulk-btn", disabled=True),
                        classes="bulk-control-buttons"
                    ),
                    classes="bulk-control-container"
                ),
                id="bulk-progress-section",
                classes="mode-fields"
            ),
            
            # Action Buttons (for single mode and form management)
            Horizontal(
                Button("📧 Send Email", variant="primary", id="send-email-enhanced-btn"),
                Button("🗑️ Clear Form", variant="default", id="clear-form-enhanced-btn"),
                Button("🔄 Refresh Data", variant="default", id="refresh-data-enhanced-btn"),
                classes="form-actions"
            ),
            
            # Status/Results Section (shown only in single mode)
            Container(
                Label("📋 Email Log", classes="form-section-title"),
                ScrollableContainer(
                    Static("", id="email-log-enhanced", classes="email-log"),
                    id="email-log-container"
                ),
                id="email-log-section",
                classes="mode-fields"
            ),
            
            id="send-email-enhanced-container"
        )]
    
    def update_mode_visibility(self, mode: str) -> None:
        """Update form visibility based on selected mode."""
        self.current_mode = mode
        
        try:
            single_fields = self.app.query_one("#single-email-fields")
            bulk_fields = self.app.query_one("#bulk-email-fields")
            scheduled_fields = self.app.query_one("#scheduled-campaign-fields")
            email_log_section = self.app.query_one("#email-log-section")
            bulk_progress_section = self.app.query_one("#bulk-progress-section")
            bulk_optional_settings = self.app.query_one("#bulk-optional-settings-collapsible")
            
            # Get the action buttons container (contains single mode send button)
            try:
                action_buttons = self.app.query_one(".form-actions")
            except:
                action_buttons = None
            
            if mode == 'single':
                single_fields.styles.display = "block"
                bulk_fields.styles.display = "none"
                scheduled_fields.styles.display = "none"
                email_log_section.styles.display = "block"
                bulk_progress_section.styles.display = "none"
                bulk_optional_settings.styles.display = "none"
                if action_buttons:
                    action_buttons.styles.display = "block"
            elif mode == 'bulk':
                single_fields.styles.display = "none"
                bulk_fields.styles.display = "block"
                scheduled_fields.styles.display = "none"
                email_log_section.styles.display = "none"
                bulk_progress_section.styles.display = "block"
                bulk_optional_settings.styles.display = "block"
                if action_buttons:
                    action_buttons.styles.display = "none"
            elif mode == 'scheduled':
                # Scheduled mode shows bulk fields (CSV, campaign info) + schedule field
                single_fields.styles.display = "none"
                bulk_fields.styles.display = "block"
                scheduled_fields.styles.display = "block"  # Show schedule datetime field
                email_log_section.styles.display = "none"
                bulk_progress_section.styles.display = "none"  # Hide progress, scheduled campaigns run in cloud
                bulk_optional_settings.styles.display = "none"  # Hide rate limit (configured in Lambda, not per-campaign)
                if action_buttons:
                    action_buttons.styles.display = "block"  # Show send button (will schedule, not send immediately)
            
            # Update custom email field visibility
            self.update_custom_email_visibility()
            
        except Exception as e:
            pass  # Widgets might not be ready yet  # nosec B110 - Intentional - widget may not be mounted
    
    def update_custom_email_visibility(self) -> None:
        """Update custom email field visibility based on identity type."""
        try:
            identity = self.app.query_one("#from-identity-enhanced", Select).value
            custom_email_row = self.app.query_one("#custom-email-row-enhanced")
            
            if identity and '@' not in identity:  # Domain identity
                custom_email_row.display = True
            else:  # Email identity
                custom_email_row.display = False
                
        except Exception:
            pass  # nosec B110 - Intentional - widget may not be mounted
    
    def update_rate_limit_help_text(self, max_rate: int) -> None:
        """Update the rate limit help text with the actual max rate."""
        try:
            rate_help = self.app.query_one("#rate-limit-help", Label)
            rate_help.update(f"Leave empty for automatic rate. Enter 0-{max_rate} to set custom rate.")
            self.max_send_rate = max_rate
        except Exception:
            pass  # nosec B110 - Intentional - widget may not be mounted
    
    def handle_template_selection(self, template_name: str) -> None:
        """Handle template selection and populate template data field with substitution keys."""
        try:
            if not template_name or not self.ses_client:
                return
            
            # Extract placeholders from the selected template using SES client method
            placeholders = self.ses_client.extract_template_placeholders(template_name)
            
            # Update the template data field for single email mode
            try:
                template_data_field = self.app.query_one("#template-data-single", TextArea)
                if placeholders:
                    # Filter out unsubscribe_link as it's automatically added by the system
                    filtered_placeholders = {k: v for k, v in placeholders.items() if k != 'unsubscribe_link'}
                    
                    if filtered_placeholders:
                        # Create JSON with filtered placeholders
                        template_json = json.dumps(filtered_placeholders, indent=2)
                        template_data_field.text = template_json
                        self.app.notify(
                            f"Auto-populated {len(filtered_placeholders)} template placeholders",
                            severity="information"
                        )
                    else:
                        # No placeholders found after filtering, use empty JSON
                        template_data_field.text = '{}'
                        self.app.notify("No template placeholders found", severity="information")
                else:
                    # No placeholders found, use empty JSON
                    template_data_field.text = '{}'
                    self.app.notify("No template placeholders found", severity="information")
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
        except Exception as e:
            # If extraction fails, just show a warning but don't break the UI
            self.app.notify(f"Could not extract template placeholders: {str(e)}", severity="warning")
            logger = get_logger()
            if logger:
                logger.error(f"Error handling template selection: {str(e)}")
    
    async def handle_mode_change(self, mode: str) -> None:
        """Handle email mode change."""
        self.update_mode_visibility(mode)
        notify_verbose(self.app, f"Switched to {mode} email mode", severity="information")
    
    async def handle_send_email(self) -> None:
        """Handle send email button click."""
        if self.current_mode == 'single':
            await self.send_single_email()
        elif self.current_mode == 'bulk':
            await self.send_bulk_email()
        elif self.current_mode == 'scheduled':
            await self.schedule_campaign()
    
    async def send_single_email(self) -> None:
        """Send a single email."""
        try:
            # Get form values
            from_identity = self.app.query_one("#from-identity-enhanced", Select).value
            template_name = self.app.query_one("#email-template-enhanced", Select).value
            to_email = self.app.query_one("#to-email-single", Input).value.strip()
            template_data_text = self.app.query_one("#template-data-single", TextArea).text.strip()
            cc_emails = self.app.query_one("#cc-emails-enhanced", Input).value.strip()
            bcc_emails = self.app.query_one("#bcc-emails-enhanced", Input).value.strip()
            config_set = self.app.query_one("#config-set-enhanced", Select).value
            ses_tags_text = self.app.query_one("#ses-tags-enhanced", TextArea).text.strip()
            
            # Validation
            if not from_identity or not template_name or not to_email:
                self.app.notify("From identity, template, and to email are required", severity="error")
                return
            
            # Construct from email
            from_email = self._construct_from_email()
            if not from_email or '@' not in from_email:
                self.app.notify("Invalid from email address", severity="error")
                return
            
            # Parse template data
            template_data = json.loads(template_data_text) if template_data_text else {}
            
            # Get unsubscribe type and topic from form
            unsub_type = self.app.query_one("#unsubscribe-type-select", Select).value
            campaign_topic = self.app.query_one("#campaign-topic", Input).value.strip()
            
            # Add unsubscribe if not "none"
            if unsub_type and unsub_type != "none":
                template_data = self.unsubscribe_handler.add_unsubscribe_to_template_data(
                    template_data, to_email, unsub_type, campaign_topic if campaign_topic else None
                )
            
            # Parse email lists
            to_list = [to_email]
            cc_list = [e.strip() for e in cc_emails.split(",") if e.strip()]
            bcc_list = [e.strip() for e in bcc_emails.split(",") if e.strip()]
            
            # Parse SES tags
            ses_tags = json.loads(ses_tags_text) if ses_tags_text else {}
            
            # Handle configuration set - convert Select.BLANK to None
            from textual.widgets import Select as TextualSelect
            final_config_set = None
            if config_set and config_set != "" and str(config_set) != "Select.BLANK":
                final_config_set = config_set
            
            # Prepare email data
            email_data = {
                "from_email": from_email,
                "to_emails": to_list,
                "template_name": template_name,
                "template_data": template_data,
                "cc_emails": cc_list,
                "bcc_emails": bcc_list,
                "ses_tags": ses_tags,
                "configuration_set": final_config_set,
                "email_type": "template"
            }
            
            # Add List-Unsubscribe headers if type is headers or both
            if unsub_type in ['headers', 'both']:
                campaign_topic = self.app.query_one("#campaign-topic", Input).value.strip()
                headers = self.unsubscribe_handler.generate_list_unsubscribe_headers(
                    to_email, campaign_topic if campaign_topic else None
                )
                if headers:
                    email_data['email_headers'] = headers
            
            # Disable send button
            send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
            send_btn.disabled = True
            send_btn.label = "Sending..."
            
            self.app.notify("Sending email...", severity="information")
            
            # Send email with log widget
            email_log = self.app.query_one("#email-log-enhanced", Static)
            await self.email_sender.send_email_worker(email_data, email_log, self.app.notify)
            
            # Re-enable button
            send_btn.disabled = False
            send_btn.label = "📧 Send Email"
            
        except json.JSONDecodeError as e:
            self.app.notify(f"Invalid JSON: {str(e)}", severity="error")
            send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
            send_btn.disabled = False
            send_btn.label = "📧 Send Email"
        except Exception as e:
            self.app.notify(f"Error: {str(e)}", severity="error")
            send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
            send_btn.disabled = False
            send_btn.label = "📧 Send Email"
    
    async def send_bulk_email(self) -> None:
        """Send bulk emails from CSV."""
        try:
            # Get CSV path
            csv_path = self.app.query_one("#csv-file-path", Input).value.strip()
            if not csv_path:
                self.app.notify("CSV file path is required", severity="error")
                return
            
            # Get template name for validation
            template_name = self.app.query_one("#email-template-enhanced", Select).value
            
            # Validate CSV file first (including template variable matching)
            self.app.notify("Validating CSV file...", severity="information")
            validation_result = self.csv_validator.validate_csv_file(
                csv_path=csv_path,
                check_duplicates=True,
                max_rows=50000,
                template_name=template_name if template_name != Select.BLANK else None
            )
            
            if not validation_result.is_valid:
                # Show validation errors
                error_count = len(validation_result.errors)
                error_preview = "\n".join(validation_result.errors[:5])
                if error_count > 5:
                    error_preview += f"\n... and {error_count - 5} more error(s)"
                
                self.app.notify(
                    f"CSV validation failed with {error_count} error(s):\n{error_preview}",
                    severity="error",
                    timeout=10
                )
                return
            
            # Show validation warnings if any
            if validation_result.warnings:
                warning_count = len(validation_result.warnings)
                warning_preview = "\n".join(validation_result.warnings[:3])
                if warning_count > 3:
                    warning_preview += f"\n... and {warning_count - 3} more warning(s)"
                
                self.app.notify(
                    f"CSV validation warnings:\n{warning_preview}",
                    severity="warning",
                    timeout=8
                )
            
            # Show validation success
            self.app.notify(
                f"✓ CSV validated: {validation_result.valid_row_count} valid recipients",
                severity="success"
            )
            
            # Parse CSV
            recipients, error = self.bulk_sender.parse_csv_file(csv_path)
            if error:
                self.app.notify(error, severity="error")
                return
            
            # Get form values
            from_identity = self.app.query_one("#from-identity-enhanced", Select).value
            template_name = self.app.query_one("#email-template-enhanced", Select).value
            base_template_data_text = self.app.query_one("#template-data-bulk", TextArea).text.strip()
            campaign_name = self.app.query_one("#campaign-name-bulk", Input).value.strip()
            cc_emails = self.app.query_one("#cc-emails-enhanced", Input).value.strip()
            bcc_emails = self.app.query_one("#bcc-emails-enhanced", Input).value.strip()
            config_set = self.app.query_one("#config-set-enhanced", Select).value
            ses_tags_text = self.app.query_one("#ses-tags-enhanced", TextArea).text.strip()
            custom_rate_text = self.app.query_one("#emails-per-second", Input).value.strip()
            
            # Validation
            if not from_identity or not template_name:
                self.app.notify("From identity and template are required", severity="error")
                return
            
            # Construct from email
            from_email = self._construct_from_email()
            if not from_email or '@' not in from_email:
                self.app.notify("Invalid from email address", severity="error")
                return
            
            # Parse data
            base_template_data = json.loads(base_template_data_text) if base_template_data_text else {}
            cc_list = [e.strip() for e in cc_emails.split(",") if e.strip()]
            bcc_list = [e.strip() for e in bcc_emails.split(",") if e.strip()]
            ses_tags = json.loads(ses_tags_text) if ses_tags_text else {}
            
            # Generate campaign ID and add to SES tags if campaign name is provided
            campaign_id = None
            if campaign_name:
                # Generate unique campaign ID
                campaign_id = generate_campaign_id()
                
                # Sanitize campaign name for SES tag requirements
                # SES tags only allow alphanumeric ASCII, '_', '-', '.', '@'
                import re
                sanitized_campaign = re.sub(r'[^a-zA-Z0-9_\-\.@]', '_', campaign_name)
                
                # Add both campaign_id and campaign_name as SES tags
                ses_tags['campaign_id'] = campaign_id
                ses_tags['campaign_name'] = sanitized_campaign
                
                # Notify user with campaign ID
                self.app.notify(
                    f"Campaign ID generated: {campaign_id}",
                    severity="information"
                )
                
                # Notify user if campaign name was sanitized
                if sanitized_campaign != campaign_name:
                    self.app.notify(
                        f"Campaign name sanitized for SES tags: '{campaign_name}' → '{sanitized_campaign}'",
                        severity="information"
                    )
            
            # Handle configuration set - convert Select.BLANK to None
            from textual.widgets import Select as TextualSelect
            final_config_set = None
            if config_set and config_set != "" and str(config_set) != "Select.BLANK":
                final_config_set = config_set
            
            # Get unsubscribe settings from dropdown
            unsub_type = self.app.query_one("#unsubscribe-type-select", Select).value
            campaign_topic = self.app.query_one("#campaign-topic", Input).value.strip()
            
            # Prepare email config
            email_config = {
                'from_email': from_email,
                'template_name': template_name,
                'base_template_data': base_template_data,
                'cc_emails': cc_list,
                'bcc_emails': bcc_list,
                'ses_tags': ses_tags,
                'configuration_set': final_config_set,
                'enable_unsubscribe': unsub_type != "none",
                'unsubscribe_type': unsub_type if unsub_type != "none" else None,
                'campaign_topic': campaign_topic if unsub_type != "none" else None
            }
            
            # Set sending rate from SES limits or custom rate
            sending_rate = 1  # Default to 1 email/second
            max_account_rate = 1
            
            try:
                # Try to get the max rate from cached account details
                from modules.cache_manager import CacheManager
                
                cache_manager = CacheManager(settings)
                cached_account = cache_manager.get_cached_data("get_account_details")
                
                # If no cache exists, fetch fresh account details
                if not cached_account and self.ses_client:
                    try:
                        account_response = self.ses_client.ses_client.get_account()
                        cached_account = account_response
                        # Cache it for future use
                        cache_manager.set_cached_data("get_account_details", cached_account)
                    except Exception:
                        pass  # nosec B110 - Intentional - widget may not be mounted
                
                # Extract MaxSendRate from cached or fresh data
                if cached_account and 'SendQuota' in cached_account:
                    max_rate = cached_account['SendQuota'].get('MaxSendRate')
                    if isinstance(max_rate, (int, float)) and max_rate > 0:
                        max_account_rate = int(max_rate)
                        sending_rate = max_account_rate  # Use max by default
                        
                        # Update the UI help text with actual max rate
                        self.update_rate_limit_help_text(max_account_rate)
                        
                        logger = get_logger()
                        if logger:
                            logger.debug(f"Retrieved MaxSendRate from account details: {max_account_rate} emails/second", "SEND_EMAIL_FORM")
                
                # Check if user specified a custom rate
                if custom_rate_text:
                    try:
                        custom_rate = int(custom_rate_text)
                        if 0 <= custom_rate <= max_account_rate:
                            sending_rate = custom_rate
                            logger = get_logger()
                            if logger:
                                logger.debug(f"Using custom sending rate: {sending_rate} emails/second", "SEND_EMAIL_FORM")
                        else:
                            self.app.notify(f"Custom rate must be between 0 and {max_account_rate}. Using max rate.", severity="warning")
                    except ValueError:
                        self.app.notify(f"Invalid rate value. Using automatic rate ({max_account_rate}/sec).", severity="warning")
                        
            except Exception as e:
                logger = get_logger()
                if logger:
                    logger.debug(f"Failed to retrieve MaxSendRate, using default: {str(e)}", "SEND_EMAIL_FORM")
            
            self.bulk_sender.set_sending_rate(sending_rate)
            
            # Reset state and time tracking
            self.bulk_cancelled = False
            self.bulk_paused = False
            self.pause_event.set()
            self.bulk_start_time = None
            self.bulk_end_time = None
            self.bulk_pause_start_time = None
            self.bulk_total_paused_seconds = 0
            
            # Disable send button, enable control buttons
            send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
            pause_btn = self.app.query_one("#pause-bulk-btn", Button)
            cancel_btn = self.app.query_one("#cancel-bulk-btn", Button)
            
            send_btn.disabled = True
            send_btn.label = "Sending Bulk..."
            pause_btn.disabled = False
            cancel_btn.disabled = False
            
            # Initialize progress
            progress_bar = self.app.query_one("#bulk-progress-bar", ProgressBar)
            progress_label = self.app.query_one("#bulk-progress-label", Label)
            stats_display = self.app.query_one("#bulk-stats-display", Static)
            
            progress_bar.update(total=len(recipients), progress=0)
            progress_label.update(f"Sending to {len(recipients)} recipients...")
            
            self.bulk_sending = True
            self.bulk_results = []
            
            # Initialize time tracking
            self.bulk_start_time = datetime.now()
            self.bulk_end_time = None
            self.bulk_total_paused_seconds = 0
            
            # Send emails using bulk_email_sender with pause/cancel support
            self.app.notify(f"Starting bulk send to {len(recipients)} recipients at {sending_rate} emails/second...", severity="information")
            
            # Create progress callback to update UI
            async def progress_callback(result):
                # Check if cancelled or paused
                if self.bulk_cancelled:
                    return
                await self.pause_event.wait()
                
                # Add result to our list
                self.bulk_results.append(result)
                
                # Update progress
                progress_bar.update(progress=len(self.bulk_results))
                
                # Calculate elapsed time, rate, and average API duration
                time_display = self.app.query_one("#bulk-time-display", Static)
                elapsed_seconds = self._get_elapsed_seconds()
                elapsed_str = self._format_elapsed_time(elapsed_seconds)
                emails_per_second = len(self.bulk_results) / elapsed_seconds if elapsed_seconds > 0 else 0
                
                # Calculate average API duration from successful sends
                api_durations = [r.get('api_duration_ms', 0) for r in self.bulk_results if r.get('status') == 'success' and r.get('api_duration_ms', 0) > 0]
                avg_api_duration = sum(api_durations) / len(api_durations) if api_durations else 0
                
                time_display.update(
                    f"⏱️ Elapsed: {elapsed_str} | 📧 Rate: {emails_per_second:.2f} emails/sec | 🔌 Avg API: {avg_api_duration:.0f}ms"
                )
                
                # Update stats with throttling info
                success = sum(1 for r in self.bulk_results if r.get('status') == 'success')
                failed = len(self.bulk_results) - success
                throttled = sum(1 for r in self.bulk_results if r.get('throttled', False))
                retries = sum(r.get('retries', 0) for r in self.bulk_results)
                
                stats_parts = [
                    f"✅ Success: {success}",
                    f"❌ Failed: {failed}",
                    f"📊 Total: {len(self.bulk_results)}/{len(recipients)}"
                ]
                
                # Add throttling info if any throttling occurred
                if throttled > 0:
                    stats_parts.append(f"⚠️ Throttled: {throttled}")
                if retries > 0:
                    stats_parts.append(f"🔄 Retries: {retries}")
                
                stats_display.update(" | ".join(stats_parts))
                
                # Update label (preserve PAUSED status if paused)
                if not self.bulk_paused:
                    progress_label.update(f"Sent {len(self.bulk_results)}/{len(recipients)} emails...")
            
            # Send all emails using bulk_sender (it handles rate limiting internally)
            results = await self.bulk_sender.send_bulk_emails(recipients, email_config, progress_callback)
            
            # Set end time
            self.bulk_end_time = datetime.now()
            
            # Write results to CSV
            results_path = self.bulk_sender.write_results_to_csv(csv_path, results)
            
            # Get stats
            stats = self.bulk_sender.get_sending_stats(results)
            
            # Update final time display
            time_display = self.app.query_one("#bulk-time-display", Static)
            elapsed_seconds = self._get_elapsed_seconds()
            elapsed_str = self._format_elapsed_time(elapsed_seconds)
            emails_per_second = len(results) / elapsed_seconds if elapsed_seconds > 0 else 0
            
            time_display.update(
                f"⏱️ Total Time: {elapsed_str} | 📧 Average Rate: {emails_per_second:.2f} emails/sec"
            )
            
            # Store campaign metadata in DynamoDB if available and campaign was created
            if campaign_id and campaign_name:
                try:
                    # Get optional metadata fields from form
                    campaign_description = self.app.query_one("#campaign-description-bulk", Input).value.strip()
                    campaign_creator = self.app.query_one("#campaign-creator-bulk", Input).value.strip()
                    
                    # Check if analytics manager has metadata manager
                    if hasattr(self.app, 'campaign_analytics_manager') and self.app.campaign_analytics_manager:
                        metadata_mgr = self.app.campaign_analytics_manager.metadata_manager
                        if metadata_mgr and metadata_mgr.enabled:
                            # Store metadata with all available information
                            metadata_mgr.store_metadata(
                                campaign_id=campaign_id,
                                campaign_name=campaign_name,
                                template_name=template_name,
                                from_address=from_email,
                                description=campaign_description if campaign_description else None,
                                creator=campaign_creator if campaign_creator else None,
                                configuration_set=final_config_set if final_config_set else None,
                                total_recipients=len(recipients),
                                success_count=stats['success'],
                                failed_count=stats['failed'],
                                success_rate=stats['success_rate'],
                                schedule='immediate'  # Bulk CSV sends immediately (not scheduled)
                            )
                            
                            logger = get_logger()
                            if logger:
                                logger.info(f"Stored campaign metadata for: {campaign_id}", "BULK_SENDER")
                except Exception as e:
                    # Don't fail the bulk send if metadata storage fails
                    logger = get_logger()
                    if logger:
                        logger.warning(f"Could not store campaign metadata: {str(e)}", "BULK_SENDER")
            
            # Update UI based on completion status
            if self.bulk_cancelled:
                progress_label.update(f"🛑 Cancelled! Partial results saved to: {Path(results_path).name}")
                
                # Show clear cancellation confirmation
                self.app.notify(
                    f"Campaign cancelled: {stats['success']} emails sent before cancellation. Results saved.",
                    severity="warning"
                )
            else:
                progress_label.update(f"✅ Completed! Results saved to: {Path(results_path).name}")
                
                # Build completion message with throttling info
                completion_msg = f"Bulk send completed: {stats['success']} succeeded, {stats['failed']} failed"
                
                # Add throttling info if any
                throttled_count = sum(1 for r in results if r.get('throttled', False))
                total_retries = sum(r.get('retries', 0) for r in results)
                
                if throttled_count > 0:
                    completion_msg += f", {throttled_count} emails encountered throttling"
                if total_retries > 0:
                    completion_msg += f" ({total_retries} total retries)"
                
                self.app.notify(
                    completion_msg,
                    severity="success" if stats['failed'] == 0 else "warning"
                )
            
            # Re-enable send button, disable control buttons
            send_btn.disabled = False
            send_btn.label = "📧 Send Email"
            pause_btn.disabled = True
            pause_btn.label = "⏸️ Pause"  # Reset label
            cancel_btn.disabled = True
            self.bulk_sending = False
            self.bulk_paused = False
            
        except json.JSONDecodeError as e:
            self.app.notify(f"Invalid JSON: {str(e)}", severity="error")
            send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
            send_btn.disabled = False
            send_btn.label = "📧 Send Email"
        except Exception as e:
            self.app.notify(f"Error: {str(e)}", severity="error")
            send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
            send_btn.disabled = False
            send_btn.label = "📧 Send Email"
            self.bulk_sending = False
    
    def handle_pause_bulk(self) -> None:
        """Handle pause/resume button click."""
        if not self.bulk_sending:
            return
            
        try:
            pause_btn = self.app.query_one("#pause-bulk-btn", Button)
            progress_label = self.app.query_one("#bulk-progress-label", Label)
            
            if self.bulk_paused:
                # Resume - add paused time to total
                if self.bulk_pause_start_time:
                    pause_duration = (datetime.now() - self.bulk_pause_start_time).total_seconds()
                    self.bulk_total_paused_seconds += pause_duration
                    self.bulk_pause_start_time = None
                
                self.bulk_paused = False
                self.pause_event.set()
                pause_btn.label = "⏸️ Pause"
                # Get current label text properly
                try:
                    current_text = progress_label.render()
                    if hasattr(current_text, 'plain'):
                        current_text = current_text.plain
                    else:
                        current_text = str(current_text)
                except:
                    current_text = "Sending..."
                
                if "PAUSED" in current_text:
                    progress_label.update(current_text.replace(" - PAUSED", ""))
                self.app.notify("Bulk sending resumed", severity="information")
            else:
                # Pause - record pause start time
                self.bulk_pause_start_time = datetime.now()
                self.bulk_paused = True
                self.pause_event.clear()
                pause_btn.label = "▶️ Resume"
                # Get current label text properly
                try:
                    current_text = progress_label.render()
                    if hasattr(current_text, 'plain'):
                        current_text = current_text.plain
                    else:
                        current_text = str(current_text)
                except:
                    current_text = "Sending..."
                
                progress_label.update(f"{current_text} - PAUSED")
                self.app.notify("Bulk sending paused", severity="warning")
                
        except Exception as e:
            logger = get_logger()
            if logger:
                logger.error(f"Error in handle_pause_bulk: {str(e)}", "SEND_EMAIL_FORM")
            self.app.notify(f"Error toggling pause: {str(e)}", severity="error")
    
    def handle_cancel_bulk(self) -> None:
        """Handle cancel button click."""
        if not self.bulk_sending:
            return
            
        try:
            self.bulk_cancelled = True
            self.pause_event.set()  # Unblock if paused
            
            # Set end time when cancelled
            if not self.bulk_end_time:
                self.bulk_end_time = datetime.now()
                
                # If we were paused when cancelled, add the final pause duration
                if self.bulk_paused and self.bulk_pause_start_time:
                    pause_duration = (self.bulk_end_time - self.bulk_pause_start_time).total_seconds()
                    self.bulk_total_paused_seconds += pause_duration
                    self.bulk_pause_start_time = None
            
            # Update progress label immediately to show cancellation in progress
            try:
                progress_label = self.app.query_one("#bulk-progress-label", Label)
                current_text = str(progress_label.render())
                if hasattr(progress_label.render(), 'plain'):
                    current_text = progress_label.render().plain
                progress_label.update(f"{current_text} - CANCELLING...")
            except:
                pass
            
            # Disable cancel button to prevent multiple clicks
            try:
                cancel_btn = self.app.query_one("#cancel-bulk-btn", Button)
                cancel_btn.disabled = True
            except:
                pass
            
            # The send loop will detect cancellation and update the progress label
            # Stats will remain visible so user can see what was accomplished
            # Use "Clear Form" button to reset everything
            
            self.app.notify("Cancelling bulk send...", severity="warning")
            
        except Exception as e:
            logger = get_logger()
            if logger:
                logger.error(f"Error in handle_cancel_bulk: {str(e)}", "SEND_EMAIL_FORM")
            self.app.notify(f"Error cancelling: {str(e)}", severity="error")
    
    def _get_default_config_set(self, configuration_sets: List[str]) -> str:
        """
        Get the default configuration set from settings.
        
        Args:
            configuration_sets: List of available configuration sets
            
        Returns:
            Default config set if it exists in available sets, otherwise empty string
        """
        default = settings.get('email.default_configuration_set', '')
        if default and default in configuration_sets:
            return default
        return ""
    
    def _construct_from_email(self) -> str:
        """Construct the from email address."""
        identity = self.app.query_one("#from-identity-enhanced", Select).value
        
        if '@' in identity:  # Email identity
            return identity
        else:  # Domain identity
            custom_email = self.app.query_one("#custom-email-enhanced", Input).value.strip()
            if custom_email:
                return f"{custom_email}@{identity}"
            else:
                return f"noreply@{identity}"
    
    def reset_bulk_progress(self) -> None:
        """Reset bulk sending progress display to initial state."""
        try:
            progress_bar = self.app.query_one("#bulk-progress-bar", ProgressBar)
            progress_label = self.app.query_one("#bulk-progress-label", Label)
            time_display = self.app.query_one("#bulk-time-display", Static)
            stats_display = self.app.query_one("#bulk-stats-display", Static)
            
            # Reset progress bar to 0
            progress_bar.update(total=100, progress=0)
            
            # Reset labels
            progress_label.update("Ready to send")
            time_display.update("")
            stats_display.update("")
            
            # Reset state variables
            self.bulk_results = []
            self.bulk_cancelled = False
            self.bulk_paused = False
            self.pause_event.set()
            
            # Reset time tracking
            self.bulk_start_time = None
            self.bulk_end_time = None
            self.bulk_pause_start_time = None
            self.bulk_total_paused_seconds = 0
            
        except Exception as e:
            logger = get_logger()
            if logger:
                logger.error(f"Error resetting bulk progress: {str(e)}", "SEND_EMAIL_FORM")
    
    def _get_elapsed_seconds(self) -> float:
        """Calculate elapsed seconds, accounting for paused time."""
        if not self.bulk_start_time:
            return 0
        
        end_time = self.bulk_end_time or datetime.now()
        total_seconds = (end_time - self.bulk_start_time).total_seconds()
        
        # Subtract paused time
        active_seconds = total_seconds - self.bulk_total_paused_seconds
        
        # If currently paused, also subtract the current pause duration
        if self.bulk_paused and self.bulk_pause_start_time:
            current_pause_duration = (datetime.now() - self.bulk_pause_start_time).total_seconds()
            active_seconds -= current_pause_duration
        
        return max(0, active_seconds)
    
    def _format_elapsed_time(self, seconds: float) -> str:
        """Format elapsed time as HH:MM:SS or MM:SS."""
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"
    
    async def schedule_campaign(self) -> None:
        """Schedule a campaign for future execution."""
        try:
            # Get form values
            from_identity = self.app.query_one("#from-identity-enhanced", Select).value
            template_name = self.app.query_one("#email-template-enhanced", Select).value
            csv_path = self.app.query_one("#csv-file-path", Input).value.strip()
            campaign_name = self.app.query_one("#campaign-name-bulk", Input).value.strip()
            schedule_datetime_str = self.app.query_one("#schedule-datetime", Input).value.strip()
            base_template_data_text = self.app.query_one("#template-data-bulk", TextArea).text.strip()
            config_set = self.app.query_one("#config-set-enhanced", Select).value
            ses_tags_text = self.app.query_one("#ses-tags-enhanced", TextArea).text.strip()
            
            # Validation
            if not from_identity or not template_name or not csv_path or not campaign_name or not schedule_datetime_str:
                self.app.notify("All fields required: From, Template, CSV, Campaign Name, and Schedule Time", severity="error")
                return
            
            # Parse schedule datetime
            try:
                schedule_dt = datetime.strptime(schedule_datetime_str, '%Y-%m-%d %H:%M')
                
                # Validate it's in the future
                if schedule_dt <= datetime.now():
                    self.app.notify("Schedule time must be in the future", severity="error")
                    return
            except ValueError:
                self.app.notify("Invalid datetime format. Use: YYYY-MM-DD HH:MM (e.g., 2025-12-25 14:30)", severity="error")
                return
            
            # Construct from email
            from_email = self._construct_from_email()
            if not from_email or '@' not in from_email:
                self.app.notify("Invalid from email address", severity="error")
                return
            
            # Validate CSV file first (including template variable matching)
            self.app.notify("Validating CSV file...", severity="information")
            validation_result = self.csv_validator.validate_csv_file(
                csv_path=csv_path,
                check_duplicates=True,
                max_rows=50000,
                template_name=template_name if template_name != Select.BLANK else None
            )
            
            if not validation_result.is_valid:
                # Show validation errors
                error_count = len(validation_result.errors)
                error_preview = "\n".join(validation_result.errors[:5])
                if error_count > 5:
                    error_preview += f"\n... and {error_count - 5} more error(s)"
                
                self.app.notify(
                    f"CSV validation failed with {error_count} error(s):\n{error_preview}",
                    severity="error",
                    timeout=10
                )
                return
            
            # Show validation warnings if any
            if validation_result.warnings:
                warning_count = len(validation_result.warnings)
                warning_preview = "\n".join(validation_result.warnings[:3])
                if warning_count > 3:
                    warning_preview += f"\n... and {warning_count - 3} more warning(s)"
                
                self.app.notify(
                    f"CSV validation warnings:\n{warning_preview}",
                    severity="warning",
                    timeout=8
                )
            
            # Parse CSV to count recipients
            recipients, error = self.bulk_sender.parse_csv_file(csv_path)
            if error:
                self.app.notify(error, severity="error")
                return
            
            total_recipients = len(recipients)
            
            # Parse optional data
            base_template_data = json.loads(base_template_data_text) if base_template_data_text else {}
            ses_tags = json.loads(ses_tags_text) if ses_tags_text else {}
            
            # Sanitize campaign name for SES tags (same as bulk email)
            import re
            sanitized_campaign = re.sub(r'[^a-zA-Z0-9_\-\.@]', '_', campaign_name)
            
            # Add campaign_name to tags now (campaign_id will be added after scheduler returns it)
            ses_tags['campaign_name'] = sanitized_campaign
            
            # Handle configuration set
            final_config_set = None
            if config_set and config_set != "" and str(config_set) != "Select.BLANK":
                final_config_set = config_set
            
            # Disable send button
            send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
            send_btn.disabled = True
            send_btn.label = "Scheduling..."
            
            self.app.notify("Uploading CSV and scheduling campaign...", severity="information")
            
            # Upload CSV to S3
            csv_s3_key = self.app.scheduled_campaigns_manager.upload_csv(csv_path, campaign_name)
            if not csv_s3_key:
                self.app.notify("Failed to upload CSV to S3", severity="error")
                send_btn.disabled = False
                send_btn.label = "📧 Send Email"
                return
            
            # Get optional metadata for analytics
            campaign_description = self.app.query_one("#campaign-description-bulk", Input).value.strip()
            campaign_creator = self.app.query_one("#campaign-creator-bulk", Input).value.strip()
            
            # Get unsubscribe settings
            unsub_type = self.app.query_one("#unsubscribe-type-select", Select).value
            campaign_topic = self.app.query_one("#campaign-topic", Input).value.strip()
            
            # Schedule the campaign (rate is configured in Lambda concurrency settings)
            result = self.app.scheduled_campaigns_manager.schedule_campaign(
                campaign_name=campaign_name,
                schedule_datetime=schedule_dt,
                csv_s3_key=csv_s3_key,
                template_name=template_name,
                from_email=from_email,
                total_recipients=total_recipients,
                configuration_set=final_config_set or '',
                tags=ses_tags,
                template_data=base_template_data,
                unsubscribe_enabled=(unsub_type != "none"),
                unsubscribe_type=unsub_type if unsub_type != "none" else "both",
                unsubscribe_topic=campaign_topic if campaign_topic else None
            )
            
            # Write metadata to analytics table if available
            if result and self.app.scheduled_campaigns_manager.analytics_deployed:
                campaign_id = result.get('campaign_id')
                if campaign_id:
                    self.app.scheduled_campaigns_manager.write_to_analytics_table(
                        campaign_id=campaign_id,
                        campaign_name=campaign_name,
                        template_name=template_name,
                        from_email=from_email,
                        configuration_set=final_config_set or '',
                        total_recipients=total_recipients,
                        schedule_time=schedule_dt,  # Pass the scheduled datetime
                        description=campaign_description if campaign_description else None,
                        creator=campaign_creator if campaign_creator else None
                    )
            
            # Re-enable button
            send_btn.disabled = False
            send_btn.label = "📧 Send Email"
            
            if result:
                campaign_id = result.get('campaign_id')
                
                # NOW update the campaign in DynamoDB to add campaign_id to tags
                # (campaign_name was already added above)
                ses_tags['campaign_id'] = campaign_id
                
                # Update campaign with complete tags including campaign_id
                try:
                    import boto3
                    from boto3.dynamodb.conditions import Key
                    
                    # Get DynamoDB table
                    if hasattr(self.app.scheduled_campaigns_manager, 'stack_info'):
                        table_name = self.app.scheduled_campaigns_manager.stack_info.get('table_name')
                        if table_name:
                            dynamodb = boto3.resource('dynamodb', region_name=self.app.scheduled_campaigns_manager.region)
                            table = dynamodb.Table(table_name)
                            
                            # Update the campaign record to include campaign_id in tags
                            table.update_item(
                                Key={
                                    'campaign_id': campaign_id,
                                    'schedule_timestamp': int(schedule_dt.timestamp())
                                },
                                UpdateExpression='SET tags = :tags',
                                ExpressionAttributeValues={':tags': ses_tags}
                            )
                except Exception as e:
                    if get_logger():
                        get_logger().warning(f"Could not update campaign tags with campaign_id: {str(e)}")
                
                self.app.notify(
                    f"✅ Campaign scheduled successfully!\n"
                    f"Campaign ID: {campaign_id}\n"
                    f"Scheduled for: {schedule_datetime_str}\n"
                    f"Recipients: {total_recipients}",
                    severity="success",
                    timeout=10
                )
            else:
                self.app.notify("Failed to schedule campaign", severity="error")
                
        except json.JSONDecodeError as e:
            self.app.notify(f"Invalid JSON: {str(e)}", severity="error")
            try:
                send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
                send_btn.disabled = False
                send_btn.label = "📧 Send Email"
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
        except Exception as e:
            self.app.notify(f"Error scheduling campaign: {str(e)}", severity="error")
            try:
                send_btn = self.app.query_one("#send-email-enhanced-btn", Button)
                send_btn.disabled = False
                send_btn.label = "📧 Send Email"
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
    
    def clear_form(self) -> None:
        """Clear the form fields."""
        try:
            # Clear single email fields
            self.app.query_one("#to-email-single", Input).value = ""
            self.app.query_one("#template-data-single", TextArea).text = '{"name": "John", "company": "Example Corp"}'
            
            # Clear bulk email fields
            self.app.query_one("#csv-file-path", Input).value = ""
            self.app.query_one("#template-data-bulk", TextArea).text = '{}'
            self.app.query_one("#campaign-name-bulk", Input).value = ""
            
            # Clear optional fields
            self.app.query_one("#cc-emails-enhanced", Input).value = ""
            self.app.query_one("#bcc-emails-enhanced", Input).value = ""
            self.app.query_one("#custom-email-enhanced", Input).value = ""
            self.app.query_one("#ses-tags-enhanced", TextArea).text = '{"example_tag": "example_value"}'
            self.app.query_one("#emails-per-second", Input).value = ""
            
            # Clear unsubscribe fields
            self.app.query_one("#unsubscribe-type-select", Select).value = "none"
            self.app.query_one("#campaign-topic", Input).value = ""
            
            # Reset bulk progress display
            self.reset_bulk_progress()
            
            notify_verbose(self.app, "Form cleared", severity="information")
            
        except Exception as e:
            self.app.notify(f"Error clearing form: {str(e)}", severity="error")
