"""
Campaign Metadata Module

Handles storing and retrieving campaign metadata from DynamoDB.
"""

import boto3
from datetime import datetime
from typing import Optional, Dict, Any
from decimal import Decimal
from botocore.exceptions import ClientError

from modules.logger import get_logger


def convert_floats_to_decimal(obj):
    """
    Recursively convert float and int values to Decimal for DynamoDB compatibility.
    
    Args:
        obj: Object to convert (can be dict, list, or primitive)
        
    Returns:
        Converted object with Decimals instead of floats
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, int):
        return Decimal(obj)
    elif isinstance(obj, dict):
        return {k: convert_floats_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    return obj


class CampaignMetadataManager:
    """Manages campaign metadata storage in DynamoDB."""
    
    def __init__(self, table_name: Optional[str] = None, region: Optional[str] = None, profile: Optional[str] = None):
        """
        Initialize the Campaign Metadata Manager.
        
        Args:
            table_name: DynamoDB table name (if None, metadata storage is disabled)
            region: AWS region
            profile: AWS profile name
        """
        self.table_name = table_name
        self.region = region
        self.profile = profile
        self.logger = get_logger()
        self.dynamodb = None
        self.table = None
        self.enabled = False
        
        if table_name:
            try:
                # Create DynamoDB client
                if profile and profile != 'default':
                    session = boto3.Session(profile_name=profile)
                else:
                    session = boto3.Session()
                
                self.dynamodb = session.resource('dynamodb', region_name=region)
                self.table = self.dynamodb.Table(table_name)
                self.enabled = True
                
                if self.logger:
                    self.logger.debug(f"Initialized DynamoDB metadata manager for table: {table_name}", "CAMPAIGN_METADATA")
                    
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Could not initialize DynamoDB metadata manager: {str(e)}", "CAMPAIGN_METADATA")
                self.enabled = False
    
    def store_metadata(
        self,
        campaign_id: str,
        campaign_name: str,
        template_name: Optional[str] = None,
        from_address: Optional[str] = None,
        description: Optional[str] = None,
        creator: Optional[str] = None,
        **additional_metadata
    ) -> bool:
        """
        Store campaign metadata in DynamoDB.
        
        Args:
            campaign_id: Unique campaign identifier
            campaign_name: Human-readable campaign name
            template_name: SES template name used
            from_address: From email address
            description: Campaign description
            creator: Creator identifier (email, username, etc.)
            **additional_metadata: Any additional metadata to store
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            if self.logger:
                self.logger.debug("DynamoDB metadata storage not enabled, skipping", "CAMPAIGN_METADATA")
            return False
        
        try:
            item = {
                'campaign_id': campaign_id,
                'campaign_name': campaign_name,
                'created_at': datetime.now().isoformat(),
            }
            
            # Add optional fields if provided
            if template_name:
                item['template_name'] = template_name
            if from_address:
                item['from_address'] = from_address
            if description:
                item['description'] = description
            if creator:
                item['creator'] = creator
            
            # Add any additional metadata
            item.update(additional_metadata)
            
            # Convert all float/int values to Decimal for DynamoDB compatibility
            item = convert_floats_to_decimal(item)
            
            # Store in DynamoDB
            self.table.put_item(Item=item)
            
            if self.logger:
                self.logger.info(f"Stored metadata for campaign: {campaign_id}", "CAMPAIGN_METADATA")
            
            return True
            
        except ClientError as e:
            if self.logger:
                self.logger.error(f"Error storing campaign metadata: {str(e)}", "CAMPAIGN_METADATA")
            return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Unexpected error storing campaign metadata: {str(e)}", "CAMPAIGN_METADATA")
            return False
    
    def get_metadata(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve campaign metadata from DynamoDB.
        
        Args:
            campaign_id: Unique campaign identifier
            
        Returns:
            Campaign metadata dictionary or None if not found
        """
        if not self.enabled:
            return None
        
        try:
            response = self.table.get_item(Key={'campaign_id': campaign_id})
            return response.get('Item')
            
        except ClientError as e:
            if self.logger:
                self.logger.error(f"Error retrieving campaign metadata: {str(e)}", "CAMPAIGN_METADATA")
            return None
        except Exception as e:
            if self.logger:
                self.logger.error(f"Unexpected error retrieving campaign metadata: {str(e)}", "CAMPAIGN_METADATA")
            return None
    
    def hide_campaign(self, campaign_id: str) -> bool:
        """
        Mark a campaign as hidden (soft delete).
        
        Args:
            campaign_id: Unique campaign identifier
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            if self.logger:
                self.logger.debug("DynamoDB metadata storage not enabled, skipping", "CAMPAIGN_METADATA")
            return False
        
        try:
            self.table.update_item(
                Key={'campaign_id': campaign_id},
                UpdateExpression='SET is_hidden = :val, hidden_at = :timestamp',
                ExpressionAttributeValues={
                    ':val': True,
                    ':timestamp': datetime.now().isoformat()
                }
            )
            
            if self.logger:
                self.logger.info(f"Hidden campaign: {campaign_id}", "CAMPAIGN_METADATA")
            
            return True
            
        except ClientError as e:
            if self.logger:
                self.logger.error(f"Error hiding campaign: {str(e)}", "CAMPAIGN_METADATA")
            return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Unexpected error hiding campaign: {str(e)}", "CAMPAIGN_METADATA")
            return False
    
    def unhide_campaign(self, campaign_id: str) -> bool:
        """
        Mark a campaign as visible (undo soft delete).
        
        Args:
            campaign_id: Unique campaign identifier
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            if self.logger:
                self.logger.debug("DynamoDB metadata storage not enabled, skipping", "CAMPAIGN_METADATA")
            return False
        
        try:
            self.table.update_item(
                Key={'campaign_id': campaign_id},
                UpdateExpression='SET is_hidden = :val REMOVE hidden_at',
                ExpressionAttributeValues={':val': False}
            )
            
            if self.logger:
                self.logger.info(f"Unhidden campaign: {campaign_id}", "CAMPAIGN_METADATA")
            
            return True
            
        except ClientError as e:
            if self.logger:
                self.logger.error(f"Error unhiding campaign: {str(e)}", "CAMPAIGN_METADATA")
            return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Unexpected error unhiding campaign: {str(e)}", "CAMPAIGN_METADATA")
            return False
    
    def get_hidden_campaigns(self) -> list:
        """
        Retrieve all hidden campaigns.
        
        Returns:
            List of hidden campaign metadata dictionaries
        """
        if not self.enabled:
            return []
        
        try:
            response = self.table.scan(
                FilterExpression='is_hidden = :val',
                ExpressionAttributeValues={':val': True}
            )
            
            return response.get('Items', [])
            
        except ClientError as e:
            if self.logger:
                self.logger.error(f"Error retrieving hidden campaigns: {str(e)}", "CAMPAIGN_METADATA")
            return []
        except Exception as e:
            if self.logger:
                self.logger.error(f"Unexpected error retrieving hidden campaigns: {str(e)}", "CAMPAIGN_METADATA")
            return []
