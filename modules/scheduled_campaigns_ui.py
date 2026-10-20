"""
Scheduled Campaigns UI Module
Provides UI for viewing and managing scheduled campaigns
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from textual.screen import ModalScreen
from textual.widgets import Static, Button, DataTable, Label
from textual.containers import Container, ScrollableContainer, Horizontal, Vertical

from modules.logger import get_logger
from modules.notification_helper import notify_verbose


# Statuses where a campaign can still be cancelled (SCHEDULED) or stopped (PROCESSING)
CANCELLABLE_STATUSES = {'SCHEDULED', 'PROCESSING'}

STATUS_LABELS = {
    'SCHEDULED': '🗓️  SCHEDULED',
    'PROCESSING': '📤 SENDING',
    'COMPLETED': '✅ COMPLETED',
    'FAILED': '❌ FAILED',
    'STOPPED': '⏹️  STOPPED',
    'CANCELLED': '🚫 CANCELLED',
}


class ConfirmActionScreen(ModalScreen):
    """Confirmation dialog for destructive campaign actions"""

    def __init__(self, title: str, text: str, confirm_label: str):
        super().__init__()
        self.title_text = title
        self.body_text = text
        self.confirm_label = confirm_label

    def compose(self):
        yield Vertical(
            Label(self.title_text, classes="dialog-title"),
            Label(self.body_text, classes="dialog-text"),
            Horizontal(
                Button(self.confirm_label, variant="error", id="confirm-action"),
                Button("Cancel", variant="default", id="cancel-action"),
                classes="dialog-buttons"
            ),
            classes="dialog-container"
        )

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "confirm-action")


def format_timestamp(timestamp) -> str:
    """Format a Unix timestamp in local time, or '-' if unset"""
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    return '-'


def format_progress(campaign: Dict[str, Any]) -> str:
    """Sent/failed progress for the table"""
    total = campaign.get('total_recipients', 0)
    if campaign.get('status') == 'SCHEDULED':
        return f"0 / {total}"
    sent = campaign.get('sent_count', 0)
    failed = campaign.get('failed_count', 0)
    progress = f"{sent} sent / {total}"
    if failed:
        progress += f" ({failed} failed)"
    return progress


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
        self.campaigns_by_id: Dict[str, Dict[str, Any]] = {}

    async def load_scheduled_campaigns(self) -> None:
        """Load and display campaigns of every status"""
        try:
            if not self.manager or not self.manager.is_deployed:
                self.app.notify("Scheduled campaigns stack not deployed", severity="warning")
                return

            # All statuses, most recent schedule first
            campaigns = self.manager.list_campaigns(limit=100, future_only=False)

            if campaigns is None:
                self.app.notify("Error loading scheduled campaigns", severity="error")
                return

            self.campaigns_by_id = {c.get('campaign_id'): c for c in campaigns if c.get('campaign_id')}

            # Update the table
            table = self.app.query_one("#scheduled-campaigns-table", DataTable)
            table.clear(columns=True)

            # Add columns
            table.add_columns(
                "Campaign Name",
                "Campaign ID",
                "Scheduled Time",
                "Status",
                "Progress",
                "Template",
                "From Email"
            )

            # Add rows
            for campaign in campaigns:
                try:
                    table.add_row(
                        campaign.get('campaign_name', 'N/A'),
                        campaign.get('campaign_id', 'N/A'),
                        format_timestamp(campaign.get('schedule_timestamp', 0)),
                        STATUS_LABELS.get(campaign.get('status'), campaign.get('status', 'UNKNOWN')),
                        format_progress(campaign),
                        campaign.get('template_name', 'N/A'),
                        campaign.get('from_email', 'N/A'),
                        key=campaign.get('campaign_id')
                    )
                except Exception as row_error:
                    if self.logger:
                        self.logger.warning(f"Error adding campaign row: {str(row_error)}")

            # Update status text
            upcoming = sum(1 for c in campaigns if c.get('status') == 'SCHEDULED')
            sending = sum(1 for c in campaigns if c.get('status') == 'PROCESSING')
            status_text = self.app.query_one("#scheduled-campaigns-status", Static)
            status_text.update(f"Found {len(campaigns)} campaigns ({upcoming} upcoming, {sending} sending)")

            # Clear selection display
            details_text = self.app.query_one("#scheduled-campaign-details", Static)
            details_text.update("👆 Select a campaign from the table above to view details and actions")

            self.selected_campaign = None
            self._update_action_buttons(None)

            self.app.notify(f"Loaded {len(campaigns)} campaigns", severity="success")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error loading scheduled campaigns: {str(e)}")
            self.app.notify(f"Error loading scheduled campaigns: {str(e)}", severity="error")

    def _update_action_buttons(self, status: Optional[str]) -> None:
        """Enable actions that make sense for the selected campaign's status"""
        try:
            cancel_btn = self.app.query_one("#cancel-scheduled-campaign-btn", Button)
            cancel_btn.disabled = status not in CANCELLABLE_STATUSES
            cancel_btn.label = "⏹️  Stop Sending" if status == 'PROCESSING' else "🚫 Cancel Campaign"

            # Deleting a campaign mid-send would lose its progress tracking - stop it first
            delete_btn = self.app.query_one("#delete-scheduled-campaign-btn", Button)
            delete_btn.disabled = status is None or status == 'PROCESSING'
        except Exception:
            pass  # nosec B110 - Intentional - widget may not be mounted

    async def handle_campaign_row_selected(self, event) -> None:
        """Handle scheduled campaign table row selection"""
        try:
            campaign_id = event.row_key.value
            campaign = self.campaigns_by_id.get(campaign_id)

            if not campaign:
                self.app.notify("Campaign not found, try refreshing", severity="warning")
                return

            self.selected_campaign = campaign
            status = campaign.get('status', 'UNKNOWN')

            # Display campaign details
            details_text = self.app.query_one("#scheduled-campaign-details", Static)

            display = f"📋 Selected Campaign Details\n"
            display += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            display += f"🏷️  Campaign Name: {campaign.get('campaign_name', 'N/A')}\n"
            display += f"🆔 Campaign ID: {campaign_id}\n"
            display += f"📅 Scheduled Time: {format_timestamp(campaign.get('schedule_timestamp', 0))}\n"
            display += f"📧 Template: {campaign.get('template_name', 'N/A')}\n"
            display += f"📨 From: {campaign.get('from_email', 'N/A')}\n"
            display += f"📊 Status: {STATUS_LABELS.get(status, status)}\n"
            display += f"👥 Recipients: {campaign.get('total_recipients', 0)}\n"

            if status != 'SCHEDULED':
                display += f"✅ Sent: {campaign.get('sent_count', 0)}\n"
                display += f"❌ Failed: {campaign.get('failed_count', 0)}\n"
                display += f"▶️  Started: {format_timestamp(campaign.get('started_at', 0))}\n"

            if campaign.get('completed_at'):
                display += f"🏁 Completed: {format_timestamp(campaign['completed_at'])}\n"
            if campaign.get('stopped_at'):
                stopped_label = "🚫 Cancelled" if status == 'CANCELLED' else "⏹️  Stopped"
                display += f"{stopped_label}: {format_timestamp(campaign['stopped_at'])}\n"
            if campaign.get('error_message'):
                display += f"⚠️  Error: {campaign['error_message']}\n"

            display += "\n"

            if campaign.get('configuration_set'):
                display += f"⚙️  Configuration Set: {campaign.get('configuration_set')}\n"

            if status == 'SCHEDULED' and campaign.get('csv_s3_path'):
                display += f"📂 CSV Path: {campaign['csv_s3_path']}\n"

            if campaign.get('created_at'):
                display += f"🕐 Created: {format_timestamp(campaign['created_at'])}\n"

            display += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            if status == 'SCHEDULED':
                display += "🚫 Cancel keeps the record as CANCELLED; Delete removes it entirely."
            elif status == 'PROCESSING':
                display += "⏹️  Stop Sending discards emails still waiting in the queue."
            else:
                display += "🗑️  Delete removes this campaign's record and progress history."

            details_text.update(display)
            self._update_action_buttons(status)

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error displaying campaign details: {str(e)}")
            self.app.notify(f"Error loading campaign details: {str(e)}", severity="error")

    async def handle_cancel_campaign(self) -> None:
        """Handle cancel/stop campaign button"""
        try:
            if not self.selected_campaign:
                self.app.notify("Please select a campaign first", severity="warning")
                return

            campaign_id = self.selected_campaign.get('campaign_id')
            campaign_name = self.selected_campaign.get('campaign_name')
            status = self.selected_campaign.get('status')

            if status == 'PROCESSING':
                title = "⏹️  Stop Sending Campaign?"
                text = (
                    f"Campaign: {campaign_name}\n\n"
                    f"Emails still waiting in the queue will not be sent.\n"
                    f"Emails already accepted by SES will still be delivered.\n\n"
                    f"This action cannot be undone!"
                )
                confirm_label = "⏹️  Stop"
            else:
                title = "🚫 Cancel Scheduled Campaign?"
                text = (
                    f"Campaign: {campaign_name}\n\n"
                    f"The campaign will not be sent. Its record is kept\n"
                    f"with status CANCELLED.\n\n"
                    f"This action cannot be undone!"
                )
                confirm_label = "🚫 Cancel Campaign"

            confirmed = await self.app.push_screen_wait(ConfirmActionScreen(title, text, confirm_label))
            if not confirmed:
                return

            result = self.manager.cancel_campaign(campaign_id)

            if result:
                self.app.notify(
                    f"✅ {campaign_name}: {result.get('message', result.get('status'))}",
                    severity="success",
                    timeout=10
                )
            else:
                self.app.notify(
                    f"❌ Could not cancel '{campaign_name}': {self.manager.last_error or 'Unknown error'}",
                    severity="error",
                    timeout=10
                )

            await self.load_scheduled_campaigns()

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error cancelling campaign: {str(e)}")
            self.app.notify(f"Error cancelling campaign: {str(e)}", severity="error")

    async def handle_delete_campaign(self) -> None:
        """Handle delete campaign button"""
        try:
            if not self.selected_campaign:
                self.app.notify("Please select a campaign to delete", severity="warning")
                return

            campaign_id = self.selected_campaign.get('campaign_id')
            campaign_name = self.selected_campaign.get('campaign_name')
            schedule_timestamp = self.selected_campaign.get('schedule_timestamp')
            status = self.selected_campaign.get('status')

            if not campaign_id or not schedule_timestamp:
                self.app.notify("Invalid campaign data", severity="error")
                return

            if status == 'PROCESSING':
                self.app.notify("This campaign is sending - stop it before deleting it", severity="warning")
                return

            if status == 'SCHEDULED':
                text = (
                    f"Campaign: {campaign_name}\n\n"
                    f"This will delete the campaign from DynamoDB.\n"
                    f"Cleanup Lambda will automatically remove:\n"
                    f"• EventBridge scheduled rule\n"
                    f"• CSV file from S3\n"
                    f"• Analytics metadata entry (if exists)\n\n"
                    f"⚠️  Cleanup happens in seconds via DynamoDB Stream.\n"
                    f"This action cannot be undone!"
                )
            else:
                text = (
                    f"Campaign: {campaign_name}\n\n"
                    f"This will delete the campaign record and its\n"
                    f"sent/failed history, plus the analytics metadata\n"
                    f"entry (if exists). SES analytics events are kept.\n\n"
                    f"This action cannot be undone!"
                )

            confirmed = await self.app.push_screen_wait(
                ConfirmActionScreen("⚠️  Delete Campaign?", text, "❌ Delete")
            )

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
                    f"✅ Campaign '{campaign_name}' deleted.",
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
                if getattr(self.manager, 'stack_status', None) == 'UPDATE_ROLLBACK_COMPLETE':
                    # Still works, but on the previous version - the last cdk deploy failed
                    status_text.update(
                        "⚠️  Scheduled Campaigns Stack Deployed, but its last update failed and was rolled back "
                        "(UPDATE_ROLLBACK_COMPLETE). Check the stack's events in the CloudFormation console and redeploy."
                    )

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
                stack_status = getattr(self.manager, 'stack_status', None) if self.manager else None
                if stack_status:
                    # Stack exists but isn't usable (e.g. a failed rollback) - say so instead of "not deployed"
                    status_text.update(f"❌ Scheduled Campaigns Stack Not Usable (status: {stack_status})")
                    info_text.update(
                        "SesScheduledCampaignsStack exists in this region but is not in a usable state.\n"
                        "Check the stack's events in the CloudFormation console, fix the error and redeploy."
                    )
                else:
                    status_text.update("❌ Scheduled Campaigns Stack Not Deployed")
                    info_text.update(
                        "The scheduled campaigns stack is required to view and manage scheduled campaigns.\n"
                        "Deploy the ses_scheduled_campaigns CDK stack in this region to use this feature."
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
                    Button("🚫 Cancel Campaign", id="cancel-scheduled-campaign-btn", variant="warning", disabled=True),
                    Button("❌ Delete Selected", id="delete-scheduled-campaign-btn", variant="error", disabled=True),
                    classes="form-row"
                ),
                Static(
                    "💡 Select a campaign from the table below to view details and enable actions",
                    classes="form-help-text"
                ),

                # Campaigns Table
                Label("📋 Campaigns (Click row for details)", classes="form-subsection-title"),
                DataTable(id="scheduled-campaigns-table", cursor_type="row"),
                Static("", id="scheduled-campaigns-status", classes="form-help-text"),
                Static(
                    "ℹ️  Shows campaigns of every status, newest first. Records are kept for 90 days after the scheduled time.",
                    classes="form-help-text"
                ),

                # Selected Campaign Details
                Label("📄 Campaign Details", classes="form-subsection-title"),
                Static(
                    "👆 Select a campaign from the table above to view details and actions",
                    id="scheduled-campaign-details",
                    classes="info-text"
                ),

                id="scheduled-campaigns-main-content"
            ),

            id="scheduled-campaigns-scroll-container"
        )
    ]
