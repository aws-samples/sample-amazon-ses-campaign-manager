"""
Scheduled Campaigns UI Module
Provides UI for viewing and managing scheduled campaigns
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from textual.widgets import Static, Button, DataTable, Label
from textual.containers import Container, ScrollableContainer, Horizontal

from modules.logger import get_logger
from modules.notification_helper import notify_verbose


class ScheduledCampaignsUI:
    """Manages the scheduled campaigns UI tab"""
    
    def __init__(self, app, scheduled_campaigns_manager):
        """
        Initialize the Scheduled Campaigns UI
        
        Args:
            app: The main TUI application instance
            scheduled_campaigns_manager: ScheduledCampaignsManager instance
        """
        self.app = app
        self.manager = scheduled_campaigns_manager
        self.logger = get_logger()
        self.selected_campaign = None
    
    async def load_scheduled_campaigns(self) -> None:
        """Load and display scheduled campaigns"""
        try:
            if not self.manager or not self.manager.is_deployed:
                self.app.notify("Scheduled campaigns stack not deployed", severity="warning")
                return
            
            # Get only SCHEDULED campaigns
            campaigns = self.manager.list_campaigns(status_filter='SCHEDULED', limit=100)
            
            if campaigns is None:
                self.app.notify("Error loading scheduled campaigns", severity="error")
                return
            
            # Update the table
            table = self.app.query_one("#scheduled-campaigns-table", DataTable)
            table.clear(columns=True)
            
            # Add columns
            table.add_columns(
                "Campaign Name",
                "Campaign ID",
                "Scheduled Time",
                "Template",
                "From Email",
                "Recipients",
                "Status"
            )
            
            # Add rows
            for campaign in campaigns:
                try:
                    # Format scheduled time
                    schedule_ts = campaign.get('schedule_timestamp', 0)
                    if isinstance(schedule_ts, (int, float)):
                        scheduled_time = datetime.fromtimestamp(schedule_ts).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        scheduled_time = str(schedule_ts)
                    
                    table.add_row(
                        campaign.get('campaign_name', 'N/A'),
                        campaign.get('campaign_id', 'N/A'),
                        scheduled_time,
                        campaign.get('template_name', 'N/A'),
                        campaign.get('from_email', 'N/A'),
                        str(campaign.get('total_recipients', 0)),
                        campaign.get('status', 'UNKNOWN')
                    )
                except Exception as row_error:
                    if self.logger:
                        self.logger.warning(f"Error adding campaign row: {str(row_error)}")
            
            # Update status text
            status_text = self.app.query_one("#scheduled-campaigns-status", Static)
            status_text.update(f"Found {len(campaigns)} scheduled campaigns")
            
            # Clear selection display
            details_text = self.app.query_one("#scheduled-campaign-details", Static)
            details_text.update("👆 Select a campaign from the table above to view details and delete options")
            
            self.selected_campaign = None
            
            self.app.notify(f"Loaded {len(campaigns)} scheduled campaigns", severity="success")
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error loading scheduled campaigns: {str(e)}")
            self.app.notify(f"Error loading scheduled campaigns: {str(e)}", severity="error")
    
    async def handle_campaign_row_selected(self, event) -> None:
        """Handle scheduled campaign table row selection"""
        try:
            # Get the row data
            row_key = event.row_key
            table = event.data_table
            
            # Get campaign data from the selected row
            row_data = table.get_row(row_key)
            campaign_name = str(row_data[0])
            campaign_id = str(row_data[1])
            scheduled_time = str(row_data[2])
            template_name = str(row_data[3])
            from_email = str(row_data[4])
            recipients = str(row_data[5])
            status = str(row_data[6])
            
            # Store selected campaign info
            self.selected_campaign = {
                'campaign_name': campaign_name,
                'campaign_id': campaign_id,
                'scheduled_time': scheduled_time,
                'template_name': template_name,
                'from_email': from_email,
                'recipients': recipients,
                'status': status
            }
            
            # Get full campaign details from manager
            campaigns = self.manager.list_campaigns(status_filter='SCHEDULED', limit=100)
            full_campaign = None
            if campaigns:
                for c in campaigns:
                    if c.get('campaign_id') == campaign_id:
                        full_campaign = c
                        break
            
            # Store schedule_timestamp and CSV path for deletion
            if full_campaign:
                self.selected_campaign['schedule_timestamp'] = full_campaign.get('schedule_timestamp')
                self.selected_campaign['csv_s3_path'] = full_campaign.get('csv_s3_path', '')
            
            # Display campaign details
            details_text = self.app.query_one("#scheduled-campaign-details", Static)
            
            display = f"📋 Selected Campaign Details\n"
            display += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            display += f"🏷️  Campaign Name: {campaign_name}\n"
            display += f"🆔 Campaign ID: {campaign_id}\n"
            display += f"📅 Scheduled Time: {scheduled_time}\n"
            display += f"📧 Template: {template_name}\n"
            display += f"📨 From: {from_email}\n"
            display += f"👥 Recipients: {recipients}\n"
            display += f"📊 Status: {status}\n\n"
            
            if full_campaign:
                if full_campaign.get('configuration_set'):
                    display += f"⚙️  Configuration Set: {full_campaign.get('configuration_set')}\n"
                
                csv_path = full_campaign.get('csv_s3_path', '')
                if csv_path:
                    display += f"📂 CSV Path: {csv_path}\n"
                
                created_at = full_campaign.get('created_at', 0)
                if created_at:
                    created_time = datetime.fromtimestamp(created_at).strftime('%Y-%m-%d %H:%M:%S')
                    display += f"🕐 Created: {created_time}\n"
            
            display += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            display += f"⚠️  Click 'Delete Selected Campaign' to permanently remove this campaign.\n"
            display += f"This will delete the EventBridge rule and all DynamoDB entries."
            
            details_text.update(display)
            
            # Enable delete button
            try:
                delete_btn = self.app.query_one("#delete-scheduled-campaign-btn", Button)
                delete_btn.disabled = False
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error displaying campaign details: {str(e)}")
            self.app.notify(f"Error loading campaign details: {str(e)}", severity="error")
    
    async def handle_delete_campaign(self) -> None:
        """Handle delete campaign button"""
        try:
            if not self.selected_campaign:
                self.app.notify("Please select a campaign to delete", severity="warning")
                return
            
            campaign_id = self.selected_campaign.get('campaign_id')
            campaign_name = self.selected_campaign.get('campaign_name')
            schedule_timestamp = self.selected_campaign.get('schedule_timestamp')
            
            if not campaign_id or not schedule_timestamp:
                self.app.notify("Invalid campaign data", severity="error")
                return
            
            # Confirm deletion
            from textual.screen import ModalScreen
            from textual.widgets import Label, Button as ModalButton
            from textual.containers import Vertical, Horizontal as ModalHorizontal
            
            class ConfirmDeleteScreen(ModalScreen):
                """Confirmation dialog for campaign deletion"""
                
                def __init__(self, campaign_name: str):
                    super().__init__()
                    self.campaign_name = campaign_name
                    self.confirmed = False
                
                def compose(self):
                    yield Vertical(
                        Label(f"⚠️  Delete Scheduled Campaign?", classes="dialog-title"),
                        Label(
                            f"Campaign: {self.campaign_name}\n\n"
                            f"This will delete the campaign from DynamoDB.\n"
                            f"Cleanup Lambda will automatically remove:\n"
                            f"• EventBridge scheduled rule\n"
                            f"• CSV file from S3\n"
                            f"• Analytics metadata entry (if exists)\n\n"
                            f"⚠️  Cleanup happens in seconds via DynamoDB Stream.\n"
                            f"This action cannot be undone!",
                            classes="dialog-text"
                        ),
                        ModalHorizontal(
                            ModalButton("❌ Delete", variant="error", id="confirm-delete"),
                            ModalButton("Cancel", variant="default", id="cancel-delete"),
                            classes="dialog-buttons"
                        ),
                        classes="dialog-container"
                    )
                
                def on_button_pressed(self, event):
                    if event.button.id == "confirm-delete":
                        self.confirmed = True
                        self.dismiss(True)
                    else:
                        self.dismiss(False)
            
            # Show confirmation dialog
            confirmed = await self.app.push_screen_wait(ConfirmDeleteScreen(campaign_name))
            
            if not confirmed:
                self.app.notify("Deletion cancelled", severity="information")
                return
            
            # Perform deletion
            self.app.notify(f"Deleting campaign: {campaign_name}...", severity="information")
            
            success = self.manager.delete_campaign(
                campaign_id=campaign_id,
                schedule_timestamp=int(schedule_timestamp),
                csv_s3_path=self.selected_campaign.get('csv_s3_path')
            )
            
            if success:
                self.app.notify(
                    f"✅ Campaign '{campaign_name}' deletion initiated!\n"
                    f"DynamoDB entry removed. Cleanup Lambda processing EventBridge rule and CSV file.",
                    severity="success",
                    timeout=8
                )
                
                # Reload the campaigns list
                await self.load_scheduled_campaigns()
            else:
                self.app.notify(
                    f"❌ Failed to delete campaign '{campaign_name}'.\n"
                    f"Check logs for details.",
                    severity="error",
                    timeout=8
                )
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error deleting campaign: {str(e)}")
            self.app.notify(f"Error deleting campaign: {str(e)}", severity="error")
    
    async def handle_refresh_campaigns(self) -> None:
        """Handle refresh campaigns button"""
        try:
            self.app.notify("Refreshing scheduled campaigns...", severity="information")
            await self.load_scheduled_campaigns()
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error refreshing campaigns: {str(e)}")
            self.app.notify(f"Error refreshing: {str(e)}", severity="error")
    
    async def update_stack_status(self) -> None:
        """Update the stack status display"""
        try:
            status_text = self.app.query_one("#scheduled-stack-status", Static)
            info_text = self.app.query_one("#scheduled-stack-info", Static)
            
            if self.manager and self.manager.is_deployed:
                stack_info = self.manager.get_stack_info()
                sched_info = stack_info.get('scheduled_campaigns', {})
                
                status_text.update("✅ Scheduled Campaigns Stack Deployed")
                
                info = f"S3 Bucket: {sched_info.get('bucket_name', 'N/A')}\n"
                info += f"DynamoDB Table: {sched_info.get('table_name', 'N/A')}\n"
                info += f"Region: {sched_info.get('region', 'N/A')}"
                info_text.update(info)
                
                # Show main content, hide deployment message
                try:
                    self.app.query_one("#scheduled-campaigns-main-content").display = True
                    self.app.query_one("#scheduled-campaigns-deploy-message").display = False
                except:
                    pass  # nosec B110 - Intentional - widget may not be mounted
                
                # Load campaigns
                await self.load_scheduled_campaigns()
                
            else:
                status_text.update("❌ Scheduled Campaigns Stack Not Deployed")
                info_text.update(
                    "The scheduled campaigns stack is required to view and manage scheduled campaigns.\n"
                    "Deploy the ses_scheduled_campaigns CDK stack to use this feature."
                )
                
                # Hide main content, show deployment message
                try:
                    self.app.query_one("#scheduled-campaigns-main-content").display = False
                    self.app.query_one("#scheduled-campaigns-deploy-message").display = True
                except:
                    pass  # nosec B110 - Intentional - widget may not be mounted
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error updating stack status: {str(e)}")


def create_scheduled_campaigns_tab_content():
    """Create the content widgets for the Scheduled Campaigns tab"""
    return [
        ScrollableContainer(
            # Header
            Label("🗓️  Scheduled Email Campaigns", classes="form-section-title"),
            
            # Stack Status
            Label("Stack Status", classes="form-subsection-title"),
            Static("Checking for deployed stack...", id="scheduled-stack-status", classes="info-text"),
            Static("", id="scheduled-stack-info", classes="form-help-text"),
            
            # Deployment Message (shown when stack not deployed)
            Container(
                Label("🚀 Deployment Required", classes="form-subsection-title"),
                Static(
                    "The Scheduled Campaigns CDK stack is not deployed.\n\n"
                    "To schedule campaigns for future execution, deploy the stack:\n"
                    "Repository: ses_scheduled_campaigns\n\n"
                    "Quick Deploy:\n"
                    "1. cd ses_scheduled_campaigns\n"
                    "2. npm install\n"
                    "3. Edit config.json\n"
                    "4. cdk deploy",
                    classes="info-text"
                ),
                id="scheduled-campaigns-deploy-message",
                classes="form-section"
            ),
            
            # Main Content (shown when stack is deployed)
            Container(
                # Actions
                Label("Actions", classes="form-subsection-title"),
                Horizontal(
                    Button("🔄 Refresh List", id="refresh-scheduled-campaigns-btn", variant="primary"),
                    Button("❌ Delete Selected", id="delete-scheduled-campaign-btn", variant="error", disabled=True),
                    classes="form-row"
                ),
                Static(
                    "💡 Select a campaign from the table below to view details and enable deletion",
                    classes="form-help-text"
                ),
                
                # Campaigns Table
                Label("📋 Upcoming Scheduled Campaigns (Click row for details)", classes="form-subsection-title"),
                DataTable(id="scheduled-campaigns-table", cursor_type="row"),
                Static("", id="scheduled-campaigns-status", classes="form-help-text"),
                Static(
                    "ℹ️  Only showing campaigns scheduled in the future. Past campaigns are automatically hidden.",
                    classes="form-help-text"
                ),
                
                # Selected Campaign Details
                Label("📄 Campaign Details", classes="form-subsection-title"),
                Static(
                    "👆 Select a campaign from the table above to view details and delete options",
                    id="scheduled-campaign-details",
                    classes="info-text"
                ),
                
                id="scheduled-campaigns-main-content"
            ),
            
            id="scheduled-campaigns-scroll-container"
        )
    ]
