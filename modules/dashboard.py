#!/usr/bin/env python3
"""
Dashboard Module
Handles the main dashboard functionality with SES account details and CloudWatch metrics
"""

from datetime import datetime, timedelta

from textual.containers import ScrollableContainer, Horizontal
from textual.widgets import Static, Button, Select, DataTable, Label

from config.settings import settings
from modules.cache_manager import CacheManager, CachedAPIWrapper
from modules.logger import get_logger
from modules.notification_helper import notify_verbose


class DashboardManager:
    """Manages dashboard operations and UI interactions."""
    
    def __init__(self, app, ses_client = None):
        self.app = app
        self.ses_client = ses_client
        self.account_details = {}
        self.metrics_data = {}
        self.cloudwatch_client = None
        
        # Initialize caching
        self.cache_manager = CacheManager(settings)
        self.cached_api = CachedAPIWrapper(self.cache_manager)
        
        # Debug logging
        logger = get_logger()
        if logger:
            logger.debug("Initializing Dashboard Manager", "DASHBOARD")
        
        # Initialize CloudWatch client if SES client is provided
        if ses_client and ses_client.session:
            try:
                self.cloudwatch_client = ses_client.session.client('cloudwatch', region_name=ses_client.region_name)
                if logger:
                    logger.debug(f"CloudWatch client initialized for region: {ses_client.region_name}", "DASHBOARD")
            except Exception as e:
                if logger:
                    logger.warning(f"Failed to initialize CloudWatch client: {str(e)}", "DASHBOARD")
                # CloudWatch client initialization failed, but continue without it
                pass  # nosec B110 - Intentional - widget may not be mounted
        elif logger:
            logger.debug("No SES client provided, CloudWatch client not initialized", "DASHBOARD")
        
    def set_ses_client(self, ses_client):
        """Set the SES client for dashboard operations."""
        self.ses_client = ses_client
        # Initialize CloudWatch client using the same session
        if ses_client and ses_client.session:
            try:
                self.cloudwatch_client = ses_client.session.client('cloudwatch', region_name=ses_client.region_name)
            except Exception as e:
                # CloudWatch client initialization failed, but continue without it
                pass  # nosec B110 - Intentional - widget may not be mounted
    
    async def refresh_dashboard_data(self) -> None:
        """Refresh all dashboard data from AWS."""
        if not self.ses_client:
            return
        
        try:
            # Refresh account details
            await self.refresh_account_details()
            # Refresh metrics
            await self.refresh_metrics_data()
            # Update the dashboard display
            await self.update_dashboard_display()
        except Exception as e:
            self.app.notify(f"Error refreshing dashboard data: {str(e)}", severity="error")
    
    async def refresh_account_details(self, force_refresh: bool = False) -> None:
        """Refresh SES account details with caching support."""
        if not self.ses_client:
            return
        
        def _fetch_account_details():
            # Get account details using SES v2 API
            account_details = {}
            
            # Get account information using SES v2 API
            try:
                account_response = self.ses_client.ses_client.get_account()
                account_details.update(account_response)
            except Exception as e:
                # Set default values if account info is not available
                account_details['EnforcementStatus'] = 'Unknown'
                account_details['ProductionAccessEnabled'] = False
                account_details['SendQuota'] = {
                    'Max24HourSend': 'N/A',
                    'MaxSendRate': 'N/A', 
                    'SentLast24Hours': 'N/A'
                }
            
            # Get suppression list info using SES v2 API
            try:
                suppression_response = self.ses_client.ses_client.list_suppressed_destinations()
                account_details['SuppressionListCount'] = len(suppression_response.get('SuppressedDestinationSummaries', []))
            except Exception as e:
                account_details['SuppressionListCount'] = 'N/A'
            
            return account_details
            
        try:
            # Use cached API wrapper for account details
            ttl_minutes = settings.get('cache.dashboard_ttl_minutes', 10)
            self.account_details = self.cached_api.cached_call(
                operation_name="get_account_details",
                api_function=_fetch_account_details,
                ttl_minutes=ttl_minutes,
                force_refresh=force_refresh
            )
                
        except Exception as e:
            self.app.notify(f"Error fetching account details: {str(e)}", severity="error")
            self.account_details = {}
    
    async def refresh_metrics_data(self, time_period: str = "24h", force_refresh: bool = False) -> None:
        """Refresh CloudWatch metrics data with caching support."""
        if not self.cloudwatch_client:
            return
        
        def _fetch_metrics_data(time_period=time_period):
            # Calculate time range
            end_time = datetime.utcnow()
            if time_period == "1h":
                start_time = end_time - timedelta(hours=1)
                period = 300  # 5 minute intervals
            elif time_period == "24h":
                start_time = end_time - timedelta(hours=24)
                period = 3600  # 1 hour intervals
            elif time_period == "2d":
                start_time = end_time - timedelta(days=2)
                period = 3600 * 3  # 3 hour intervals
            elif time_period == "7d":
                start_time = end_time - timedelta(days=7)
                period = 3600 * 12  # 12 hour intervals
            elif time_period == "30d":
                start_time = end_time - timedelta(days=30)
                period = 3600 * 24  # 24 hour intervals
            else:
                start_time = end_time - timedelta(hours=24)
                period = 3600
            
            # SES metrics to fetch
            metrics_to_fetch = [
                'Send',
                'Delivery',
                'Open',
                'RenderingFailure',
                'Reputation.BounceRate',
                'Reputation.ComplaintRate'
            ]
            
            metrics_data = {}
            
            for metric_name in metrics_to_fetch:
                try:
                    response = self.cloudwatch_client.get_metric_statistics(
                        Namespace='AWS/SES',
                        MetricName=metric_name,
                        StartTime=start_time,
                        EndTime=end_time,
                        Period=period,
                        Statistics=['Sum'] if metric_name in ['Send', 'Delivery', 'Open', 'RenderingFailure'] else ['Average']
                    )
                    
                    # Process the data points
                    datapoints = response.get('Datapoints', [])
                    if datapoints:
                        # Sort by timestamp
                        datapoints.sort(key=lambda x: x['Timestamp'])
                        
                        # Calculate total/average based on metric type
                        if metric_name in ['Send', 'Delivery', 'Open', 'RenderingFailure']:
                            total_value = sum(dp.get('Sum', 0) for dp in datapoints)
                            metrics_data[metric_name] = {
                                'value': total_value,
                                'type': 'count',
                                'datapoints': datapoints
                            }
                        else:  # Reputation metrics
                            # Get the latest value for reputation metrics
                            latest_value = datapoints[-1].get('Average', 0) if datapoints else 0
                            metrics_data[metric_name] = {
                                'value': latest_value,
                                'type': 'percentage',
                                'datapoints': datapoints
                            }
                    else:
                        metrics_data[metric_name] = {
                            'value': 0,
                            'type': 'count' if metric_name in ['Send', 'Delivery', 'Open', 'RenderingFailure'] else 'percentage',
                            'datapoints': []
                        }
                        
                except Exception as e:
                    metrics_data[metric_name] = {
                        'value': 'Error',
                        'type': 'count',
                        'datapoints': []
                    }
            
            return metrics_data
            
        try:
            # Use cached API wrapper for metrics data
            ttl_minutes = settings.get('cache.metrics_ttl_minutes', 5)
            params = {'time_period': time_period}
            self.metrics_data = self.cached_api.cached_call(
                operation_name="get_metrics_data",
                api_function=_fetch_metrics_data,
                params=params,
                ttl_minutes=ttl_minutes,
                force_refresh=force_refresh
            )
                    
        except Exception as e:
            self.app.notify(f"Error fetching metrics data: {str(e)}", severity="error")
            self.metrics_data = {}
    
    async def update_dashboard_display(self) -> None:
        """Update the dashboard display with current data."""
        logger = get_logger()
        try:
            if logger:
                logger.debug("Updating dashboard display", "DASHBOARD")
            
            # Update account details display
            await self.update_account_details_display()
            # Update metrics display
            await self.update_metrics_display()
            
            if logger:
                logger.debug("Dashboard display updated successfully", "DASHBOARD")
                
        except Exception as e:
            if logger:
                logger.error(f"Error updating dashboard display: {str(e)}", "DASHBOARD")
            self.app.notify(f"Error updating dashboard display: {str(e)}", severity="error")
    
    async def update_account_details_display(self) -> None:
        """Update the account details section using DataTable."""
        try:
            table = self.app.query_one("#account-table", DataTable)
        except:
            # Table doesn't exist yet, skip update
            return
            
        table.clear(columns=True)
        
        if not self.account_details:
            table.add_columns("Account Information", "Status")
            table.add_row("📊 Connection", "Connecting to Amazon SES...")
            return
        
        # Add columns
        table.add_columns("Account Information", "Value")
        
        # Status information
        enforcement_status = self.account_details.get('EnforcementStatus', 'Unknown')
        status_icon = "🟢" if enforcement_status == "HEALTHY" else "🔴"
        table.add_row(f"{status_icon} Enforcement Status", enforcement_status)
        
        # Production access
        production_access = self.account_details.get('ProductionAccessEnabled', False)
        access_text = "Production" if production_access else "Sandbox"
        access_icon = "✅" if production_access else "⚠️"
        table.add_row(f"{access_icon} Access Mode", access_text)
        
        # Sending quotas
        send_quota = self.account_details.get('SendQuota', {})
        if send_quota:
            max_24h = send_quota.get('Max24HourSend', 'N/A')
            max_rate = send_quota.get('MaxSendRate', 'N/A')
            sent_24h = send_quota.get('SentLast24Hours', 'N/A')
            
            # Format numbers nicely
            if isinstance(max_24h, (int, float)):
                max_24h_formatted = f"{int(max_24h):,}"
            else:
                max_24h_formatted = str(max_24h)
                
            if isinstance(max_rate, (int, float)):
                max_rate_formatted = f"{max_rate}/sec"
            else:
                max_rate_formatted = str(max_rate)
                
            if isinstance(sent_24h, (int, float)):
                sent_24h_formatted = f"{int(sent_24h):,}"
            else:
                sent_24h_formatted = str(sent_24h)
            
            table.add_row("📊 Max 24h Send Limit", max_24h_formatted)
            table.add_row("⚡ Max Send Rate", max_rate_formatted)
            table.add_row("📤 Sent Last 24h", sent_24h_formatted)
        else:
            table.add_row("📊 Sending Quotas", "Not available")
        
        # Suppression list info
        suppression_count = self.account_details.get('SuppressionListCount', 'N/A')
        if isinstance(suppression_count, int):
            suppression_text = f"{suppression_count:,} addresses"
        else:
            suppression_text = str(suppression_count)
        table.add_row("🚫 Suppression List", suppression_text)
    
    async def update_metrics_display(self) -> None:
        """Update the metrics display section using DataTable."""
        try:
            table = self.app.query_one("#metrics-table", DataTable)
        except:
            # Table doesn't exist yet, skip update
            return
            
        table.clear(columns=True)
        
        if not self.metrics_data:
            table.add_columns("Metric", "Value")
            table.add_row("📤 Emails Sent", "0")
            table.add_row("✅ Delivered", "0")
            table.add_row("👁️ Opened", "0")
            table.add_row("❌ Rendering Failures", "0")
            table.add_row("⚠️ Bounce Rate", "0.00%")
            table.add_row("🚨 Complaint Rate", "0.00%")
            return
        
        # Add columns
        table.add_columns("Metric", "Value")
        
        # Define metric display order and formatting
        metric_info = {
            'Send': {'label': '📤 Emails Sent', 'format': 'count'},
            'Delivery': {'label': '✅ Delivered', 'format': 'count'},
            'Open': {'label': '👁️ Opened', 'format': 'count'},
            'RenderingFailure': {'label': '❌ Rendering Failures', 'format': 'count'},
            'Reputation.BounceRate': {'label': '⚠️ Bounce Rate', 'format': 'percentage'},
            'Reputation.ComplaintRate': {'label': '🚨 Complaint Rate', 'format': 'percentage'}
        }
        
        # Add metric rows
        for metric_name, info in metric_info.items():
            label = info['label']
            
            if metric_name in self.metrics_data:
                metric_data = self.metrics_data[metric_name]
                value = metric_data['value']
                
                if value == 'Error':
                    formatted_value = "Error fetching data"
                elif info['format'] == 'percentage':
                    if isinstance(value, (int, float)):
                        formatted_value = f"{value:.2%}"
                    else:
                        formatted_value = str(value)
                else:  # count format
                    if isinstance(value, (int, float)):
                        formatted_value = f"{int(value):,}"
                    else:
                        formatted_value = str(value)
            else:
                formatted_value = "No data"
            
            table.add_row(label, formatted_value)
        
        # Add calculated metrics if available
        calculated_metrics = []
        
        # Calculate delivery rate if we have both send and delivery data
        if 'Send' in self.metrics_data and 'Delivery' in self.metrics_data:
            send_count = self.metrics_data['Send']['value']
            delivery_count = self.metrics_data['Delivery']['value']
            if isinstance(send_count, (int, float)) and isinstance(delivery_count, (int, float)) and send_count > 0:
                delivery_rate = delivery_count / send_count
                calculated_metrics.append(('📊 Delivery Rate', f"{delivery_rate:.2%}"))
        
        # Calculate open rate if we have both delivery and open data
        if 'Delivery' in self.metrics_data and 'Open' in self.metrics_data:
            delivery_count = self.metrics_data['Delivery']['value']
            open_count = self.metrics_data['Open']['value']
            if isinstance(delivery_count, (int, float)) and isinstance(open_count, (int, float)) and delivery_count > 0:
                open_rate = open_count / delivery_count
                calculated_metrics.append(('📊 Open Rate', f"{open_rate:.2%}"))
        
        # Add calculated metrics to table if any exist
        for label, value in calculated_metrics:
            table.add_row(label, value)
    
    async def handle_time_period_change(self, time_period: str) -> None:
        """Handle time period selection change."""
        try:
            # Update the metrics display title
            period_labels = {
                "1h": "Past 1 Hour",
                "24h": "Past 24 Hours",
                "2d": "Past 2 Days", 
                "7d": "Past 7 Days",
                "30d": "Past 1 Month"
            }
            
            # Refresh metrics with new time period
            await self.refresh_metrics_data(time_period)
            
            # Update the display
            await self.update_metrics_display()
            
            # Update the metrics title
            try:
                metrics_display = self.app.query_one("#metrics-display", Static)
                current_text = metrics_display.renderable
                if isinstance(current_text, str):
                    # Replace the time period in the title
                    lines = current_text.split('\n')
                    if lines:
                        lines[0] = f"📈 Email Engagement Metrics ({period_labels.get(time_period, time_period)})"
                        metrics_display.update('\n'.join(lines))
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
                
            notify_verbose(self.app, f"Updated metrics for {period_labels.get(time_period, time_period)}", severity="information")
            
        except Exception as e:
            self.app.notify(f"Error updating time period: {str(e)}", severity="error")


