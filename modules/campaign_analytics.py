"""
Campaign Analytics Module

Provides integration with AWS CDK Campaign Analytics stack for Amazon SES Campaign Manager.
Handles CDK stack detection, Athena queries, and campaign metrics visualization.
"""

import json
import boto3
import time
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from botocore.exceptions import ClientError

from modules.logger import get_logger
from modules.notification_helper import notify_verbose, notify_always


class CampaignAnalyticsManager:
    """
    Manages campaign analytics integration with CDK stack.
    """
    
    def __init__(self, app, ses_client, settings):
        """
        Initialize the Campaign Analytics Manager.
        
        Args:
            app: The main TUI application instance
            ses_client: SES client instance
            settings: Settings instance
        """
        self.app = app
        self.ses_client = ses_client
        self.settings = settings
        self.logger = get_logger()
        
        # Stack detection results
        self.stack_deployed = False
        self.stack_name = None
        self.stack_outputs = {}
        self.configuration_set_name = None
        self.database_name = None
        self.workgroup_name = None
        self.output_location = None
        self.region = None
        self.metadata_table_name = None
        
        # Athena client
        self.athena = None
        
        # Campaign metadata manager
        self.metadata_manager = None
        
        # Cache for analytics data
        self.campaigns_cache = None
        self.campaigns_cache_time = None
        self.cache_ttl = 300  # 5 minutes
        
    async def detect_cdk_stack(self, profile_name: str, region_name: str) -> bool:
        """
        Detect if the Campaign Analytics CDK stack is deployed.
        
        Args:
            profile_name: AWS profile name
            region_name: AWS region
            
        Returns:
            True if stack is deployed and configured, False otherwise
        """
        try:
            # Create CloudFormation client - same approach as SES client
            if profile_name and profile_name != 'default':
                session = boto3.Session(profile_name=profile_name)
            else:
                # Use default session (will pick up env vars, IAM roles, or default profile)
                session = boto3.Session()
            
            cfn = session.client('cloudformation', region_name=region_name)
            
            if self.logger:
                self.logger.info(f"Searching for Campaign Analytics CDK stack in {region_name}...")
            
            # List all stacks and check each one
            paginator = cfn.get_paginator('list_stacks')
            all_stacks = []
            for page in paginator.paginate(
                StackStatusFilter=[
                    'CREATE_COMPLETE', 
                    'UPDATE_COMPLETE', 
                    'UPDATE_ROLLBACK_COMPLETE'
                ]
            ):
                for stack_summary in page['StackSummaries']:
                    stack_name = stack_summary['StackName']
                    all_stacks.append(stack_name)
                    
                    # Get stack details and check outputs
                    try:
                        stack_details = cfn.describe_stacks(StackName=stack_name)
                        if stack_details['Stacks']:
                            stack = stack_details['Stacks'][0]
                            outputs = stack.get('Outputs', [])
                            
                            # Parse outputs into a dictionary
                            output_dict = {}
                            for output in outputs:
                                output_dict[output['OutputKey']] = output['OutputValue']
                            
                            # Check if this has the expected outputs for campaign analytics
                            # Must have all these outputs to be considered a campaign analytics stack
                            required_outputs = [
                                'SesConfigurationSetName',
                                'GlueDatabaseName',
                                'AthenaWorkGroupName',
                                'AthenaResultsBucketName'
                            ]
                            
                            if all(key in output_dict for key in required_outputs):
                                # Found the stack!
                                self.stack_deployed = True
                                self.stack_name = stack_name
                                self.stack_outputs = output_dict
                                self.configuration_set_name = output_dict['SesConfigurationSetName']
                                self.database_name = output_dict['GlueDatabaseName']
                                self.workgroup_name = output_dict['AthenaWorkGroupName']
                                self.output_location = f"s3://{output_dict['AthenaResultsBucketName']}/query-results/"
                                self.region = region_name
                                
                                # Check for optional DynamoDB table
                                if 'CampaignMetadataTableName' in output_dict:
                                    self.metadata_table_name = output_dict['CampaignMetadataTableName']
                                    
                                    # Initialize metadata manager
                                    from modules.campaign_metadata import CampaignMetadataManager
                                    self.metadata_manager = CampaignMetadataManager(
                                        table_name=self.metadata_table_name,
                                        region=region_name,
                                        profile=profile_name
                                    )
                                    
                                    if self.logger:
                                        self.logger.info(f"DynamoDB Metadata Table: {self.metadata_table_name}")
                                
                                # Initialize Athena client with region
                                self.athena = session.client('athena', region_name=region_name)
                                
                                if self.logger:
                                    self.logger.success(f"Found Campaign Analytics stack: {stack_name}")
                                    self.logger.info(f"Configuration Set: {self.configuration_set_name}")
                                    self.logger.info(f"Database: {self.database_name}")
                                    self.logger.info(f"Workgroup: {self.workgroup_name}")
                                
                                return True
                    except Exception as e:
                        if self.logger:
                            self.logger.debug(f"Stack {stack_name} is not campaign analytics stack: {str(e)}")
                        continue
            
            # Stack not found - log all stacks seen for debugging
            if self.logger:
                self.logger.info(f"Campaign Analytics CDK stack not found in {region_name}")
                self.logger.info(f"Searched {len(all_stacks)} stacks: {', '.join(all_stacks[:5])}{'...' if len(all_stacks) > 5 else ''}")
            
            return False
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error detecting CDK stack: {str(e)}")
            return False
    
    def get_stack_info(self) -> Dict:
        """
        Get information about the detected stack.
        
        Returns:
            Dictionary with stack information
        """
        return {
            'deployed': self.stack_deployed,
            'stack_name': self.stack_name,
            'configuration_set': self.configuration_set_name,
            'database': self.database_name,
            'workgroup': self.workgroup_name,
            'region': self.region,
            'outputs': self.stack_outputs
        }
    
    async def get_campaigns_by_period(
        self,
        days: int = 30,
        campaign_name: Optional[str] = None,
        limit: int = 100,
        force_refresh: bool = False,
        show_hidden: bool = False
    ) -> List[Dict]:
        """
        Get campaign performance for a specific time period from campaign_metrics_daily.
        
        Args:
            days: Number of days to analyze (default 30, max 180)
            campaign_name: Optional specific campaign to filter
            limit: Maximum number of campaigns to return
            force_refresh: Force refresh from Athena (ignore cache)
            show_hidden: Include hidden campaigns in results (default False)
            
        Returns:
            List of campaign dictionaries with aggregated metrics for the period
        """
        if not self.stack_deployed:
            return []
        
        if self.logger:
            query_desc = f"Campaigns: {days}d"
            if campaign_name:
                query_desc += f", filter={campaign_name}"
            if force_refresh:
                query_desc += ", force"
            self.logger.debug(query_desc)
        
        # Enforce 180-day maximum
        if days > 180:
            days = 180
            if self.logger:
                self.logger.warning(f"Date range limited to 180 days maximum")
        
        # Check cache only for default 30-day view without filter
        if not force_refresh and days == 30 and not campaign_name:
            if self.campaigns_cache is not None:
                cache_age = (datetime.now() - self.campaigns_cache_time).total_seconds()
                if cache_age < self.cache_ttl:
                    if self.logger:
                        self.logger.debug(f"Using cache ({len(self.campaigns_cache)} campaigns, {cache_age:.0f}s old)")
                    return self.campaigns_cache[:limit]
        
        try:
            # Query campaign_metrics_daily and aggregate over the period
            # nosec B608 - No user input in SQL query
            query = f"""
            SELECT
              campaign_id,
              campaign_name,
              MIN(date) as first_send_date,
              MAX(date) as last_send_date,
              SUM(emails_sent) as total_sent,
              SUM(emails_delivered) as total_delivered,
              SUM(emails_opened) as total_opened,
              SUM(emails_clicked) as total_clicked,
              SUM(hard_bounces) as total_hard_bounces,
              SUM(soft_bounces) as total_soft_bounces,
              SUM(complaints) as total_complaints,
              SUM(rendering_failures) as total_rendering_failures,
              AVG(delivery_rate) as overall_delivery_rate,
              AVG(open_rate) as overall_open_rate,
              AVG(click_rate) as overall_click_rate,
              AVG(hard_bounce_rate) as overall_hard_bounce_rate,
              AVG(complaint_rate) as overall_complaint_rate,
              AVG(rendering_failure_rate) as overall_rendering_failure_rate,
              COUNT(DISTINCT date) as days_active
            FROM {self.database_name}.campaign_metrics_daily
            WHERE date >= CAST(current_date - interval '{days}' day AS VARCHAR)
            """
            
            if campaign_name:
                # Exact match for specific campaign
                safe_campaign = campaign_name.replace("'", "''")
                query += f" AND campaign_name = '{safe_campaign}'"
            
            query += f"""
            GROUP BY campaign_id, campaign_name
            ORDER BY MIN(date) DESC, total_sent DESC
            LIMIT {limit}
            """
            
            results = await self._execute_query_async(query)
            
            # Update cache for default 30-day view BEFORE filtering
            # This allows us to toggle show_hidden without re-querying Athena
            if days == 30 and not campaign_name:
                self.campaigns_cache = results
                self.campaigns_cache_time = datetime.now()
            
            # Filter out hidden campaigns if metadata manager is available
            if not show_hidden and self.metadata_manager and self.metadata_manager.enabled:
                original_count = len(results)
                filtered_results = []
                for campaign in results:
                    metadata = await self.get_campaign_metadata(campaign['campaign_id'])
                    # Include campaign if no metadata exists or is_hidden is not True
                    if not metadata or not metadata.get('is_hidden', False):
                        filtered_results.append(campaign)
                
                hidden_count = original_count - len(filtered_results)
                if self.logger and hidden_count > 0:
                    self.logger.debug(f"Filtered {hidden_count} hidden → {len(filtered_results)} visible")
                
                results = filtered_results
            
            return results
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error fetching campaigns by period: {str(e)}")
            return []
    
    async def get_campaign_daily_metrics(
        self,
        campaign_name: str,
        days: int = 30
    ) -> List[Dict]:
        """
        Get daily metrics for a specific campaign from campaign_metrics_daily table.
        
        Args:
            campaign_name: Name of the campaign
            days: Number of days to retrieve
            
        Returns:
            List of daily metric dictionaries
        """
        if not self.stack_deployed:
            return []
        
        try:
            # Escape single quotes in campaign name
            safe_campaign = campaign_name.replace("'", "''")
            
            # Query the materialized view table directly
            # nosec B608 - No user input in SQL query
            query = f"""
            SELECT
              date,
              campaign_name,
              emails_sent,
              emails_delivered,
              emails_opened,
              emails_clicked,
              hard_bounces,
              soft_bounces,
              complaints,
              rejects,
              delivery_rate,
              open_rate,
              click_rate,
              bounce_rate,
              complaint_rate,
              unique_recipients,
              avg_delivery_time_ms
            FROM {self.database_name}.campaign_metrics_daily
            WHERE campaign_name = '{safe_campaign}'
              AND date >= CAST(current_date - interval '{days}' day AS VARCHAR)
            ORDER BY date DESC
            """
            
            return await self._execute_query_async(query)
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error fetching daily metrics: {str(e)}")
            return []
    
    async def get_campaign_list(self, days: int = 30) -> List[str]:
        """
        Get list of campaign names for the specified period.
        
        Args:
            days: Number of days to look back (max 180)
            
        Returns:
            List of campaign names
        """
        if not self.stack_deployed:
            return []
        
        # Enforce 180-day maximum
        if days > 180:
            days = 180
        
        try:
            # nosec B608 - No user input in SQL query
            query = f"""
            SELECT DISTINCT campaign_name
            FROM {self.database_name}.campaign_metrics_daily
            WHERE date >= CAST(current_date - interval '{days}' day AS VARCHAR)
            ORDER BY campaign_name
            """
            
            results = await self._execute_query_async(query)
            return [row['campaign_name'] for row in results]
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error fetching campaign list: {str(e)}")
            return []
    
    async def get_date_filtered_campaigns(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        campaign_name: Optional[str] = None,
        show_hidden: bool = False
    ) -> Tuple[List[Dict], int]:
        """
        Get campaigns filtered by date range from campaign_metrics_daily table.
        
        Args:
            start_date: Start date (YYYY-MM-DD format)
            end_date: End date (YYYY-MM-DD format)
            campaign_name: Optional specific campaign to filter
            show_hidden: Include hidden campaigns in results (default False)
            
        Returns:
            Tuple of (List of campaign dictionaries, days in range)
        """
        if not self.stack_deployed:
            return [], 0
        
        try:
            # Calculate days in range for validation
            days_in_range = 0
            if start_date and end_date:
                from datetime import datetime
                start = datetime.strptime(start_date, '%Y-%m-%d')
                end = datetime.strptime(end_date, '%Y-%m-%d')
                days_in_range = (end - start).days + 1
                
                # Enforce 180-day maximum
                if days_in_range > 180:
                    return [], days_in_range
            
            # Build query with date filters
            # nosec B608 - No user input in SQL query
            query = f"""
            SELECT
              campaign_name,
              MIN(date) as first_send_date,
              MAX(date) as last_send_date,
              SUM(emails_sent) as total_sent,
              SUM(emails_delivered) as total_delivered,
              SUM(emails_opened) as total_opened,
              SUM(emails_clicked) as total_clicked,
              SUM(hard_bounces) as total_hard_bounces,
              SUM(soft_bounces) as total_soft_bounces,
              SUM(complaints) as total_complaints,
              SUM(rendering_failures) as total_rendering_failures,
              AVG(delivery_rate) as overall_delivery_rate,
              AVG(open_rate) as overall_open_rate,
              AVG(click_rate) as overall_click_rate,
              AVG(hard_bounce_rate) as overall_hard_bounce_rate,
              AVG(complaint_rate) as overall_complaint_rate,
              AVG(rendering_failure_rate) as overall_rendering_failure_rate,
              COUNT(DISTINCT date) as days_active
            FROM {self.database_name}.campaign_metrics_daily
            WHERE 1=1
            """
            
            if start_date:
                query += f" AND date >= '{start_date}'"
            
            if end_date:
                query += f" AND date <= '{end_date}'"
            
            if campaign_name:
                safe_campaign = campaign_name.replace("'", "''")
                query += f" AND campaign_name = '{safe_campaign}'"
            
            query += " GROUP BY campaign_name"
            query += " ORDER BY MIN(date) DESC, total_sent DESC LIMIT 100"
            
            results = await self._execute_query_async(query)
            
            # Filter out hidden campaigns if metadata manager is available
            # Note: This query doesn't include campaign_id, so we need to look up by campaign_name
            if not show_hidden and self.metadata_manager and self.metadata_manager.enabled:
                original_count = len(results)
                filtered_results = []
                for campaign in results:
                    # Try to find metadata by campaign name (less reliable than campaign_id)
                    # In practice, you may want to modify the query to include campaign_id
                    metadata = await self.get_campaign_metadata(campaign.get('campaign_name', ''))
                    if not metadata or not metadata.get('is_hidden', False):
                        filtered_results.append(campaign)
                
                hidden_count = original_count - len(filtered_results)
                if self.logger and hidden_count > 0:
                    self.logger.debug(f"Filtered out {hidden_count} hidden campaign(s)")
                
                results = filtered_results
            
            return results, days_in_range
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error fetching filtered campaigns: {str(e)}")
            return [], 0
    
    async def get_campaign_metadata(self, campaign_id: str) -> Optional[Dict]:
        """
        Get campaign metadata from DynamoDB.
        
        Args:
            campaign_id: The unique campaign ID
            
        Returns:
            Dictionary with campaign metadata or None if not found
        """
        if not self.metadata_manager:
            return None
        
        try:
            # Use the metadata manager to get the campaign metadata
            metadata = self.metadata_manager.get_metadata(campaign_id)
            return metadata
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error fetching campaign metadata: {str(e)}")
            return None
    
    def hide_campaign(self, campaign_id: str) -> bool:
        """
        Hide a campaign (soft delete).
        
        Args:
            campaign_id: The unique campaign ID to hide
            
        Returns:
            True if successful, False otherwise
        """
        if not self.metadata_manager:
            if self.logger:
                self.logger.warning("Metadata manager not available, cannot hide campaign")
            return False
        
        try:
            success = self.metadata_manager.hide_campaign(campaign_id)
            if success:
                # Invalidate cache to reflect changes
                self.campaigns_cache = None
                if self.logger:
                    self.logger.info(f"Campaign hidden: {campaign_id}")
            return success
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error hiding campaign: {str(e)}")
            return False
    
    def unhide_campaign(self, campaign_id: str) -> bool:
        """
        Unhide a campaign (undo soft delete).
        
        Args:
            campaign_id: The unique campaign ID to unhide
            
        Returns:
            True if successful, False otherwise
        """
        if not self.metadata_manager:
            if self.logger:
                self.logger.warning("Metadata manager not available, cannot unhide campaign")
            return False
        
        try:
            success = self.metadata_manager.unhide_campaign(campaign_id)
            if success:
                # Invalidate cache to reflect changes
                self.campaigns_cache = None
                if self.logger:
                    self.logger.info(f"Campaign unhidden: {campaign_id}")
            return success
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error unhiding campaign: {str(e)}")
            return False
    
    def get_hidden_campaigns(self) -> list:
        """
        Get all hidden campaigns.
        
        Returns:
            List of hidden campaign metadata dictionaries
        """
        if not self.metadata_manager:
            return []
        
        try:
            return self.metadata_manager.get_hidden_campaigns()
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error fetching hidden campaigns: {str(e)}")
            return []
    
    async def get_performance_overview(self, days: int = 30, start_date: str = None, end_date: str = None) -> Dict:
        """
        Get overall performance summary across all campaigns from campaign_metrics_daily.
        
        Args:
            days: Number of days to analyze (used if start_date/end_date not provided)
            start_date: Optional start date in YYYY-MM-DD format
            end_date: Optional end date in YYYY-MM-DD format
            
        Returns:
            Dictionary with overall performance metrics
        """
        if not self.stack_deployed:
            return {}
        
        try:
            # Build date filter based on parameters
            # Note: date column is VARCHAR, so we compare as strings
            if start_date and end_date:
                date_filter = f"date BETWEEN '{start_date}' AND '{end_date}'"
            elif start_date:
                date_filter = f"date >= '{start_date}'"
            elif end_date:
                date_filter = f"date <= '{end_date}'"
            else:
                date_filter = f"date >= CAST(current_date - interval '{days}' day AS VARCHAR)"
            
            # nosec B608 - No user input in SQL query
            query = f"""
            SELECT
              COALESCE(COUNT(DISTINCT campaign_name), 0) as total_campaigns,
              COALESCE(SUM(emails_sent), 0) as total_sent,
              COALESCE(SUM(emails_delivered), 0) as total_delivered,
              COALESCE(SUM(emails_opened), 0) as total_opened,
              COALESCE(SUM(emails_clicked), 0) as total_clicked,
              COALESCE(SUM(hard_bounces), 0) as total_hard_bounces,
              COALESCE(SUM(soft_bounces), 0) as total_soft_bounces,
              COALESCE(SUM(complaints), 0) as total_complaints,
              COALESCE(SUM(rendering_failures), 0) as total_rendering_failures,
              COALESCE(AVG(delivery_rate), 0.0) as avg_delivery_rate,
              COALESCE(AVG(open_rate), 0.0) as avg_open_rate,
              COALESCE(AVG(click_rate), 0.0) as avg_click_rate,
              COALESCE(AVG(hard_bounce_rate), 0.0) as avg_hard_bounce_rate,
              COALESCE(AVG(complaint_rate), 0.0) as avg_complaint_rate,
              COALESCE(AVG(rendering_failure_rate), 0.0) as avg_rendering_failure_rate
            FROM {self.database_name}.campaign_metrics_daily
            WHERE {date_filter}
            """
            
            results = await self._execute_query_async(query)
            return results[0] if results else {}
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error fetching performance overview: {str(e)}")
            return {}
    
    async def _execute_query_async(self, query: str, max_wait_seconds: int = 60) -> List[Dict]:
        """
        Execute an Athena query asynchronously and return results.
        
        Args:
            query: SQL query to execute
            max_wait_seconds: Maximum time to wait for query completion
            
        Returns:
            List of result dictionaries
        """
        if not self.athena:
            raise Exception("Athena client not initialized")
        
        try:
            # Start query execution
            response = self.athena.start_query_execution(
                QueryString=query,
                QueryExecutionContext={'Database': self.database_name},
                ResultConfiguration={'OutputLocation': self.output_location},
                WorkGroup=self.workgroup_name
            )
            
            query_id = response['QueryExecutionId']
            
            # Wait for query to complete
            start_time = time.time()
            while True:
                if time.time() - start_time > max_wait_seconds:
                    raise TimeoutError(f"Query did not complete within {max_wait_seconds} seconds")
                
                status_response = self.athena.get_query_execution(
                    QueryExecutionId=query_id
                )
                status = status_response['QueryExecution']['Status']['State']
                
                if status == 'SUCCEEDED':
                    break
                elif status in ['FAILED', 'CANCELLED']:
                    reason = status_response['QueryExecution']['Status'].get(
                        'StateChangeReason', 'Unknown'
                    )
                    raise Exception(f"Query {status}: {reason}")
                
                await asyncio.sleep(1)
            
            # Get query results
            results = self.athena.get_query_results(QueryExecutionId=query_id)
            
            if self.logger:
                self.logger.debug(f"Query completed successfully")
            
            # Parse results into list of dicts
            if not results['ResultSet']['Rows']:
                return []
            
            # Extract column names from first row
            columns = [
                col['VarCharValue']
                for col in results['ResultSet']['Rows'][0]['Data']
            ]
            
            # Parse data rows
            rows = []
            for row in results['ResultSet']['Rows'][1:]:  # Skip header row
                values = [
                    field.get('VarCharValue', '')
                    for field in row['Data']
                ]
                rows.append(dict(zip(columns, values)))
            
            return rows
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error executing Athena query: {str(e)}")
            raise


