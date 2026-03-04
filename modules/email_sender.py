#!/usr/bin/env python3
"""
Email Sender Module
Handles email sending functionality
"""

import json
from typing import Dict, List, Optional, Any
from datetime import datetime

from textual.widgets import Log

from aws.ses_client import SESClient
from config.settings import settings


class EmailSender:
    """Manages email sending operations and UI interactions."""
    
    def __init__(self, ses_client: Optional[SESClient] = None, settings_instance = None):
        self.ses_client = ses_client
        self.settings = settings_instance or settings
        self.identities = []
    
    def set_ses_client(self, ses_client: SESClient):
        """Set the SES client for email operations."""
        self.ses_client = ses_client
    
    async def refresh_identities(self) -> None:
        """Refresh identities data from Amazon SES."""
        if not self.ses_client:
            return
        
        try:
            self.identities = self.ses_client.get_identities()
        except Exception as e:
            print(f"Error refreshing identities: {str(e)}")
    
    async def send_email_worker(self, email_data: Dict[str, Any], log: Optional[Any], notify_func) -> None:
        """Enhanced worker for sending emails with detailed logging."""
        if not self.ses_client:
            notify_func("No SES connection available", severity="error")
            return
        
        # Track log content for Static widgets
        log_content = []
        
        def safe_log(message: str):
            """Safely log message if log widget is available."""
            if log:
                # Handle both Log and Static widgets
                if hasattr(log, 'write_line'):
                    # It's a Log widget
                    log.write_line(message)
                elif hasattr(log, 'update'):
                    # It's a Static widget - accumulate content
                    log_content.append(message)
                    log.update("\n".join(log_content))
        
        try:
            timestamp = datetime.now().strftime('%H:%M:%S')
            
            # Log email sending start
            safe_log(f"[{timestamp}] 📧 Starting email send process...")
            safe_log(f"[{timestamp}] Mode: {email_data.get('email_mode', 'single')}")
            safe_log(f"[{timestamp}] From: {email_data['from_email']}")
            safe_log(f"[{timestamp}] Template: {email_data['template_name']}")
            safe_log(f"[{timestamp}] To: {', '.join(email_data['to_emails'])}")
            
            if email_data.get('cc_emails'):
                safe_log(f"[{timestamp}] CC: {', '.join(email_data['cc_emails'])}")
            if email_data.get('bcc_emails'):
                safe_log(f"[{timestamp}] BCC: {', '.join(email_data['bcc_emails'])}")
            
            # Log optional features
            if email_data.get('ses_tags'):
                safe_log(f"[{timestamp}] SES Tags: {json.dumps(email_data['ses_tags'])}")
            if email_data.get('configuration_set'):
                safe_log(f"[{timestamp}] Configuration Set: {email_data['configuration_set']}")
            
            # Log template data
            if email_data.get('template_data'):
                safe_log(f"[{timestamp}] Template Data: {json.dumps(email_data['template_data'], indent=2)}")
            
            safe_log(f"[{timestamp}] ⏳ Sending via Amazon SES...")
            
            # Send the email (only templated emails are supported)
            response = self.ses_client.send_templated_email(email_data)
            
            # Log success details
            timestamp = datetime.now().strftime('%H:%M:%S')
            message_id = response.get('MessageId', 'Unknown')
            
            safe_log(f"[{timestamp}] ✅ EMAIL SENT SUCCESSFULLY!")
            safe_log(f"[{timestamp}] 📨 Message ID: {message_id}")
            
            # Log AWS API response details
            safe_log(f"[{timestamp}] 📊 Amazon SES API Response:")
            safe_log(f"[{timestamp}]   • Status: SUCCESS")
            safe_log(f"[{timestamp}]   • Message ID: {message_id}")
            
            if 'ResponseMetadata' in response:
                metadata = response['ResponseMetadata']
                safe_log(f"[{timestamp}]   • Request ID: {metadata.get('RequestId', 'N/A')}")
                safe_log(f"[{timestamp}]   • HTTP Status: {metadata.get('HTTPStatusCode', 'N/A')}")
                safe_log(f"[{timestamp}]   • Retry Attempts: {metadata.get('RetryAttempts', 0)}")
            
            # Log recipient summary
            total_recipients = len(email_data['to_emails']) + len(email_data.get('cc_emails', [])) + len(email_data.get('bcc_emails', []))
            safe_log(f"[{timestamp}] 👥 Recipients: {total_recipients} total")
            safe_log(f"[{timestamp}]   • To: {len(email_data['to_emails'])}")
            if email_data.get('cc_emails'):
                safe_log(f"[{timestamp}]   • CC: {len(email_data['cc_emails'])}")
            if email_data.get('bcc_emails'):
                safe_log(f"[{timestamp}]   • BCC: {len(email_data['bcc_emails'])}")
            
            # Log full API response for debugging (collapsed format)
            safe_log(f"[{timestamp}] 🔍 Full API Response (for debugging):")
            response_json = json.dumps(response, indent=2, default=str)
            for line in response_json.split('\n'):
                safe_log(f"[{timestamp}]   {line}")
            
            safe_log("=" * 80)
            
            notify_func("Email sent successfully!", severity="success")
            
        except Exception as e:
            timestamp = datetime.now().strftime('%H:%M:%S')
            safe_log(f"[{timestamp}] ❌ EMAIL SEND FAILED")
            safe_log(f"[{timestamp}] 🚨 Error Type: {type(e).__name__}")
            safe_log(f"[{timestamp}] 📝 Error Message: {str(e)}")
            
            # Try to extract more details from AWS error
            if hasattr(e, 'response'):
                error_response = getattr(e, 'response', {})
                if 'Error' in error_response:
                    error_details = error_response['Error']
                    safe_log(f"[{timestamp}] 🏷️  AWS Error Code: {error_details.get('Code', 'Unknown')}")
                    safe_log(f"[{timestamp}] 💬 AWS Error Message: {error_details.get('Message', 'Unknown')}")
            
            safe_log(f"[{timestamp}] 📧 Email Details:")
            safe_log(f"[{timestamp}]   • From: {email_data.get('from_email', 'N/A')}")
            safe_log(f"[{timestamp}]   • Template: {email_data.get('template_name', 'N/A')}")
            safe_log(f"[{timestamp}]   • Recipients: {len(email_data.get('to_emails', []))}")
            
            safe_log("=" * 80)
            notify_func(f"Error sending email: {str(e)}", severity="error")
    
    def clear_log(self, log: Log) -> None:
        """Clear the email log."""
        try:
            log.clear()
            log.write_line(f"[{datetime.now().strftime('%H:%M:%S')}] Log cleared")
        except:
            pass  # nosec B110 - Intentional - widget may not be mounted
