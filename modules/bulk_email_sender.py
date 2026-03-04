#!/usr/bin/env python3
"""
Bulk Email Sender Module
Handles bulk email sending with rate limiting, progress tracking, and CSV result logging
"""

import csv
import asyncio
import time
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from pathlib import Path
import json

from modules.logger import get_logger
from modules.unsubscribe_handler import UnsubscribeHandler
from config.settings import settings


class BulkEmailSender:
    """Manages bulk email sending operations with rate limiting and progress tracking."""
    
    def __init__(self, ses_client, settings_instance=None):
        self.ses_client = ses_client
        self.settings = settings_instance or settings
        self.logger = get_logger()
        self.unsubscribe_handler = UnsubscribeHandler(settings_instance)
        
        # Semaphore for rate limiting (will be set based on SES limits)
        self.semaphore = None
        self.sending_rate = 1  # emails per second (default fallback if SES rate not available)
        
        # Retry configuration - load from settings
        self.max_retries = self.settings.get('email.max_retries', 3)
        self.base_retry_delay = self.settings.get('email.base_retry_delay', 1.0)
        
        # Throttling tracking
        self.throttle_count = 0
        self.total_retries = 0
    
    def set_sending_rate(self, rate: int) -> None:
        """
        Set the sending rate based on SES account limits.
        
        Args:
            rate: Number of emails per second
        """
        self.sending_rate = max(1, rate)  # Ensure at least 1 per second
        self.semaphore = asyncio.Semaphore(self.sending_rate)
        
        if self.logger:
            self.logger.debug(f"Set bulk email sending rate to {self.sending_rate} emails/second", "BULK_SENDER")
    
    def parse_csv_file(self, csv_path: str) -> tuple[List[Dict[str, str]], Optional[str]]:
        """
        Parse CSV file and extract recipient data.
        
        Args:
            csv_path: Path to the CSV file
            
        Returns:
            Tuple of (list of recipient dictionaries, error message if any)
        """
        try:
            recipients = []
            csv_file = Path(csv_path)
            
            if not csv_file.exists():
                return [], f"CSV file not found: {csv_path}"
            
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                # Validate required column
                if 'To_Address' not in reader.fieldnames:
                    return [], "CSV must contain 'To_Address' column"
                
                for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
                    to_address = row.get('To_Address', '').strip()
                    
                    if not to_address:
                        if self.logger:
                            self.logger.warning(f"Skipping row {row_num}: missing To_Address", "BULK_SENDER")
                        continue
                    
                    # Extract substitution variables (columns starting with 'sub_')
                    substitutions = {}
                    for key, value in row.items():
                        if key.startswith('sub_'):
                            # Remove 'sub_' prefix for the actual substitution key
                            sub_key = key[4:]
                            substitutions[sub_key] = value
                    
                    recipients.append({
                        'to_address': to_address,
                        'substitutions': substitutions,
                        'row_number': row_num
                    })
            
            if not recipients:
                return [], "No valid recipients found in CSV file"
            
            if self.logger:
                self.logger.info(f"Parsed {len(recipients)} recipients from CSV file", "BULK_SENDER")
            
            return recipients, None
            
        except Exception as e:
            error_msg = f"Error parsing CSV file: {str(e)}"
            if self.logger:
                self.logger.error(error_msg, "BULK_SENDER")
            return [], error_msg
    
    def create_results_csv_path(self, original_csv_path: str) -> str:
        """
        Create a path for the results CSV file.
        
        Args:
            original_csv_path: Path to the original CSV file
            
        Returns:
            Path for the results CSV file
        """
        csv_file = Path(original_csv_path)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_filename = f"{csv_file.stem}_results_{timestamp}.csv"
        
        # Save to bulk_email_csv/bulk_email_output/ folder
        output_dir = Path('bulk_email_csv/bulk_email_output')
        output_dir.mkdir(parents=True, exist_ok=True)
        results_path = output_dir / results_filename
        
        return str(results_path)
    
    async def send_single_email_async(
        self,
        recipient: Dict[str, Any],
        email_config: Dict[str, Any],
        progress_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Send a single email asynchronously with rate limiting and retry logic.
        
        Args:
            recipient: Recipient data dictionary
            email_config: Email configuration (template, from, etc.)
            progress_callback: Optional callback for progress updates
            
        Returns:
            Result dictionary with status, message_id, and error info
        """
        result = {
            'to_address': recipient['to_address'],
            'row_number': recipient['row_number'],
            'status': 'pending',
            'message_id': None,
            'response_code': None,
            'error': None,
            'timestamp': datetime.now().isoformat(),
            'retries': 0,
            'throttled': False,
            'api_duration_ms': 0
        }
        
        # Retry loop with exponential backoff
        for attempt in range(self.max_retries + 1):
            try:
                # Rate limiting with semaphore
                async with self.semaphore:
                    # Prepare template data
                    template_data = email_config.get('base_template_data', {}).copy()
                    template_data.update(recipient['substitutions'])
                    
                    # Add unsubscribe link if enabled
                    if email_config.get('enable_unsubscribe'):
                        unsubscribe_type = email_config.get('unsubscribe_type', 'link')
                        campaign_topic = email_config.get('campaign_topic')
                        template_data = self.unsubscribe_handler.add_unsubscribe_to_template_data(
                            template_data,
                            recipient['to_address'],
                            unsubscribe_type,
                            campaign_topic
                        )
                    
                    # Prepare email data
                    email_data = {
                        'from_email': email_config['from_email'],
                        'to_emails': [recipient['to_address']],
                        'template_name': email_config['template_name'],
                        'template_data': template_data,
                        'cc_emails': email_config.get('cc_emails', []),
                        'bcc_emails': email_config.get('bcc_emails', []),
                        'ses_tags': email_config.get('ses_tags', {}),
                        'configuration_set': email_config.get('configuration_set'),
                        'email_type': 'template'
                    }
                    
                    # Add List-Unsubscribe headers if enabled
                    if email_config.get('enable_unsubscribe'):
                        unsubscribe_type = email_config.get('unsubscribe_type', 'link')
                        if unsubscribe_type in ['headers', 'both']:
                            headers = self.unsubscribe_handler.generate_list_unsubscribe_headers(
                                recipient['to_address'],
                                email_config.get('campaign_topic')
                            )
                            if headers:
                                email_data['email_headers'] = headers
                    
                    # Send the email (run in executor to avoid blocking) and track API duration
                    loop = asyncio.get_event_loop()
                    api_start = time.time()
                    response = await loop.run_in_executor(
                        None,
                        self.ses_client.send_templated_email,
                        email_data
                    )
                    api_end = time.time()
                    api_duration_ms = (api_end - api_start) * 1000  # Convert to milliseconds
                    
                    # Extract response details
                    result['status'] = 'success'
                    result['message_id'] = response.get('MessageId', 'Unknown')
                    result['response_code'] = response.get('ResponseMetadata', {}).get('HTTPStatusCode', 200)
                    result['retries'] = attempt
                    result['api_duration_ms'] = api_duration_ms
                    
                    # Call progress callback if provided
                    if progress_callback:
                        await progress_callback(result)
                    
                    # Success - break retry loop
                    break
                    
            except Exception as e:
                error_code = getattr(e, 'response', {}).get('Error', {}).get('Code', '')
                http_status = getattr(e, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode', 500)
                
                # Check if this is a throttling error
                is_throttling = (
                    error_code in ['Throttling', 'ThrottlingException', 'RequestLimitExceeded', 'TooManyRequestsException'] or
                    http_status == 429 or
                    'throttl' in str(e).lower() or
                    'rate' in str(e).lower()
                )
                
                if is_throttling:
                    result['throttled'] = True
                    self.throttle_count += 1
                
                # If this is the last attempt or not a retryable error, fail
                if attempt >= self.max_retries or not self._is_retryable_error(e):
                    result['status'] = 'failed'
                    result['error'] = str(e)
                    result['response_code'] = http_status
                    result['retries'] = attempt
                    
                    if self.logger:
                        self.logger.error(
                            f"Failed to send email to {recipient['to_address']} after {attempt + 1} attempts: {str(e)}",
                            "BULK_SENDER"
                        )
                    
                    # Call progress callback even on failure
                    if progress_callback:
                        await progress_callback(result)
                    
                    break
                else:
                    # Retry with exponential backoff and jitter
                    self.total_retries += 1
                    delay = self._calculate_backoff_delay(attempt, is_throttling)
                    
                    if self.logger:
                        self.logger.warning(
                            f"Retrying email to {recipient['to_address']} (attempt {attempt + 1}/{self.max_retries}) after {delay:.2f}s delay. Error: {str(e)}",
                            "BULK_SENDER"
                        )
                    
                    await asyncio.sleep(delay)
        
        return result
    
    def _is_retryable_error(self, exception: Exception) -> bool:
        """
        Determine if an error is retryable.
        
        Args:
            exception: The exception to check
            
        Returns:
            True if the error is retryable, False otherwise
        """
        error_code = getattr(exception, 'response', {}).get('Error', {}).get('Code', '')
        http_status = getattr(exception, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
        
        # Retryable error codes
        retryable_codes = [
            'Throttling',
            'ThrottlingException',
            'RequestLimitExceeded',
            'TooManyRequestsException',
            'ServiceUnavailable',
            'InternalError',
            'RequestTimeout'
        ]
        
        # Retryable HTTP status codes
        retryable_statuses = [429, 500, 502, 503, 504]
        
        return error_code in retryable_codes or http_status in retryable_statuses
    
    def _calculate_backoff_delay(self, attempt: int, is_throttling: bool = False) -> float:
        """
        Calculate exponential backoff delay.
        
        Args:
            attempt: The current attempt number (0-indexed)
            is_throttling: Whether this is a throttling error
            
        Returns:
            Delay in seconds
        """
        # Use 2x delay for throttling errors
        base_delay = self.base_retry_delay * 2 if is_throttling else self.base_retry_delay
        
        # Exponential backoff: base_delay × (2^attempt), capped at 30s
        delay = min(base_delay * (2 ** attempt), 30.0)
        
        return delay
    
    async def send_bulk_emails(
        self,
        recipients: List[Dict[str, Any]],
        email_config: Dict[str, Any],
        progress_callback: Optional[Callable] = None
    ) -> List[Dict[str, Any]]:
        """
        Send emails to multiple recipients with progress tracking.
        
        Args:
            recipients: List of recipient dictionaries
            email_config: Email configuration
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of result dictionaries
        """
        if self.logger:
            self.logger.info(f"Starting bulk email send to {len(recipients)} recipients", "BULK_SENDER")
        
        # Reset throttling counters
        self.throttle_count = 0
        self.total_retries = 0
        
        # Create tasks for all emails
        tasks = [
            self.send_single_email_async(recipient, email_config, progress_callback)
            for recipient in recipients
        ]
        
        # Execute all tasks concurrently (but rate-limited by semaphore)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                processed_results.append({
                    'status': 'failed',
                    'error': str(result),
                    'timestamp': datetime.now().isoformat(),
                    'retries': 0,
                    'throttled': False
                })
            else:
                processed_results.append(result)
        
        # Log summary with throttling info
        success_count = sum(1 for r in processed_results if r.get('status') == 'success')
        failed_count = len(processed_results) - success_count
        throttled_count = sum(1 for r in processed_results if r.get('throttled', False))
        
        if self.logger:
            log_msg = f"Bulk email send completed: {success_count} succeeded, {failed_count} failed"
            if throttled_count > 0:
                log_msg += f", {throttled_count} throttled (total retries: {self.total_retries})"
            self.logger.info(log_msg, "BULK_SENDER")
        
        return processed_results
    
    def write_results_to_csv(
        self,
        original_csv_path: str,
        results: List[Dict[str, Any]],
        output_path: Optional[str] = None
    ) -> str:
        """
        Write bulk email results to a CSV file.
        
        Args:
            original_csv_path: Path to the original CSV file
            results: List of result dictionaries
            output_path: Optional custom output path
            
        Returns:
            Path to the results CSV file
        """
        try:
            # Determine output path
            if not output_path:
                output_path = self.create_results_csv_path(original_csv_path)
            
            # Read original CSV to get all columns
            original_data = {}
            with open(original_csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                original_fieldnames = reader.fieldnames
                for row in reader:
                    to_address = row.get('To_Address', '').strip()
                    if to_address:
                        original_data[to_address] = row
            
            # Write results CSV with original columns plus result columns
            result_columns = ['Status', 'MessageId', 'ResponseCode', 'Error', 'Timestamp']
            fieldnames = list(original_fieldnames) + result_columns
            
            with open(output_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for result in results:
                    to_address = result.get('to_address', '')
                    
                    # Get original row data
                    row_data = original_data.get(to_address, {}).copy()
                    
                    # Add result data
                    row_data['Status'] = result.get('status', 'unknown')
                    row_data['MessageId'] = result.get('message_id', '')
                    row_data['ResponseCode'] = result.get('response_code', '')
                    row_data['Error'] = result.get('error', '')
                    row_data['Timestamp'] = result.get('timestamp', '')
                    
                    writer.writerow(row_data)
            
            if self.logger:
                self.logger.info(f"Results written to: {output_path}", "BULK_SENDER")
            
            return output_path
            
        except Exception as e:
            error_msg = f"Error writing results CSV: {str(e)}"
            if self.logger:
                self.logger.error(error_msg, "BULK_SENDER")
            raise Exception(error_msg)
    
    def get_sending_stats(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate statistics from bulk email results.
        
        Args:
            results: List of result dictionaries
            
        Returns:
            Dictionary with statistics
        """
        total = len(results)
        success = sum(1 for r in results if r.get('status') == 'success')
        failed = total - success
        
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': round((success / total * 100) if total > 0 else 0, 2)
        }