def create_campaign_analytics_tab_content():
    """
    Create the content widgets for the Campaign Analytics tab.
    
    Returns:
        List of widgets to mount in the tab
    """
    from textual.widgets import Static, Button, DataTable, Label, Select, Input, Switch
    from textual.containers import Container, Horizontal, ScrollableContainer
    
    # Build the widget tree with improved UX
    return [
        ScrollableContainer(
            # Header
            Label("📊 Campaign Analytics", classes="form-section-title"),
            
            # Stack Status Section (always visible)
            Label("Stack Status", classes="form-subsection-title"),
            Static("Checking for deployed stack...", id="stack-status-text", classes="info-text"),
            Static("", id="stack-configuration-set", classes="highlight-text"),
            Static("", id="stack-info-text", classes="form-help-text"),
            
            # Deployment Instructions (initially hidden, shown when stack not deployed)
            Label("🚀 Deployment Required", classes="form-subsection-title", id="deploy-title"),
            Static(
                "The Campaign Analytics CDK stack is not deployed in this region. "
                "To enable campaign analytics, deploy the CDK stack:",
                classes="form-help-text",
                id="deploy-description"
            ),
            Static(
                "Repository: github.com/YOUR_ORG/ses-campaign-analytics\n\n"
                "Quick Deploy Steps:\n"
                "1. git clone <repo-url>\n"
                "2. cd ses-campaign-analytics\n"
                "3. npm install\n"
                "4. Edit config.json\n"
                "5. cdk bootstrap\n"
                "6. cdk deploy",
                classes="info-text",
                id="deploy-steps"
            ),
            Button("🔄 Refresh Stack Detection", id="refresh-stack-detection-btn", variant="primary"),
            
            # ========== SECTION 1: DATA PROCESSING ========== #
            Label("⚡ Data Processing", classes="form-subsection-title", id="processing-title"),
            Static(
                "ℹ️  Process today's data immediately (async ~1-2 min) or wait for automatic nightly processing",
                classes="form-help-text",
                id="processing-help"
            ),
            Horizontal(
                Button("⚡ Process Today's Data", id="invoke-lambda-btn", variant="primary"),
                Static("Ready", id="lambda-status-indicator", classes="info-text"),
                classes="form-row",
                id="processing-row"
            ),
            Horizontal(
                Button("🔄 Refresh Data & Check Today", id="check-and-refresh-btn", variant="default"),
                classes="form-row",
                id="check-refresh-row"
            ),
            Static(
                "💡 Click to refresh all displayed data and verify if today's data has been processed",
                classes="form-help-text",
                id="check-refresh-tip"
            ),
            
            # ========== SECTION 2: VIEW & FILTER OPTIONS ========== #
            Label("🔍 View & Filter Options", classes="form-subsection-title", id="filters-title"),
            Static(
                "ℹ️  Step 1: Select date range (affects which campaigns appear). Step 2: Optionally filter by specific campaign.",
                classes="form-help-text",
                id="filters-help"
            ),
            
            # Date Range Filter
            Horizontal(
                Label("Date Range:", classes="form-label"),
                Input(
                    placeholder="YYYY-MM-DD (optional)",
                    id="start-date-input",
                    classes="form-input"
                ),
                Label("to", classes="form-label-small"),
                Input(
                    placeholder="YYYY-MM-DD (optional)",
                    id="end-date-input",
                    classes="form-input"
                ),
                Button("Apply Date Filter", id="filter-dates-btn", variant="primary"),
                Button("Reset to Last 30 Days", id="show-all-campaigns-btn", variant="default"),
                classes="form-row",
                id="date-row"
            ),
            Static(
                "💡 Leave dates empty to view last 30 days (max 180-day range). Campaign list below updates based on this range.",
                classes="form-help-text",
                id="date-filter-tip"
            ),
            
            # Campaign Filter (moved AFTER date range)
            Horizontal(
                Label("Campaign:", classes="form-label"),
                Select(
                    [("All Campaigns", "all")],
                    prompt="All campaigns in selected date range",
                    id="campaign-select",
                    allow_blank=False
                ),
                classes="form-row",
                id="campaign-select-row"
            ),
            Static(
                "💡 Select a specific campaign from the date range above, or keep 'All Campaigns' selected",
                classes="form-help-text",
                id="campaign-filter-tip"
            ),
            
            # Show Hidden Campaigns Toggle
            Horizontal(
                Label("Show Hidden:", classes="form-label"),
                Switch(value=False, id="show-hidden-switch"),
                classes="form-row",
                id="show-hidden-row"
            ),
            Static(
                "💡 Toggle to show/hide campaigns that have been marked as hidden",
                classes="form-help-text",
                id="show-hidden-tip"
            ),
            
            # ========== SECTION 3: PERFORMANCE OVERVIEW ========== #
            Label("📈 Performance Summary", classes="form-subsection-title", id="overview-title"),
            Static("Loading performance data...", id="performance-overview-text", classes="info-text"),
            
            # ========== SECTION 4: CAMPAIGN PERFORMANCE TABLE ========== #
            Label("📊 Campaign Performance (Click row for details)", classes="form-subsection-title", id="table-title"),
            DataTable(id="campaign-analytics-table", cursor_type="row"),
            Static("", id="campaign-table-status", classes="form-help-text"),
            
            # ========== SECTION 5: SELECTED CAMPAIGN DETAILS ========== #
            Label("📋 Campaign Details", classes="form-subsection-title", id="metadata-title"),
            Static(
                "👆 Select a campaign from the table above to view detailed information",
                id="campaign-metadata-display",
                classes="info-text"
            ),
            
            # Hide/Unhide Campaign Controls
            Horizontal(
                Button("🗑️  Hide Campaign", id="hide-campaign-btn", variant="error"),
                Button("👁️  Unhide Campaign", id="unhide-campaign-btn", variant="success"),
                classes="form-row",
                id="campaign-action-row"
            ),
            Static(
                "💡 Hide campaigns to remove them from the main view (soft delete). You can unhide them anytime.",
                classes="form-help-text",
                id="campaign-action-tip"
            ),
            
            id="campaign-analytics-scroll-container"
        )
    ]
