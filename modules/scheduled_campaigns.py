"""
Scheduled Campaigns Module
Detects and integrates with ses_scheduled_campaigns CDK stack
"""

import boto3
import json
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path

from modules.logger import get_logger


class ScheduledCampaignsManager:
    """Manages integration with cloud-based scheduled campaigns"""
    
    def __init__(self, region=None, settings_instance=None):
        """
        Initialize ScheduledCampaignsManager
        
        Args:
            region: AWS region (if None, will try to get from settings)
            settings_instance: Settings manager instance to get region from
        """
        # Get region from settings if available, otherwise use default
        if region:
            self.region = region
        elif settings_instance:
            self.region = settings_instance.get('aws.region', 'us-east-1')
        else:
            # Fallback to environment or default
            import os
            self.region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
        
        self.lambda_client = boto3.client('lambda', region_name=self.region)
        self.s3_client = boto3.client('s3', region_name=self.region)
        self.cloudformation = boto3.client('cloudformation', region_name=self.region)
        self.dynamodb = boto3.resource('dynamodb', region_name=self.region)
        self.logger = get_logger()
        
        self.stack_info = None
        self.is_deployed = False
        self.analytics_info = None
        self.analytics_deployed = False
        
        # Detect stacks on initialization
        self.detect_stack()
        self.detect_analytics_stack()
    
    def detect_stack(self) -> bool:
        """
        Detect if ses_scheduled_campaigns stack is deployed
        
        Returns:
            True if stack is deployed, False otherwise
        """
        try:
            # Try to find the CloudFormation stack
            response = self.cloudformation.describe_stacks(
                StackName='SesScheduledCampaignsStack'
            )
            
            if not response.get('Stacks'):
                self.is_deployed = False
                return False
            
            stack = response['Stacks'][0]
            
            if stack['StackStatus'] not in ['CREATE_COMPLETE', 'UPDATE_COMPLETE']:
                self.is_deployed = False
                if self.logger:
                    self.logger.warning(
                        f"Scheduled campaigns stack status: {stack['StackStatus']}",
                        "SCHEDULED_CAMPAIGNS"
                    )
                return False
            
            # Extract outputs
            outputs = {o['OutputKey']: o['OutputValue'] for o in stack.get('Outputs', [])}
            
            self.stack_info = {
                'deployed': True,
                'bucket_name': outputs.get('CampaignBucketName'),
                'table_name': outputs.get('CampaignTableName'),
                'scheduler_function': outputs.get('CampaignSchedulerFunctionName'),
                'region': outputs.get('DeploymentRegion', self.region),
            }
            
            self.is_deployed = True
            
            if self.logger:
                self.logger.info(
                    "Scheduled campaigns stack detected and ready",
                    "SCHEDULED_CAMPAIGNS"
                )
            
            return True
            
        except self.cloudformation.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'ValidationError':
                # Stack doesn't exist
                self.is_deployed = False
                return False
            raise
        
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error detecting scheduled campaigns stack: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            self.is_deployed = False
            return False
    
    def upload_csv(self, local_csv_path: str, campaign_name: str) -> Optional[str]:
        """
        Upload CSV file to S3
        
        Args:
            local_csv_path: Path to local CSV file
            campaign_name: Name of campaign (used in S3 key)
            
        Returns:
            S3 key if successful, None otherwise
        """
        if not self.is_deployed:
            if self.logger:
                self.logger.error("Scheduled campaigns stack not deployed", "SCHEDULED_CAMPAIGNS")
            return None
        
        try:
            # Generate S3 key
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = Path(local_csv_path).name
            s3_key = f"campaigns/{campaign_name}_{timestamp}_{filename}"
            
            # Upload to S3
            self.s3_client.upload_file(
                local_csv_path,
                self.stack_info['bucket_name'],
                s3_key
            )
            
            if self.logger:
                self.logger.info(
                    f"Uploaded CSV to s3://{self.stack_info['bucket_name']}/{s3_key}",
                    "SCHEDULED_CAMPAIGNS"
                )
            
            return s3_key
            
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error uploading CSV: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            return None
    
    def schedule_campaign(
        self,
        campaign_name: str,
        schedule_datetime: datetime,
        csv_s3_key: str,
        template_name: str,
        from_email: str,
        total_recipients: int,
        configuration_set: str = '',
        tags: Optional[Dict[str, str]] = None,
        template_data: Optional[Dict[str, Any]] = None,
        unsubscribe_enabled: bool = False,
        unsubscribe_type: str = 'both',
        unsubscribe_topic: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Schedule a campaign for future execution
        
        Args:
            campaign_name: Name of the campaign
            schedule_datetime: When to execute the campaign
            csv_s3_key: S3 key of uploaded CSV file
            template_name: SES template name
            from_email: Sender email address
            total_recipients: Total number of recipients
            configuration_set: SES configuration set (optional)
            tags: Campaign tags (optional)
            template_data: Base template data (optional)
            
        Returns:
            Response dict with campaign_id if successful, None otherwise
        
        Note:
            Sending rate (TPS) is configured in AWS Lambda EmailSender function's 
            reserved concurrency setting. Default is 1 TPS. 
            Formula: Reserved Concurrency × 20 = Target TPS
        """
        if not self.is_deployed:
            if self.logger:
                self.logger.error("Scheduled campaigns stack not deployed", "SCHEDULED_CAMPAIGNS")
            return None
        
        try:
            # Prepare campaign data
            campaign_data = {
                'action': 'schedule',
                'campaign_data': {
                    'campaign_name': campaign_name,
                    'schedule_timestamp': int(schedule_datetime.timestamp()),
                    'csv_s3_key': csv_s3_key,
                    'template_name': template_name,
                    'from_email': from_email,
                    'configuration_set': configuration_set,
                    'tags': tags or {},
                    'total_recipients': total_recipients,
                    'template_data': template_data or {},
                    'unsubscribe_enabled': unsubscribe_enabled,
                    'unsubscribe_type': unsubscribe_type,
                    'unsubscribe_topic': unsubscribe_topic
                }
            }
            
            # Invoke scheduler Lambda
            response = self.lambda_client.invoke(
                FunctionName=self.stack_info['scheduler_function'],
                InvocationType='RequestResponse',
                Payload=json.dumps(campaign_data)
            )
            
            # Parse response
            result = json.loads(response['Payload'].read())
            
            if result.get('statusCode') == 200:
                body = json.loads(result['body'])
                campaign_id = body.get('campaign_id')
                
                # Also write to analytics table if deployed (for correlation with SES events)
                if campaign_id and self.analytics_deployed:
                    self.write_to_analytics_table(
                        campaign_id=campaign_id,
                        campaign_name=campaign_name,
                        template_name=template_name,
                        from_email=from_email,
                        configuration_set=configuration_set,
                        total_recipients=total_recipients
                    )
                
                if self.logger:
                    self.logger.info(
                        f"Campaign scheduled: {campaign_id}",
                        "SCHEDULED_CAMPAIGNS"
                    )
                
                return body
            else:
                error = json.loads(result.get('body', '{}')).get('error', 'Unknown error')
                if self.logger:
                    self.logger.error(
                        f"Failed to schedule campaign: {error}",
                        "SCHEDULED_CAMPAIGNS"
                    )
                return None
                
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error scheduling campaign: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            return None
    
    def list_campaigns(
        self,
        status_filter: Optional[str] = None,
        limit: int = 50,
        future_only: bool = True
    ) -> Optional[List[Dict[str, Any]]]:
        """
        List scheduled campaigns
        
        Args:
            status_filter: Filter by status (SCHEDULED, PROCESSING, COMPLETED, etc.)
            limit: Maximum number of campaigns to return
            future_only: If True, only return campaigns scheduled in the future (default: True)
            
        Returns:
            List of campaign dicts if successful, None otherwise
        """
        if not self.is_deployed:
            return None
        
        try:
            payload = {
                'action': 'list',
                'limit': limit,
                'future_only': future_only
            }
            
            if status_filter:
                payload['status'] = status_filter
            
            response = self.lambda_client.invoke(
                FunctionName=self.stack_info['scheduler_function'],
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            result = json.loads(response['Payload'].read())
            
            # Log the response for debugging
            if self.logger:
                self.logger.debug(
                    f"Lambda response: statusCode={result.get('statusCode')}, "
                    f"body keys={list(json.loads(result.get('body', '{}')).keys()) if result.get('body') else 'none'}",
                    "SCHEDULED_CAMPAIGNS"
                )
            
            if result.get('statusCode') == 200:
                body = json.loads(result['body'])
                campaigns = body.get('campaigns', [])
                
                if self.logger:
                    self.logger.info(
                        f"Retrieved {len(campaigns)} campaigns (future_only={payload.get('future_only')})",
                        "SCHEDULED_CAMPAIGNS"
                    )
                
                return campaigns
            else:
                # Log non-200 responses
                error_body = result.get('body', 'No error body')
                if self.logger:
                    self.logger.error(
                        f"Lambda returned status {result.get('statusCode')}: {error_body}",
                        "SCHEDULED_CAMPAIGNS"
                    )
                return None
            
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error listing campaigns: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            return None
    
    def get_campaign_status(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific campaign
        
        Args:
            campaign_id: Campaign ID
            
        Returns:
            Campaign status dict if successful, None otherwise
        """
        if not self.is_deployed:
            return None
        
        try:
            payload = {
                'action': 'get_status',
                'campaign_id': campaign_id
            }
            
            response = self.lambda_client.invoke(
                FunctionName=self.stack_info['scheduler_function'],
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            result = json.loads(response['Payload'].read())
            
            if result.get('statusCode') == 200:
                return json.loads(result['body'])
            
            return None
            
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error getting campaign status: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            return None
    
    def cancel_campaign(self, campaign_id: str) -> bool:
        """
        Cancel a scheduled campaign
        
        Args:
            campaign_id: Campaign ID to cancel
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_deployed:
            return False
        
        try:
            payload = {
                'action': 'cancel',
                'campaign_id': campaign_id
            }
            
            response = self.lambda_client.invoke(
                FunctionName=self.stack_info['scheduler_function'],
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            result = json.loads(response['Payload'].read())
            
            if result.get('statusCode') == 200:
                if self.logger:
                    self.logger.info(
                        f"Campaign cancelled: {campaign_id}",
                        "SCHEDULED_CAMPAIGNS"
                    )
                return True
            
            return False
            
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error cancelling campaign: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            return False
    
    def delete_campaign(self, campaign_id: str, schedule_timestamp: int, csv_s3_path: str = None) -> bool:
        """
        Delete a scheduled campaign from DynamoDB
        
        DynamoDB Stream automatically triggers cleanup Lambda which:
        - Deletes EventBridge rule
        - Deletes S3 CSV file
        
        This method handles:
        - Scheduler DynamoDB entry (triggers stream)
        - Analytics DynamoDB entry (if exists)
        
        Args:
            campaign_id: Campaign ID to delete
            schedule_timestamp: Schedule timestamp (partition key for DynamoDB)
            csv_s3_path: Unused (kept for API compatibility)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_deployed:
            if self.logger:
                self.logger.error("Scheduled campaigns stack not deployed", "SCHEDULED_CAMPAIGNS")
            return False
        
        try:
            # 1. Delete from scheduler DynamoDB table
            # This triggers DynamoDB Stream → Cleanup Lambda → EventBridge + S3 deletion
            try:
                scheduler_table = self.dynamodb.Table(self.stack_info['table_name'])
                scheduler_table.delete_item(
                    Key={
                        'campaign_id': campaign_id,
                        'schedule_timestamp': schedule_timestamp
                    }
                )
                if self.logger:
                    self.logger.info(
                        f"Deleted from scheduler table: {campaign_id} "
                        f"(DynamoDB Stream will trigger EventBridge + S3 cleanup)",
                        "SCHEDULED_CAMPAIGNS"
                    )
            except Exception as db_error:
                if self.logger:
                    self.logger.error(f"Error deleting from scheduler table: {str(db_error)}", "SCHEDULED_CAMPAIGNS")
                raise
            
            # 2. Delete from analytics metadata table (if deployed)
            if self.analytics_deployed and self.analytics_info and self.analytics_info.get('table_name'):
                try:
                    analytics_table = self.dynamodb.Table(self.analytics_info['table_name'])
                    analytics_table.delete_item(
                        Key={
                            'campaign_id': campaign_id
                        }
                    )
                    if self.logger:
                        self.logger.info(f"Deleted from analytics metadata table: {campaign_id}", "SCHEDULED_CAMPAIGNS")
                except Exception as analytics_error:
                    # Don't fail if analytics deletion fails - it might not exist
                    if self.logger:
                        self.logger.warning(f"Could not delete from analytics table: {str(analytics_error)}", "SCHEDULED_CAMPAIGNS")  # nosec B608 - No user input in SQL query
            
            if self.logger:
                self.logger.info(
                    f"Campaign deletion initiated: {campaign_id} "
                    f"(cleanup Lambda processing EventBridge rule and S3 CSV)",
                    "SCHEDULED_CAMPAIGNS"
                )
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error deleting campaign: {str(e)}", "SCHEDULED_CAMPAIGNS")
            return False
    
    def detect_analytics_stack(self) -> bool:
        """
        Detect if ses_campaign_analytics stack is deployed
        
        Returns:
            True if stack is deployed, False otherwise
        """
        try:
            # Try to find the CloudFormation stack
            response = self.cloudformation.describe_stacks(
                StackName='SesCampaignAnalyticsStack'
            )
            
            if not response.get('Stacks'):
                self.analytics_deployed = False
                return False
            
            stack = response['Stacks'][0]
            
            if stack['StackStatus'] not in ['CREATE_COMPLETE', 'UPDATE_COMPLETE']:
                self.analytics_deployed = False
                return False
            
            # Extract outputs
            outputs = {o['OutputKey']: o['OutputValue'] for o in stack.get('Outputs', [])}
            
            self.analytics_info = {
                'deployed': True,
                'table_name': outputs.get('CampaignMetadataTableName'),
                'config_set': outputs.get('SesConfigurationSetName'),
            }
            
            self.analytics_deployed = True
            
            if self.logger:
                self.logger.info(
                    "Campaign analytics stack detected",
                    "SCHEDULED_CAMPAIGNS"
                )
            
            return True
            
        except self.cloudformation.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'ValidationError':
                self.analytics_deployed = False
                return False
            raise
        
        except Exception as e:
            if self.logger:
                self.logger.debug(
                    f"Analytics stack not deployed: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            self.analytics_deployed = False
            return False
    
    def write_to_analytics_table(
        self,
        campaign_id: str,
        campaign_name: str,
        template_name: str,
        from_email: str,
        configuration_set: str,
        total_recipients: int,
        schedule_time: Optional[datetime] = None,
        description: Optional[str] = None,
        creator: Optional[str] = None
    ) -> bool:
        """
        Write campaign metadata to analytics table for correlation
        
        Args:
            campaign_id: Campaign ID
            campaign_name: Campaign name
            template_name: SES template used
            from_email: Sender email
            configuration_set: SES configuration set
            total_recipients: Total number of recipients
            schedule_time: Scheduled datetime (None for immediate)
            description: Campaign description (optional)
            creator: Campaign creator (optional)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.analytics_deployed:
            if self.logger:
                self.logger.debug(
                    "Analytics stack not deployed, skipping analytics table write",
                    "SCHEDULED_CAMPAIGNS"
                )
            return False
        
        try:
            analytics_table = self.dynamodb.Table(self.analytics_info['table_name'])
            
            # Prepare item with format matching existing analytics table
            item = {
                'campaign_id': campaign_id,
                'campaign_name': campaign_name,
                'template_name': template_name,
                'from_address': from_email,
                'configuration_set': configuration_set,
                'total_recipients': total_recipients,
                'created_at': datetime.now().isoformat(),  # ISO format string
                'schedule': schedule_time.isoformat() if schedule_time else 'immediate',
                'success_count': 0,  # Will be updated as emails are sent
                'failed_count': 0,   # Will be updated as emails fail
                'success_rate': 0    # Will be calculated later
            }
            
            # Add optional fields
            if description:
                item['description'] = description
            if creator:
                item['creator'] = creator
            
            # Write campaign metadata
            analytics_table.put_item(Item=item)
            
            if self.logger:
                self.logger.info(
                    f"Wrote campaign metadata to analytics table: {campaign_id}",
                    "SCHEDULED_CAMPAIGNS"
                )
            
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Error writing to analytics table: {str(e)}",
                    "SCHEDULED_CAMPAIGNS"
                )
            return False
    
    def get_stack_info(self) -> Dict[str, Any]:
        """Get information about the deployed stacks"""
        return {
            'scheduled_campaigns': self.stack_info or {'deployed': False},
            'analytics': self.analytics_info or {'deployed': False}
        }