def create_dashboard_tab_content():
    """Create the content for the dashboard tab."""
    return [
        ScrollableContainer(
            Static("🎉 Welcome to Amazon SES Campaign Manager", classes="section-title"),
            Static("Terminal-based email campaign management for Amazon Simple Email Service", classes="welcome-subtitle"),
            Static("📊 Account Information", classes="section-title"),
            DataTable(id="account-table", cursor_type="none"),
            Static("📈 Email Engagement Metrics", classes="section-title"),
            Label("Time Period:"),
            Select(
                [("Past 1 Hour", "1h"), ("Past 24 Hours", "24h"), ("Past 2 Days", "2d"), ("Past 7 Days", "7d"), ("Past 1 Month", "30d")],
                id="metrics-time-period",
                value="24h"
            ),
            DataTable(id="metrics-table", cursor_type="none"),
            Static(
                "ℹ️ Data Source: Metrics are fetched from AWS CloudWatch using the SES namespace. "
                "Data includes email sends, deliveries, opens, rendering failures, and reputation metrics (bounce/complaint rates). "
                "Metrics are cached for 5 minutes to optimize performance. Use the time period selector above to view different time ranges.",
                classes="metrics-info-note"
            ),
            Horizontal(
                Button("Refresh Dashboard", variant="primary", id="refresh-dashboard"),
                classes="dashboard-actions"
            ),
            id="dashboard-scroll-container"
        )
    ]
