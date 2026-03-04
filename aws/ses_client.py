#!/usr/bin/env python3
"""
Amazon SES Client Module
Handles all Amazon SES operations and API interactions
"""

import json
import os
import subprocess

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

from modules.cache_manager import CacheManager, CachedAPIWrapper
from modules.logger import get_logger
from config.settings import Settings


class SESClient:
    """Amazon SES operations manager."""
    
    def __init__(self, profile_name = None, region_name = None, settings = None):
        self.profile_name = profile_name
        self.region_name = region_name or 'us-east-1'
        self.session = None
        self.ses_client = None
        
        # Initialize caching
        self.settings = settings or Settings()
        self.cache_manager = CacheManager(self.settings)
        self.cached_api = CachedAPIWrapper(self.cache_manager)
        
        # Debug logging
        logger = get_logger()
        if logger:
            logger.debug(f"Initializing SES client with profile: {profile_name}, region: {self.region_name}", "SES_CLIENT")
        
        self.initialize_session()
    
    def initialize_session(self):
        """Initialize AWS session and SES client."""
        logger = get_logger()
        try:
            if logger:
                logger.debug(f"Creating AWS session with profile: {self.profile_name}", "SES_CLIENT")
            
            if self.profile_name and self.profile_name != 'default':
                self.session = boto3.Session(profile_name=self.profile_name)
            else:
                # Use default session (will pick up env vars or default profile)
                self.session = boto3.Session()
            
            # Use specified region
            self.ses_client = self.session.client('sesv2', region_name=self.region_name)
            
            if logger:
                logger.debug(f"Created SES client for region: {self.region_name}", "SES_CLIENT")
            
            # Test the connection by trying to list identities
            test_response = self.ses_client.list_email_identities(PageSize=1)
            
            if logger:
                logger.debug(f"SES connection test successful, found {len(test_response.get('EmailIdentities', []))} identities", "SES_CLIENT")
            
        except (NoCredentialsError, ProfileNotFound) as e:
            if logger:
                logger.error(f"AWS credentials error during session initialization: {str(e)}", "SES_CLIENT")
            raise Exception(f"AWS credentials error: {str(e)}")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if logger:
                logger.error(f"Amazon SES ClientError during initialization: {error_code} - {str(e)}", "SES_CLIENT")
            if error_code == 'UnauthorizedOperation':
                raise Exception(f"AWS credentials are valid but don't have SES permissions: {str(e)}")
            else:
                raise Exception(f"Amazon SES connection error: {str(e)}")
        except Exception as e:
            if logger:
                logger.error(f"Failed to initialize AWS session: {str(e)}", "SES_CLIENT")
            raise Exception(f"Failed to initialize AWS session: {str(e)}")
    
    def get_templates(self, force_refresh: bool = False):
        """Fetch all email templates with caching support."""
        def _fetch_templates():
            response = self.ses_client.list_email_templates()
            templates = []
            
            for template_info in response.get('TemplatesMetadata', []):
                template_name = template_info['TemplateName']
                created_timestamp = template_info.get('CreatedTimestamp')
                
                template_detail = self.ses_client.get_email_template(
                    TemplateName=template_name
                )
                
                # Combine metadata with template content
                template_data = template_detail['TemplateContent'].copy()
                template_data['TemplateName'] = template_name
                template_data['CreatedTimestamp'] = created_timestamp
                
                templates.append(template_data)
            
            return templates
        
        try:
            # Use cached API wrapper for templates
            ttl_minutes = self.settings.get('cache.templates_ttl_minutes', 30)
            return self.cached_api.cached_call(
                operation_name="get_templates",
                api_function=_fetch_templates,
                ttl_minutes=ttl_minutes,
                force_refresh=force_refresh
            )
        except Exception as e:
            raise Exception(f"Error fetching templates: {str(e)}")
    
    def create_template(self, template_data):
        """Create a new email template."""
        logger = get_logger()
        template_name = template_data.get('TemplateName', 'Unknown')
        
        try:
            if logger:
                logger.debug(f"Creating template: {template_name}", "SES_CLIENT")
            
            self.ses_client.create_email_template(
                TemplateName=template_data['TemplateName'],
                TemplateContent={
                    'Subject': template_data.get('SubjectPart', ''),
                    'Html': template_data.get('HtmlPart', ''),
                    'Text': template_data.get('TextPart', '')
                }
            )
            
            if logger:
                logger.success(f"Successfully created template: {template_name}", "SES_CLIENT")
            
            # Invalidate templates cache after creating
            self.cache_manager.invalidate_cache("get_templates")
            
            if logger:
                logger.debug(f"Invalidated templates cache after creating: {template_name}", "SES_CLIENT")
                
        except ClientError as e:
            if logger:
                logger.error(f"Failed to create template '{template_name}': {str(e)}", "SES_CLIENT")
            raise Exception(f"Error creating template: {str(e)}")
    
    def update_template(self, template_data):
        """Update an existing email template."""
        try:
            self.ses_client.update_email_template(
                TemplateName=template_data['TemplateName'],
                TemplateContent={
                    'Subject': template_data.get('SubjectPart', ''),
                    'Html': template_data.get('HtmlPart', ''),
                    'Text': template_data.get('TextPart', '')
                }
            )
            # Invalidate templates cache after updating
            self.cache_manager.invalidate_cache("get_templates")
        except ClientError as e:
            raise Exception(f"Error updating template: {str(e)}")
    
    def delete_template(self, template_name: str):
        """Delete an email template."""
        try:
            self.ses_client.delete_email_template(TemplateName=template_name)
            # Invalidate templates cache after deleting
            self.cache_manager.invalidate_cache("get_templates")
        except ClientError as e:
            raise Exception(f"Error deleting template: {str(e)}")
    
    def get_identities(self, force_refresh: bool = False):
        """Fetch all verified identities with caching support."""
        def _fetch_identities():
            response = self.ses_client.list_email_identities()
            return [identity['IdentityName'] for identity in response.get('EmailIdentities', [])]
        
        try:
            # Use cached API wrapper for identities
            ttl_minutes = self.settings.get('cache.identities_ttl_minutes', 60)
            return self.cached_api.cached_call(
                operation_name="get_identities",
                api_function=_fetch_identities,
                ttl_minutes=ttl_minutes,
                force_refresh=force_refresh
            )
        except Exception as e:
            raise Exception(f"Error fetching identities: {str(e)}")
    
    def get_configuration_sets(self, force_refresh: bool = False):
        """Fetch all configuration sets with caching support."""
        def _fetch_configuration_sets():
            response = self.ses_client.list_configuration_sets()
            
            # Handle the response structure properly
            config_sets = response.get('ConfigurationSets', [])
            
            # Extract configuration set names - handle both possible response formats
            result = []
            for config_set in config_sets:
                if isinstance(config_set, dict):
                    # If it's a dict, get the ConfigurationSetName
                    result.append(config_set.get('ConfigurationSetName', str(config_set)))
                else:
                    # If it's a string, use it directly
                    result.append(str(config_set))
            
            return result
        
        try:
            # Use cached API wrapper for configuration sets
            ttl_minutes = self.settings.get('cache.configuration_sets_ttl_minutes', 120)
            return self.cached_api.cached_call(
                operation_name="get_configuration_sets",
                api_function=_fetch_configuration_sets,
                ttl_minutes=ttl_minutes,
                force_refresh=force_refresh
            )
        except Exception as e:
            raise Exception(f"Error fetching configuration sets: {str(e)}")
    
    def extract_template_placeholders(self, template_name: str):
        """Extract placeholders from a template and return a dict with placeholder keys and example values."""
        try:
            import re
            
            # Get the template
            response = self.ses_client.get_email_template(TemplateName=template_name)
            template_content = response['TemplateContent']
            
            # Extract all text content to search for placeholders
            all_text = ""
            if template_content.get('Subject'):
                all_text += template_content['Subject'] + " "
            if template_content.get('Html'):
                all_text += template_content['Html'] + " "
            if template_content.get('Text'):
                all_text += template_content['Text'] + " "
            
            # Find all {{placeholder}} patterns
            placeholder_pattern = r'\{\{([^}]+)\}\}'
            matches = re.findall(placeholder_pattern, all_text)
            
            # Filter out functional template syntax (Handlebars/SES template functions)
            functional_keywords = [
                '#if', '/if', '#unless', '/unless', '#each', '/each', '#with', '/with',
                '#eq', '#ne', '#lt', '#gt', '#le', '#ge', '#and', '#or', '#not',
                'else', 'this', '@index', '@first', '@last', '@key', '@root',
                'lookup', 'log', 'blockHelperMissing', 'helperMissing'
            ]
            
            # Create a dict with placeholder keys and example values
            placeholders = {}
            for match in matches:
                placeholder_key = match.strip()
                
                # Skip functional template syntax
                is_functional = False
                for keyword in functional_keywords:
                    if placeholder_key.startswith(keyword) or f' {keyword}' in placeholder_key or keyword in placeholder_key.split():
                        is_functional = True
                        break
                
                # Skip if it contains operators or looks like a function call
                if (is_functional or 
                    any(op in placeholder_key for op in ['==', '!=', '>', '<', '>=', '<=', '&&', '||']) or
                    placeholder_key.startswith('#') or placeholder_key.startswith('/') or
                    '(' in placeholder_key or ')' in placeholder_key):
                    continue
                
                # Generate example values based on common placeholder names
                if 'name' in placeholder_key.lower():
                    placeholders[placeholder_key] = "John Doe"
                elif 'email' in placeholder_key.lower():
                    placeholders[placeholder_key] = "user@example.com"
                elif 'company' in placeholder_key.lower():
                    placeholders[placeholder_key] = "Example Corp"
                elif 'url' in placeholder_key.lower() or 'link' in placeholder_key.lower():
                    placeholders[placeholder_key] = "https://example.com"
                elif 'date' in placeholder_key.lower():
                    placeholders[placeholder_key] = "2024-01-01"
                elif 'amount' in placeholder_key.lower() or 'price' in placeholder_key.lower():
                    placeholders[placeholder_key] = "99.99"
                else:
                    placeholders[placeholder_key] = f"example_{placeholder_key.lower()}"
            
            return placeholders
        except ClientError as e:
            raise Exception(f"Error extracting template placeholders: {str(e)}")
        except Exception as e:
            # Return empty dict if extraction fails
            return {}
    
    def send_templated_email(self, email_data):
        """Send email using a template with enhanced features."""
        logger = get_logger()
        template_name = email_data.get('template_name', 'Unknown')
        to_emails = email_data.get('to_emails', [])
        
        try:
            # Verbose logging commented out for bulk sending performance
            # if logger:
            #     logger.debug(f"Sending templated email using template '{template_name}' to {len(to_emails)} recipients", "SES_CLIENT")
            #     logger.debug(f"Email recipients: {', '.join(to_emails)}", "SES_CLIENT")
            
            destination = {'ToAddresses': email_data['to_emails']}
            if email_data.get('cc_emails'):
                destination['CcAddresses'] = email_data['cc_emails']
                # if logger:
                #     logger.debug(f"CC recipients: {', '.join(email_data['cc_emails'])}", "SES_CLIENT")
            if email_data.get('bcc_emails'):
                destination['BccAddresses'] = email_data['bcc_emails']
                # if logger:
                #     logger.debug(f"BCC recipients: {', '.join(email_data['bcc_emails'])}", "SES_CLIENT")
            
            # Build the send_email parameters
            send_params = {
                'FromEmailAddress': email_data['from_email'],
                'Destination': destination,
                'Content': {
                    'Template': {
                        'TemplateName': email_data['template_name'],
                        'TemplateData': json.dumps(email_data.get('template_data', {}))
                    }
                }
            }
            
            # if logger:
            #     logger.debug(f"Template data: {email_data.get('template_data', {})}", "SES_CLIENT")
            
            # Add custom email headers if provided (e.g., List-Unsubscribe)
            if email_data.get('email_headers'):
                # Convert headers dict to list of MessageHeader objects
                headers_list = [
                    {'Name': name, 'Value': value}
                    for name, value in email_data['email_headers'].items()
                ]
                send_params['Content']['Template']['Headers'] = headers_list
                # if logger:
                #     logger.debug(f"Adding custom headers: {list(email_data['email_headers'].keys())}", "SES_CLIENT")
            
            # Add optional SES tags (SES v2 format)
            if email_data.get('ses_tags'):
                send_params['EmailTags'] = [
                    {'Name': str(key), 'Value': str(value)}
                    for key, value in email_data['ses_tags'].items()
                ]
                # if logger:
                #     logger.debug(f"SES tags: {email_data['ses_tags']}", "SES_CLIENT")
            
            # Add optional configuration set
            if email_data.get('configuration_set'):
                send_params['ConfigurationSetName'] = email_data['configuration_set']
                # if logger:
                #     logger.debug(f"Using configuration set: {email_data['configuration_set']}", "SES_CLIENT")
            
            response = self.ses_client.send_email(**send_params)
            
            # Success logging commented out for bulk sending performance
            # if logger:
            #     message_id = response.get('MessageId', 'Unknown')
            #     logger.success(f"Successfully sent templated email '{template_name}' - MessageId: {message_id}", "SES_CLIENT")
            
            return response
        except ClientError as e:
            if logger:
                logger.error(f"Failed to send templated email '{template_name}': {str(e)}", "SES_CLIENT")
            raise Exception(f"Error sending templated email: {str(e)}")


def get_aws_profiles():
    """Get list of AWS profiles from AWS CLI configuration."""
    profiles = []
    
    # Try to get profiles from AWS CLI
    try:
        result = subprocess.run(  # nosec B603 - Controlled AWS CLI command
            ['aws', 'configure', 'list-profiles'],
            capture_output=True,
            text=True,
            check=True
        )
        profiles = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: try to read from config file
        config_path = os.path.expanduser('~/.aws/config')
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('[profile '):
                            profile_name = line[9:-1]  # Remove '[profile ' and ']'
                            profiles.append(profile_name)
                        elif line.startswith('[default]'):
                            profiles.append('default')
            except Exception:
                pass  # nosec B110 - Intentional - widget may not be mounted
    
    # If no profiles found, add default
    if not profiles:
        profiles = ['default']
    
    return profiles
