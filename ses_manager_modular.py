#!/usr/bin/env python3
"""
Amazon SES Email Template Manager - Modular Version
A comprehensive tool for managing Amazon SES email templates and sending emails.
"""

import asyncio
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, TabbedContent, TabPane, Static, Switch, Input, DataTable, Select
from textual.binding import Binding
from textual import on

# Import our modular components
from config.settings import Settings
from aws.ses_client import SESClient, get_aws_profiles
from ui.screens import ProfileSelectionScreen, TemplateFormScreen, CSVValidationReportScreen
from ui.file_browser_screen import FileBrowserScreen
from modules.templates import TemplatesManager, create_templates_tab_content
from modules.email_sender import EmailSender
from modules.settings_manager import SettingsManager, SettingsTabHandler, create_settings_tab_content
from modules.email_composer import EmailComposer
from modules.dashboard import DashboardManager, create_dashboard_tab_content
from modules.campaign_analytics import CampaignAnalyticsManager, create_campaign_analytics_tab_content
from modules.scheduled_campaigns import ScheduledCampaignsManager
from modules.scheduled_campaigns_ui import ScheduledCampaignsUI, create_scheduled_campaigns_tab_content
from modules.logger import init_logger, get_logger
from modules.notification_helper import notify_verbose, notify_always


class SESManagerApp(App):
    """Main SES Manager Application with modular architecture."""
    
    CSS_PATH = "styles.css"
    TITLE = "Amazon SES Campaign Manager"
    
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+p", "change_profile", "Change Profile"),
    ]
    
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        # Initialize centralized logger
        self.logger = init_logger(self.settings)
        self.ses_client = None
        self.templates_manager = None
        self.email_sender = None
        self.settings_manager = None
        self.settings_handler = None
        self.dashboard_manager = None
        self.campaign_analytics_manager = None
        self.scheduled_campaigns_ui = None
        self.current_profile = None
        self.current_region = None
        
        # Campaign analytics state
        self.show_hidden_campaigns = False
        self.selected_campaign_id = None
        self.selected_campaign_name = None
        
        # Button handler registry for cleaner event handling
        self._init_button_handlers()
    
    def _init_button_handlers(self):
        """Initialize button handler registry for cleaner event handling."""
        self.button_handlers = {
            # Email composer buttons
            "send-email-btn": self.handle_send_email,
            "send-email-enhanced-btn": lambda: self.email_composer.handle_send_email() if hasattr(self, 'email_composer') else None,
            "send-email-enhanced-btn-bulk": lambda: self.email_composer.handle_send_email() if hasattr(self, 'email_composer') else None,
            "clear-form-enhanced-btn": lambda: self.email_composer.clear_form() if hasattr(self, 'email_composer') else None,
            "refresh-data-enhanced-btn": self.handle_refresh_send_data,
            "browse-csv-btn": self.handle_browse_csv,
            "view-csv-report-btn": self.handle_view_csv_report,
            "pause-bulk-btn": lambda: self.email_composer.handle_pause_bulk() if hasattr(self, 'email_composer') else None,
            "cancel-bulk-btn": lambda: self.email_composer.handle_cancel_bulk() if hasattr(self, 'email_composer') else None,
            
            # Template buttons
            "create-template": self._handle_create_template,
            "edit-template": self._handle_edit_template,
            "preview-template": self._handle_preview_template,
            "delete-template": self._handle_delete_template,
            "refresh-templates": lambda: self.templates_manager.refresh_templates(force_refresh=True) if self.templates_manager else None,
            
            # Settings tab buttons
            "change-aws-profile": self.action_change_profile,
            "generate-unsub-key-btn": lambda: self.settings_handler.handle_generate_unsub_key() if self.settings_handler else None,
            "save-unsub-settings-btn": lambda: self.settings_handler.handle_save_unsub_settings() if self.settings_handler else None,
            "save-email-settings-btn": lambda: self.settings_handler.handle_save_email_settings() if self.settings_handler else None,
            "refresh-config-sets-btn": self.handle_refresh_config_sets,
            "export-settings": lambda: self.settings_handler.handle_export_settings() if self.settings_handler else None,
            "reset-settings": lambda: self.settings_handler.handle_reset_settings() if self.settings_handler else None,
            
            # Cache management buttons
            "view-cache-stats": lambda: self.settings_handler.handle_view_cache_stats() if self.settings_handler else None,
            "clear-all-cache": lambda: self.settings_handler.handle_clear_all_cache() if self.settings_handler else None,
            
            # Debug log management buttons
            "save-log-settings-btn": lambda: self.settings_handler.handle_save_log_settings() if self.settings_handler else None,
            "clear-debug-log": lambda: self.settings_handler.handle_clear_debug_log() if self.settings_handler else None,
            "view-log-file": lambda: self.settings_handler.handle_view_log_file() if self.settings_handler else None,
            "refresh-log-info": lambda: self.settings_handler.handle_refresh_log_info() if self.settings_handler else None,
            
            # Dashboard tab buttons
            "refresh-dashboard": self.handle_refresh_dashboard,
            "goto-templates": self.handle_goto_templates,
            "goto-send-email": self.handle_goto_send_email,
            
            # Campaign Analytics tab buttons
            "refresh-stack-detection-btn": self.handle_refresh_stack_detection,
            "filter-dates-btn": self.handle_filter_campaigns_by_date,
            "check-and-refresh-btn": self.handle_check_and_refresh,
            "invoke-lambda-btn": self.handle_invoke_refresh_lambda,
            "show-all-campaigns-btn": self.handle_show_all_campaigns,
            "hide-campaign-btn": self.handle_hide_campaign,
            "unhide-campaign-btn": self.handle_unhide_campaign,
            
            # Scheduled Campaigns tab buttons
            "refresh-scheduled-campaigns-btn": lambda: self.scheduled_campaigns_ui.handle_refresh_campaigns() if self.scheduled_campaigns_ui else None,
            "delete-scheduled-campaign-btn": lambda: self.scheduled_campaigns_ui.handle_delete_campaign() if self.scheduled_campaigns_ui else None,
        }
    
    async def _update_tab_content_base(self, tab_id: str, loading_content_id: str, 
                                       existing_check_id: str, create_func, 
                                       update_func=None, error_id=None):
        """
        Base method for updating tab content with common pattern.
        
        Args:
            tab_id: ID of the tab pane (e.g., "dashboard")
            loading_content_id: ID of the loading message widget
            existing_check_id: ID to check if content already exists
            create_func: Function that returns list of widgets to mount
            update_func: Optional async function to call after mounting
            error_id: Optional ID for error display widget
        """
        try:
            # Step 1: Remove loading message if it exists
            try:
                loading_widget = self.query_one(f"#{loading_content_id}", Static)
                loading_widget.remove()
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
            # Step 2: Check if content already exists
            try:
                self.query_one(f"#{existing_check_id}")
                # Content exists, just update if update_func provided
                if update_func:
                    await update_func()
                return
            except:
                pass  # Content doesn't exist, create it  # nosec B110 - Intentional - widget may not be mounted
            
            # Step 3: Create new content
            new_content_widgets = create_func()
            
            # Step 4: Mount new content
            tab_pane = self.query_one(f"#{tab_id}", TabPane)
            for widget in new_content_widgets:
                await tab_pane.mount(widget)
            
            # Step 5: Wait for mounting to complete
            await asyncio.sleep(0.1)
            
            # Step 6: Update display if update_func provided
            if update_func:
                try:
                    await update_func()
                except Exception as e:
                    self.notify(f"Warning: Could not update display: {str(e)}", severity="warning")
            
            # Step 7: Force refresh
            self.refresh()
            
        except Exception as e:
            self.notify(f"Error updating {tab_id} content: {str(e)}", severity="error")
            # Create error display if error_id provided
            if error_id:
                try:
                    tab_pane = self.query_one(f"#{tab_id}", TabPane)
                    await tab_pane.mount(Static(f"Error loading {tab_id}: {str(e)}", id=error_id))
                except:
                    pass  # nosec B110 - Intentional - widget may not be mounted
        
    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        
        with Container(classes="main-container"):
            # Status bar
            yield Static("Initializing Amazon SES Manager...", id="status-bar", classes="status-bar")
            
            # Main content with tabs
            with Container(classes="content-container"):
                with TabbedContent(initial="dashboard"):
                    with TabPane("Dashboard", id="dashboard"):
                        yield Static("Loading dashboard...", id="dashboard-content")
                    
                    with TabPane("Templates", id="templates"):
                        yield Static("Loading templates...", id="templates-content")
                    
                    with TabPane("Send Email", id="send-email"):
                        yield Static("Loading send email form...", id="send-email-content")
                    
                    with TabPane("Campaign Analytics", id="campaign-analytics"):
                        yield Static("Loading campaign analytics...", id="campaign-analytics-content")
                    
                    with TabPane("Scheduled Campaigns", id="scheduled-campaigns"):
                        yield Static("Loading scheduled campaigns...", id="scheduled-campaigns-content")
                    
                    with TabPane("Settings", id="settings"):
                        yield Static("Loading settings...", id="settings-content")
        
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the application."""
        self.run_worker(self.initialize_app())
        # Initialize email log after mount
        self.call_after_refresh(self.initialize_email_log)
    
    async def initialize_app(self) -> None:
        """Initialize the application with AWS profile selection."""
        try:
            # Check if we have saved AWS configuration
            aws_config = self.settings.get_aws_config()
            profiles = get_aws_profiles()
            
            if aws_config and aws_config.get('profile') in profiles:
                # Use saved configuration
                profile_name = aws_config['profile']
                region_name = aws_config['region']
                notify_verbose(self, f"Using saved AWS config: {profile_name} ({region_name})", severity="information")
            else:
                # Show profile selection screen
                notify_verbose(self, f"Found AWS profiles: {profiles}", severity="information")
                config = await self.push_screen_wait(ProfileSelectionScreen(profiles))
                
                if config is None:
                    self.notify("No configuration selected. Exiting.", severity="warning")
                    self.exit()
                    return
                
                profile_name = config["profile"]
                region_name = config["region"]
                
                # Save the configuration
                self.settings.set_aws_config(profile_name, region_name)
                notify_verbose(self, f"Using AWS profile: {profile_name}, region: {region_name}", severity="information")
            
            # Update status bar
            self.update_status_bar(f"Connecting to Amazon SES ({profile_name} - {region_name})...")
            
            # Initialize components
            try:
                self.ses_client = SESClient(profile_name, region_name, self.settings)
                self.templates_manager = TemplatesManager(self, self.ses_client)
                self.email_sender = EmailSender(self.ses_client, self.settings)
                self.settings_manager = SettingsManager(self)
                self.settings_handler = SettingsTabHandler(self, self.settings_manager)
                self.dashboard_manager = DashboardManager(self, self.ses_client)
                self.campaign_analytics_manager = CampaignAnalyticsManager(self, self.ses_client, self.settings)
                
                # Initialize scheduled campaigns manager and UI
                self.scheduled_campaigns_manager = ScheduledCampaignsManager(
                    region=region_name,
                    settings_instance=self.settings
                )
                self.scheduled_campaigns_ui = ScheduledCampaignsUI(self, self.scheduled_campaigns_manager)
                
                # Initialize analytics manager to detect DynamoDB table for metadata storage
                if self.current_profile and self.current_region:
                    asyncio.create_task(self.campaign_analytics_manager.detect_cdk_stack(
                        self.current_profile,
                        self.current_region
                    ))
                
                self.current_profile = profile_name
                self.current_region = region_name
                
                # Load initial data
                await self.refresh_all_data()
                
                self.update_status_bar(f"Connected to Amazon SES ({profile_name} - {region_name})")
                self.notify("Connected to Amazon SES successfully!", severity="success")
                
            except Exception as e:
                self.update_status_bar(f"Connection failed: {str(e)}")
                self.notify(f"Failed to initialize Amazon SES: {str(e)}", severity="error")
                
                # Still initialize basic components and UI even if AWS connection fails
                try:
                    self.settings_manager = SettingsManager(self)
                    self.settings_handler = SettingsTabHandler(self, self.settings_manager)
                    self.dashboard_manager = DashboardManager(self, None)  # Initialize with None SES client
                    self.campaign_analytics_manager = CampaignAnalyticsManager(self, None, self.settings)
                    
                    # Initialize scheduled campaigns even if AWS connection failed
                    self.scheduled_campaigns_manager = ScheduledCampaignsManager(
                        region=region_name,
                        settings_instance=self.settings
                    )
                    self.scheduled_campaigns_ui = ScheduledCampaignsUI(self, self.scheduled_campaigns_manager)
                    
                    self.current_profile = profile_name
                    self.current_region = region_name
                    
                    # Update UI with error states
                    await self.refresh_all_data()
                except Exception as ui_error:
                    self.notify(f"Error initializing UI: {str(ui_error)}", severity="error")
                
        except Exception as e:
            self.notify(f"Unexpected error during initialization: {str(e)}", severity="error")
    
    def update_status_bar(self, message: str) -> None:
        """Update the status bar with a message."""
        try:
            status_bar = self.query_one("#status-bar", Static)
            status_bar.update(message)
        except:
            pass  # Status bar might not be ready yet  # nosec B110 - Intentional - widget may not be mounted
    
    def initialize_email_log(self) -> None:
        """Initialize the email log with welcome message."""
        try:
            from datetime import datetime
            # Try to find the email log widget
            email_log = self.query_one("#email-log", Log)
            timestamp = datetime.now().strftime('%H:%M:%S')
            email_log.write_line(f"[{timestamp}] 📧 Amazon SES Campaign Manager - Send Email Log")
            email_log.write_line(f"[{timestamp}] Ready to send emails via Amazon SES")
            email_log.write_line(f"[{timestamp}] Click 'Send Email' button to compose and send emails")
            email_log.write_line("=" * 60)
        except Exception as e:
            # Email log widget might not exist yet, that's okay
            pass  # nosec B110 - Intentional - widget may not be mounted
    
    
    async def refresh_all_data(self) -> None:
        """Refresh all data and update the interface."""
        try:
            # Refresh templates if available
            if self.templates_manager:
                await self.templates_manager.refresh_templates()
            
            # Refresh dashboard data if available
            if self.dashboard_manager:
                await self.dashboard_manager.refresh_dashboard_data()
            
            # Update all tab content regardless of manager availability
            await self.update_dashboard_content()
            await self.update_templates_content()
            await self.update_send_email_content()
            await self.update_campaign_analytics_content()
            await self.update_scheduled_campaigns_content()
            await self.update_settings_content()
            
        except Exception as e:
            self.notify(f"Error refreshing data: {str(e)}", severity="error")
    
    async def update_dashboard_content(self) -> None:
        """Update the dashboard tab content."""
        if not self.dashboard_manager:
            self.dashboard_manager = DashboardManager(self, self.ses_client)
        
        await self._update_tab_content_base(
            tab_id="dashboard",
            loading_content_id="dashboard-content",
            existing_check_id="dashboard-scroll-container",
            create_func=create_dashboard_tab_content,
            update_func=lambda: self.dashboard_manager.update_dashboard_display() if self.dashboard_manager else None,
            error_id="dashboard-error"
        )
    
    async def update_templates_content(self) -> None:
        """Update the templates tab content."""
        await self._update_tab_content_base(
            tab_id="templates",
            loading_content_id="templates-content",
            existing_check_id="templates-table",
            create_func=create_templates_tab_content,
            update_func=self._update_templates_display,
            error_id="templates-error"
        )
    
    async def _update_templates_display(self):
        """Update templates table display."""
        if self.templates_manager and self.templates_manager.templates:
            await self.templates_manager.update_templates_table()
    
    async def update_send_email_content(self) -> None:
        """Update the send email tab content."""
        try:
            # Remove loading message if it exists (do this FIRST)
            try:
                send_email_content = self.query_one("#send-email-content", Static)
                send_email_content.remove()
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
            # Check if send email content already exists
            try:
                existing_content = self.query_one("#send-email-enhanced-container")
                # If content exists, don't recreate it
                return
            except:
                pass  # Content doesn't exist, create it  # nosec B110 - Intentional - widget may not be mounted
            
            # Get data with fallbacks for connection issues
            identities = ["No identities available"]
            templates = []
            configuration_sets = []
            
            # Try to get fresh data if components are available
            if self.ses_client:
                try:
                    identities = self.ses_client.get_identities()
                    if not identities:
                        identities = ["No identities available"]
                except Exception as e:
                    identities = [f"Error loading identities: {str(e)[:50]}..."]
                
                try:
                    configuration_sets = self.ses_client.get_configuration_sets()
                except Exception as e:
                    configuration_sets = []
            
            if self.templates_manager and self.templates_manager.templates:
                templates = self.templates_manager.templates
                if not templates:
                    templates = []
            
            # Create enhanced send email form handler - allow creation even if AWS connection failed
            try:
                # Log attempt
                if self.settings_manager and self.settings_manager.get_debug_logging_enabled():
                    from modules.logger import get_logger
                    logger = get_logger()
                    if logger:
                        logger.info(f"Creating send email form with {len(templates)} templates, {len(identities)} identities")
                
                self.email_composer = EmailComposer(
                    self, 
                    self.ses_client, 
                    self.email_sender, 
                    self.templates_manager
                )
                
                send_email_content = self.email_composer.create_form_content(
                    templates, identities, configuration_sets
                )
                
                # Mount new content - unpack the list using *
                send_email_tab = self.query_one("#send-email", TabPane)
                await send_email_tab.mount(*send_email_content)
                
                # Force a refresh to ensure widgets are rendered
                self.refresh()
                
                # Initialize mode visibility after mounting and refresh
                await asyncio.sleep(0.3)
                try:
                    self.email_composer.update_mode_visibility('single')
                except Exception as vis_error:
                    # Log visibility error but don't fail
                    if self.settings_manager and self.settings_manager.get_debug_logging_enabled():
                        from modules.logger import get_logger
                        logger = get_logger()
                        if logger:
                            logger.warning(f"Could not set initial mode visibility: {vis_error}")
                
                # Log success
                if self.settings_manager and self.settings_manager.get_debug_logging_enabled():
                    from modules.logger import get_logger
                    logger = get_logger()
                    if logger:
                        logger.info("Send email form created successfully")
                
            except Exception as form_error:
                # Log the full error
                if self.settings_manager and self.settings_manager.get_debug_logging_enabled():
                    from modules.logger import get_logger
                    import traceback
                    logger = get_logger()
                    if logger:
                        logger.error(f"Error creating send email form: {form_error}")
                        logger.error(f"Traceback: {traceback.format_exc()}")
                
                # Fallback if form creation fails
                send_email_tab = self.query_one("#send-email", TabPane)
                error_msg = f"Error loading send email form:\n\n{str(form_error)}\n\nStack trace logged to debug log.\n\nPlease check:\n- AWS connection\n- Templates available\n- Identities configured"
                await send_email_tab.mount(Static(error_msg, id="send-email-error"))
                self.notify(f"Error creating send email form: {str(form_error)}", severity="error")
            
        except Exception as e:
            self.notify(f"Error updating send email content: {str(e)}", severity="error")
            # Create a basic error display if all else fails
            try:
                send_email_tab = self.query_one("#send-email", TabPane)
                await send_email_tab.mount(Static(f"Error loading send email form: {str(e)}", id="send-email-error"))
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
    
    async def update_campaign_analytics_content(self) -> None:
        """Update the campaign analytics tab content."""
        if not self.campaign_analytics_manager:
            self.campaign_analytics_manager = CampaignAnalyticsManager(self, self.ses_client, self.settings)
        
        await self._update_tab_content_base(
            tab_id="campaign-analytics",
            loading_content_id="campaign-analytics-content",
            existing_check_id="campaign-analytics-scroll-container",
            create_func=create_campaign_analytics_tab_content,
            update_func=self._update_campaign_analytics_after_mount,
            error_id="analytics-error"
        )
    
    async def _update_campaign_analytics_after_mount(self):
        """Update campaign analytics after mounting."""
        try:
            if self.campaign_analytics_manager and self.current_profile and self.current_region:
                await self.campaign_analytics_manager.detect_cdk_stack(
                    self.current_profile,
                    self.current_region
                )
                await self.update_campaign_analytics_display()
        except Exception as e:
            self.notify(f"Warning: Could not detect CDK stack: {str(e)}", severity="warning")
    
    async def update_scheduled_campaigns_content(self) -> None:
        """Update the scheduled campaigns tab content."""
        if not self.scheduled_campaigns_manager:
            self.scheduled_campaigns_manager = ScheduledCampaignsManager(
                region=self.current_region,
                settings_instance=self.settings
            )
        if not self.scheduled_campaigns_ui:
            self.scheduled_campaigns_ui = ScheduledCampaignsUI(self, self.scheduled_campaigns_manager)
        
        await self._update_tab_content_base(
            tab_id="scheduled-campaigns",
            loading_content_id="scheduled-campaigns-content",
            existing_check_id="scheduled-campaigns-scroll-container",
            create_func=create_scheduled_campaigns_tab_content,
            update_func=lambda: self.scheduled_campaigns_ui.update_stack_status() if self.scheduled_campaigns_ui else None,
            error_id="scheduled-error"
        )
    
    async def update_settings_content(self) -> None:
        """Update the settings tab content."""
        await self._update_tab_content_base(
            tab_id="settings",
            loading_content_id="settings-content",
            existing_check_id="settings-scroll-container",
            create_func=create_settings_tab_content,
            update_func=self._update_settings_display,
            error_id="settings-error"
        )
    
    async def _update_settings_display(self):
        """Update settings display."""
        if not self.settings_manager:
            self.settings_manager = SettingsManager(self)
        if not self.settings_handler:
            self.settings_handler = SettingsTabHandler(self, self.settings_manager)
        
        try:
            self.settings_handler.update_settings_display()
        except Exception as e:
            self.notify(f"Warning: Could not update settings display: {str(e)}", severity="warning")
    
    def on_tabbed_content_tab_activated(self, event) -> None:
        """Handle tab activation events."""
        # Tab content is now initialized during startup, so we don't need to do anything here
        # Just log for debugging if needed
        self.logger.ui_operation(f"Tab activated: {event.tab.id}")
    
    def on_button_pressed(self, event) -> None:
        """Handle button press events using registry pattern."""
        button_id = event.button.id
        
        # Look up handler in registry
        handler = self.button_handlers.get(button_id)
        
        if handler:
            # Execute handler (wrap in run_worker if it's a coroutine)
            result = handler()
            if result is not None and hasattr(result, '__await__'):
                self.run_worker(result)
        else:
            # Log unknown button for debugging
            if self.logger:
                self.logger.debug(f"No handler registered for button: {button_id}")
    
    def _handle_create_template(self):
        """Handle create template action."""
        if self.templates_manager:
            return self.templates_manager.create_template_worker()
    
    def _handle_edit_template(self):
        """Handle edit template action."""
        if not self.templates_manager:
            return
        selected_template = self.templates_manager.get_selected_template()
        if selected_template:
            return self.templates_manager.edit_template_worker(selected_template)
        else:
            self.notify("Please select a template to edit", severity="warning")
    
    def _handle_preview_template(self):
        """Handle preview template action."""
        if not self.templates_manager:
            return
        selected_template = self.templates_manager.get_selected_template()
        if selected_template:
            return self.templates_manager.preview_template_worker(selected_template)
        else:
            self.notify("Please select a template to preview", severity="warning")
    
    def _handle_delete_template(self):
        """Handle delete template action."""
        if not self.templates_manager:
            return
        selected_template = self.templates_manager.get_selected_template()
        if selected_template:
            template_name = selected_template.get('TemplateName')
            return self.templates_manager.delete_template(template_name)
        else:
            self.notify("Please select a template to delete", severity="warning")
    
    @on(Switch.Changed, "#debug-logging-switch")
    def on_debug_logging_changed(self, event: Switch.Changed) -> None:
        """Handle debug logging toggle."""
        if self.settings_handler:
            self.settings_handler.handle_debug_logging_toggle(event.value)
    
    @on(Switch.Changed, "#verbose-notifications-switch")
    def on_verbose_notifications_changed(self, event: Switch.Changed) -> None:
        """Handle verbose notifications toggle."""
        if self.settings_handler:
            self.settings_handler.handle_verbose_notifications_toggle(event.value)
    
    @on(Switch.Changed, "#show-hidden-switch")
    def on_show_hidden_changed(self, event: Switch.Changed) -> None:
        """Handle show hidden campaigns switch toggle."""
        self.run_worker(self.handle_toggle_show_hidden_checkbox(event.value))

    
    def on_data_table_row_selected(self, event) -> None:
        """Handle DataTable row selection events."""
        try:
            # Check if this is the campaign analytics table
            if event.data_table.id == "campaign-analytics-table":
                self.run_worker(self.handle_campaign_row_selected(event))
            # Check if this is the scheduled campaigns table
            elif event.data_table.id == "scheduled-campaigns-table":
                if self.scheduled_campaigns_ui:
                    self.run_worker(self.scheduled_campaigns_ui.handle_campaign_row_selected(event))
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling row selection: {str(e)}")
    
    async def handle_campaign_row_selected(self, event) -> None:
        """Handle campaign analytics table row selection."""
        try:
            if not self.campaign_analytics_manager:
                return
            
            # Get the row key which is now the campaign_id
            campaign_id = event.row_key.value if hasattr(event.row_key, 'value') else str(event.row_key)
            table = event.data_table
            
            # Get campaign_name from the selected row (first column)
            row_data = table.get_row(event.row_key)
            campaign_name = str(row_data[0])
            
            # Store selected campaign info
            self.selected_campaign_id = campaign_id
            self.selected_campaign_name = campaign_name
            
            if not campaign_id or campaign_id == 'None':
                # Metadata display without ID
                metadata_display = self.query_one("#campaign-metadata-display", Static)
                metadata_display.update(
                    f"📋 Campaign: {campaign_name}\n\n"
                    f"ℹ️  Campaign ID not found in current data.\n"
                    f"Metadata is only available for campaigns with tracked IDs."
                )
                # Hide action buttons when no campaign ID
                self.query_one("#campaign-action-row").display = False
                return
            
            # Show action buttons when campaign is selected
            self.query_one("#campaign-action-row").display = True
            
            # Fetch metadata from DynamoDB
            metadata = await self.campaign_analytics_manager.get_campaign_metadata(campaign_id)
            
            # Display metadata
            metadata_display = self.query_one("#campaign-metadata-display", Static)
            
            if metadata:
                # Format metadata nicely
                display_text = f"📋 Campaign: {campaign_name}\n"
                display_text += f"🆔 Campaign ID: {campaign_id}\n"
                
                # Show hidden status prominently
                is_hidden = metadata.get('is_hidden', False)
                if is_hidden:
                    display_text += f"🔒 Status: HIDDEN (soft deleted)\n"
                    if metadata.get('hidden_at'):
                        display_text += f"   Hidden at: {metadata['hidden_at']}\n"
                else:
                    display_text += f"✅ Status: Visible\n"
                
                display_text += "\n"
                
                # Add metadata fields (exclude metrics already shown in table)
                if metadata.get('template_name'):
                    display_text += f"📧 Template: {metadata['template_name']}\n"
                
                if metadata.get('from_address'):
                    display_text += f"📨 From: {metadata['from_address']}\n"
                
                if metadata.get('description'):
                    display_text += f"📝 Description: {metadata['description']}\n"
                
                if metadata.get('creator'):
                    display_text += f"👤 Creator: {metadata['creator']}\n"
                
                if metadata.get('configuration_set'):
                    display_text += f"⚙️  Config Set: {metadata['configuration_set']}\n"
                
                # Show schedule type (scheduled vs immediate)
                if metadata.get('schedule'):
                    schedule_val = metadata['schedule']
                    if schedule_val == 'immediate':
                        display_text += f"⏱️  Schedule: Immediate execution\n"
                    else:
                        display_text += f"📅 Scheduled: {schedule_val}\n"
                
                if metadata.get('created_at'):
                    display_text += f"🕐 Created: {metadata['created_at']}\n"
                
                # Add any additional metadata fields
                excluded_fields = {
                    'campaign_id', 'campaign_name', 'template_name', 'from_address',
                    'description', 'creator', 'configuration_set', 'created_at', 'updated_at',
                    'is_hidden', 'hidden_at'  # Exclude hidden status fields as we show them above
                }
                
                other_metadata = {k: v for k, v in metadata.items() if k not in excluded_fields}
                if other_metadata:
                    display_text += "\n📎 Additional Information:\n"
                    for key, value in other_metadata.items():
                        display_text += f"  • {key}: {value}\n"
                
                metadata_display.update(display_text)
            else:
                metadata_display.update(
                    f"📋 Campaign: {campaign_name}\n"
                    f"🆔 Campaign ID: {campaign_id}\n\n"
                    f"ℹ️  No metadata found in DynamoDB.\n"
                    f"Metadata is stored when campaigns are sent via the bulk email feature."
                )
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error displaying campaign metadata: {str(e)}")
            self.notify(f"Error loading campaign details: {str(e)}", severity="error")
    
    def on_select_changed(self, event) -> None:
        """Handle select changes for integrated send email form and dashboard."""
        if hasattr(event, 'select'):
            select_id = event.select.id
            
            # Email mode selector (dropdown)
            if select_id == "email-mode-selector" and hasattr(self, 'email_composer'):
                self.run_worker(self.email_composer.handle_mode_change(event.value))
            # Email composer selects
            elif select_id == "from-identity-enhanced" and hasattr(self, 'email_composer'):
                self.email_composer.update_custom_email_visibility()
            elif select_id == "email-template-enhanced" and hasattr(self, 'email_composer'):
                # Auto-populate template data with substitution keys
                self.email_composer.handle_template_selection(event.value)
            # Old form selects (for backward compatibility)
            elif select_id == "email-template" and hasattr(self, 'send_email_handler'):
                self.send_email_handler.handle_template_changed(event.value)
            elif select_id == "from-identity" and hasattr(self, 'send_email_handler'):
                self.send_email_handler.update_form_visibility()
            # Dashboard selects
            elif select_id == "metrics-time-period" and self.dashboard_manager:
                self.run_worker(self.dashboard_manager.handle_time_period_change(event.value))
            # Campaign Analytics select
            elif select_id == "campaign-select":
                self.run_worker(self.handle_campaign_selection_change(event.value))
    
    async def handle_send_email(self) -> None:
        """Handle send email action by switching to the Send Email tab."""
        try:
            # Switch to the Send Email tab
            tabbed_content = self.query_one(TabbedContent)
            tabbed_content.active = "send-email"
            notify_verbose(self, "Switched to Send Email tab", "information")
            
        except Exception as e:
            self.notify(f"Error switching to Send Email tab: {str(e)}", severity="error")
    
    async def handle_clear_log(self) -> None:
        """Handle clear log action."""
        if self.email_sender:
            email_log = self.query_one("#email-log", Log)
            self.email_sender.clear_log(email_log)
            self.notify("Email log cleared", severity="information")
    
    async def handle_refresh_send_data(self) -> None:
        """Handle refresh send data action - refreshes templates and identities for send email form."""
        try:
            # Refresh templates with force refresh to clear cache
            if self.templates_manager:
                await self.templates_manager.refresh_templates(force_refresh=True)
                self.logger.debug(f"Refreshed templates, count: {len(self.templates_manager.templates)}", "REFRESH")
            
            # Refresh identities (from addresses) with force refresh to clear cache
            if self.ses_client:
                self.identities = self.ses_client.get_identities(force_refresh=True)
                self.logger.debug(f"Refreshed identities, count: {len(self.identities)}", "REFRESH")
            
            # Update the template dropdown in the send email form
            try:
                template_select = self.query_one("#email-template-enhanced", Select)
                if self.templates_manager and self.templates_manager.templates:
                    templates = self.templates_manager.templates
                    self.logger.debug(f"Updating template dropdown with {len(templates)} templates", "REFRESH")
                    
                    # Build new options list
                    new_options = []
                    if templates:
                        for template in templates:
                            template_name = template.get('TemplateName', '')
                            if template_name:
                                new_options.append((template_name, template_name))
                                self.logger.debug(f"Added template: {template_name}", "REFRESH")
                    else:
                        new_options.append(("No templates", ""))
                        self.logger.debug("No templates found, added placeholder", "REFRESH")
                    
                    # Replace all options at once
                    template_select.set_options(new_options)
                else:
                    template_select.set_options([("No templates", "")])
                    self.logger.debug("Templates manager has no templates", "REFRESH")
            except Exception as e:
                self.logger.warning(f"Could not update template dropdown: {str(e)}", "REFRESH")
                import traceback
                self.logger.debug(f"Traceback: {traceback.format_exc()}", "REFRESH")
            
            # Update the from identity dropdown
            try:
                identity_select = self.query_one("#from-identity-enhanced", Select)
                if self.identities:
                    self.logger.debug(f"Updating identity dropdown with {len(self.identities)} identities", "REFRESH")
                    
                    # Build new options list
                    new_options = [(identity, identity) for identity in self.identities]
                    identity_select.set_options(new_options)
                else:
                    identity_select.set_options([("No identities", "")])
                    self.logger.debug("No identities found, added placeholder", "REFRESH")
            except Exception as e:
                self.logger.warning(f"Could not update identity dropdown: {str(e)}", "REFRESH")
                import traceback
                self.logger.debug(f"Traceback: {traceback.format_exc()}", "REFRESH")
            
            notify_verbose(self, "Send email data refreshed (templates and identities)", severity="information")
        except Exception as e:
            self.notify(f"Error refreshing send email data: {str(e)}", severity="error")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}", "REFRESH")
        notify_verbose(self, "Send data refreshed", severity="information")
    
    def action_refresh(self) -> None:
        """Refresh data action."""
        self.run_worker(self.refresh_all_data())
    
    def action_new_template(self) -> None:
        """Create new template action."""
        self.on_create_template()
    
    def action_send_email(self) -> None:
        """Send email action."""
        self.run_worker(self.handle_send_email())
    
    def action_change_profile(self) -> None:
        """Change AWS profile action."""
        self.run_worker(self.change_profile())
    
    async def change_profile(self) -> None:
        """Change AWS profile and region."""
        try:
            profiles = get_aws_profiles()
            config = await self.push_screen_wait(ProfileSelectionScreen(profiles))
            
            if config:
                profile_name = config["profile"]
                region_name = config["region"]
                
                # Save the new configuration
                self.settings.set_aws_config(profile_name, region_name)
                
                # Reinitialize components
                self.update_status_bar(f"Reconnecting to Amazon SES ({profile_name} - {region_name})...")
                
                self.ses_client = SESClient(profile_name, region_name, self.settings)
                self.templates_manager = TemplatesManager(self, self.ses_client)
                self.email_sender = EmailSender(self.ses_client, self.settings)
                self.dashboard_manager = DashboardManager(self, self.ses_client)
                
                self.current_profile = profile_name
                self.current_region = region_name
                
                # Refresh data
                await self.refresh_all_data()
                
                self.update_status_bar(f"Connected to Amazon SES ({profile_name} - {region_name})")
                self.notify(f"Switched to AWS profile: {profile_name} ({region_name})", severity="success")
                
        except Exception as e:
            self.notify(f"Error changing profile: {str(e)}", severity="error")
    
    async def handle_refresh_dashboard(self) -> None:
        """Handle refresh dashboard action."""
        if self.dashboard_manager:
            await self.dashboard_manager.refresh_account_details(force_refresh=True)
            await self.dashboard_manager.refresh_metrics_data(force_refresh=True)
            await self.dashboard_manager.update_dashboard_display()
            notify_verbose(self, "Dashboard data refreshed from AWS", severity="information")
        else:
            self.notify("Dashboard not available", severity="warning")
    
    async def handle_goto_templates(self) -> None:
        """Handle goto templates action."""
        try:
            # Switch to the Templates tab
            tabbed_content = self.query_one(TabbedContent)
            tabbed_content.active = "templates"
            notify_verbose(self, "Switched to Templates tab", "information")
        except Exception as e:
            self.notify(f"Error switching to Templates tab: {str(e)}", severity="error")
    
    async def handle_goto_send_email(self) -> None:
        """Handle goto send email action."""
        try:
            # Switch to the Send Email tab
            tabbed_content = self.query_one(TabbedContent)
            tabbed_content.active = "send-email"
            notify_verbose(self, "Switched to Send Email tab", "information")
        except Exception as e:
            self.notify(f"Error switching to Send Email tab: {str(e)}", severity="error")
    
    async def handle_browse_csv(self) -> None:
        """Handle CSV file browsing using DirectoryTree widget."""
        try:
            from pathlib import Path
            
            # Get current value from CSV input field or use current directory
            csv_input = self.query_one("#csv-file-path", Input)
            current_value = csv_input.value.strip()
            
            # Determine initial path for browser
            if current_value and Path(current_value).exists():
                if Path(current_value).is_file():
                    initial_path = str(Path(current_value).parent)
                else:
                    initial_path = current_value
            else:
                initial_path = str(Path.cwd())
            
            # Show file browser modal
            selected_file = await self.push_screen_wait(
                FileBrowserScreen(initial_path=initial_path, file_filter="*.csv")
            )
            
            # If a file was selected, validate it immediately
            if selected_file:
                self.notify("Validating CSV file...", severity="information")
                
                # Get currently selected template (if any) for validation
                template_name = None
                try:
                    template_select = self.query_one("#email-template-enhanced", Select)
                    if template_select.value and template_select.value != Select.BLANK:
                        template_name = template_select.value
                except:
                    pass  # nosec B110 - Template select may not be mounted yet
                
                # Validate the CSV file
                if hasattr(self, 'email_composer') and self.email_composer:
                    validation_result = self.email_composer.csv_validator.validate_csv_file(
                        csv_path=selected_file,
                        check_duplicates=True,
                        template_name=template_name
                    )
                    
                    # Store validation result for later viewing
                    self.last_csv_validation = {
                        'result': validation_result,
                        'filename': Path(selected_file).name,
                        'filepath': selected_file
                    }
                    
                    # Enable the View Report button
                    try:
                        # Try to find and enable the button
                        buttons = self.query("#view-csv-report-btn")
                        if buttons:
                            view_report_btn = buttons.first()
                            view_report_btn.disabled = False
                            self.logger.debug("View Report button enabled", "CSV_VALIDATION")
                        else:
                            self.logger.warning("View Report button not found in DOM", "CSV_VALIDATION")
                    except Exception as e:
                        self.logger.warning(f"Could not enable View Report button: {e}", "CSV_VALIDATION")
                    
                    if validation_result.is_valid:
                        # CSV is valid, update the input field
                        csv_input.value = selected_file
                        
                        # Show summary notification
                        summary = f"✅ CSV Valid: {validation_result.valid_row_count} recipients"
                        if validation_result.warnings:
                            summary += f" ({len(validation_result.warnings)} warnings)"
                        self.notify(summary, severity="success")
                        
                        # Show view report option if there are warnings
                        if validation_result.warnings:
                            self.notify("⚠️ Warnings found - Click 'View Report' to review", severity="warning")
                    else:
                        # CSV has errors - BLOCK sending by not loading the file
                        error_count = len(validation_result.errors)
                        warning_count = len(validation_result.warnings)
                        
                        summary = f"❌ CSV Invalid: {error_count} error(s)"
                        if warning_count > 0:
                            summary += f", {warning_count} warning(s)"
                        
                        self.notify(summary, severity="error")
                        self.notify("❌ Cannot send - Fix errors first. Click 'View Report' for details", severity="error")
                        
                        # Don't update the input field - this blocks sending
                else:
                    # Fallback if email_composer not available
                    csv_input.value = selected_file
                    self.notify(f"Selected: {Path(selected_file).name}", severity="success")
            
        except Exception as e:
            self.notify(f"Error browsing files: {str(e)}", severity="error")
            self.logger.error(f"Error in handle_browse_csv: {str(e)}", "CSV_VALIDATION")
    
    async def handle_view_csv_report(self) -> None:
        """Handle viewing the CSV validation report."""
        try:
            if not hasattr(self, 'last_csv_validation') or not self.last_csv_validation:
                self.notify("No validation report available", severity="warning")
                return
            
            validation_data = self.last_csv_validation
            
            # Show the validation report screen
            action = await self.push_screen_wait(
                CSVValidationReportScreen(
                    validation_data['result'],
                    validation_data['filename']
                )
            )
            
            # If user chose to save the report
            if action == "save":
                await self.save_validation_report(validation_data)
        
        except Exception as e:
            self.notify(f"Error viewing validation report: {str(e)}", severity="error")
    
    async def save_validation_report(self, validation_data) -> None:
        """Save the validation report to a file."""
        try:
            from pathlib import Path
            from datetime import datetime
            
            # Generate report filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = Path(validation_data['filename']).stem
            report_filename = f"validation_report_{base_name}_{timestamp}.txt"
            report_path = Path("bulk_email_csv") / "bulk_email_output" / report_filename
            
            # Ensure directory exists
            report_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Build report content
            result = validation_data['result']
            lines = []
            lines.append("=" * 70)
            lines.append("CSV VALIDATION REPORT")
            lines.append("=" * 70)
            lines.append(f"File: {validation_data['filename']}")
            lines.append(f"Validated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"Status: {'VALID' if result.is_valid else 'INVALID'}")
            lines.append(f"Total Rows: {result.row_count}")
            lines.append(f"Valid Rows: {result.valid_row_count}")
            lines.append(f"Errors: {len(result.errors)}")
            lines.append(f"Warnings: {len(result.warnings)}")
            lines.append("")
            
            if result.errors:
                lines.append("=" * 70)
                lines.append(f"ERRORS ({len(result.errors)})")
                lines.append("=" * 70)
                for i, error in enumerate(result.errors, 1):
                    lines.append(f"{i}. {error}")
                lines.append("")
            
            if result.warnings:
                lines.append("=" * 70)
                lines.append(f"WARNINGS ({len(result.warnings)})")
                lines.append("=" * 70)
                for i, warning in enumerate(result.warnings, 1):
                    lines.append(f"{i}. {warning}")
                lines.append("")
            
            # Write to file
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            
            self.notify(f"Report saved: {report_path}", severity="success")
        
        except Exception as e:
            self.notify(f"Error saving report: {str(e)}", severity="error")
        except Exception as e:
            self.notify(f"Error browsing files: {str(e)}", severity="error")
    
    async def update_campaign_analytics_display(self) -> None:
        """Update the campaign analytics display based on stack detection."""
        try:
            if not self.campaign_analytics_manager:
                return
            
            stack_info = self.campaign_analytics_manager.get_stack_info()
            
            # Update stack status text
            status_text = self.query_one("#stack-status-text", Static)
            config_set_text = self.query_one("#stack-configuration-set", Static)
            info_text = self.query_one("#stack-info-text", Static)
            
            # Helper function to show/hide widgets
            def set_widget_visibility(widget_id: str, visible: bool):
                try:
                    widget = self.query_one(f"#{widget_id}")
                    widget.display = visible
                except:
                    pass  # nosec B110 - Intentional - widget may not be mounted
            
            if stack_info['deployed']:
                status_text.update(f"✅ Stack Deployed: {stack_info['stack_name']}")
                
                config_set_text.update(
                    f"📌 Configuration Set: {stack_info['configuration_set']}\n"
                    f"⚠️  Use this configuration set when sending emails to track campaign analytics"
                )
                
                info_text.update(
                    f"Database: {stack_info['database']}\n"
                    f"Workgroup: {stack_info['workgroup']}\n"
                    f"Region: {stack_info['region']}"
                )
                
                # Hide deployment instructions
                set_widget_visibility("deploy-title", False)
                set_widget_visibility("deploy-description", False)
                set_widget_visibility("deploy-steps", False)
                set_widget_visibility("refresh-stack-detection-btn", False)
                
                # Show data processing section
                set_widget_visibility("processing-title", True)
                set_widget_visibility("processing-help", True)
                set_widget_visibility("processing-row", True)
                set_widget_visibility("check-refresh-row", True)
                set_widget_visibility("check-refresh-tip", True)
                
                # Show analytics controls
                set_widget_visibility("filters-title", True)
                set_widget_visibility("filters-help", True)
                set_widget_visibility("campaign-select-row", True)
                set_widget_visibility("campaign-filter-tip", True)
                set_widget_visibility("date-row", True)
                set_widget_visibility("date-filter-tip", True)
                
                # Show performance overview
                set_widget_visibility("overview-title", True)
                set_widget_visibility("performance-overview-text", True)
                
                # Show campaign table and details
                set_widget_visibility("table-title", True)
                set_widget_visibility("campaign-analytics-table", True)
                set_widget_visibility("campaign-table-status", True)
                set_widget_visibility("metadata-title", True)
                set_widget_visibility("campaign-metadata-display", True)
                
                # Show hidden campaigns toggle
                try:
                    set_widget_visibility("show-hidden-row", True)
                    set_widget_visibility("show-hidden-tip", True)
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"Could not set show-hidden visibility: {e}")
                
                # Hide action buttons initially (shown when campaign selected)
                try:
                    set_widget_visibility("campaign-action-row", False)
                    set_widget_visibility("campaign-action-tip", True)
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"Could not set action buttons visibility: {e}")
                
                # Load campaign list into dropdown
                await self.populate_campaign_dropdown()
                
                # Load initial data
                await self.load_campaign_analytics_data()
                
            else:
                status_text.update("❌ Campaign Analytics Stack Not Deployed")
                config_set_text.update("")
                
                # Show debug info about what was searched
                region_info = f"Region: {self.current_region}\n"
                region_info += "Searched all CloudFormation stacks for required outputs:\n"
                region_info += "- SesConfigurationSetName\n"
                region_info += "- GlueDatabaseName\n"
                region_info += "- AthenaWorkGroupName\n"
                region_info += "- AthenaResultsBucketName\n\n"
                region_info += "Enable debug logging in Settings to see which stacks were checked."
                info_text.update(region_info)
                
                # Show deployment instructions
                set_widget_visibility("deploy-title", True)
                set_widget_visibility("deploy-description", True)
                set_widget_visibility("deploy-steps", True)
                set_widget_visibility("refresh-stack-detection-btn", True)
                
                # Hide data processing section
                set_widget_visibility("processing-title", False)
                set_widget_visibility("processing-help", False)
                set_widget_visibility("processing-row", False)
                set_widget_visibility("check-refresh-row", False)
                set_widget_visibility("check-refresh-tip", False)
                
                # Hide analytics controls and data
                set_widget_visibility("filters-title", False)
                set_widget_visibility("filters-help", False)
                set_widget_visibility("campaign-select-row", False)
                set_widget_visibility("campaign-filter-tip", False)
                set_widget_visibility("date-row", False)
                set_widget_visibility("date-filter-tip", False)
                
                # Hide performance overview
                set_widget_visibility("overview-title", False)
                set_widget_visibility("performance-overview-text", False)
                
                # Hide campaign table and details
                set_widget_visibility("table-title", False)
                set_widget_visibility("campaign-analytics-table", False)
                set_widget_visibility("campaign-table-status", False)
                set_widget_visibility("metadata-title", False)
                set_widget_visibility("campaign-metadata-display", False)
                
                # Hide show hidden toggle and action buttons
                set_widget_visibility("show-hidden-row", False)
                set_widget_visibility("show-hidden-tip", False)
                set_widget_visibility("campaign-action-row", False)
                set_widget_visibility("campaign-action-tip", False)
            
        except Exception as e:
            self.notify(f"Error updating campaign analytics display: {str(e)}", severity="error")
    
    async def load_campaign_analytics_data(self, days: int = 30, campaign_name: str = None, force_refresh: bool = False) -> None:
        """
        Load and display campaign analytics data.
        
        Args:
            days: Number of days to analyze (default 30, max 180)
            campaign_name: Optional specific campaign to filter
            force_refresh: Force refresh from Athena (ignore cache)
        """
        try:
            if not self.campaign_analytics_manager or not self.campaign_analytics_manager.stack_deployed:
                return
            
            # Load performance overview for the same period
            overview = await self.campaign_analytics_manager.get_performance_overview(days=days)
            if overview:
                overview_text = self.query_one("#performance-overview-text", Static)
                overview_text.update(
                    f"Total Campaigns: {int(float(overview.get('total_campaigns', 0) or 0)):,}\n"
                    f"Total Sent: {int(float(overview.get('total_sent', 0) or 0)):,}\n"
                    f"Total Delivered: {int(float(overview.get('total_delivered', 0) or 0)):,}\n"
                    f"Total Hard Bounces: {int(float(overview.get('total_hard_bounces', 0) or 0)):,}\n"
                    f"Total Soft Bounces: {int(float(overview.get('total_soft_bounces', 0) or 0)):,}\n"
                    f"Total Complaints: {int(float(overview.get('total_complaints', 0) or 0)):,}\n"
                    f"Total Rendering Failures: {int(float(overview.get('total_rendering_failures', 0) or 0)):,}\n"
                    f"Avg Delivery Rate: {float(overview.get('avg_delivery_rate', 0) or 0):.2f}%\n"
                    f"Avg Hard Bounce Rate: {float(overview.get('avg_hard_bounce_rate', 0) or 0):.2f}%\n"
                    f"Avg Complaint Rate: {float(overview.get('avg_complaint_rate', 0) or 0):.2f}%\n"
                    f"Avg Rendering Failure Rate: {float(overview.get('avg_rendering_failure_rate', 0) or 0):.2f}%\n"
                    f"Avg Open Rate: {float(overview.get('avg_open_rate', 0) or 0):.2f}%\n"
                    f"Avg Click Rate: {float(overview.get('avg_click_rate', 0) or 0):.2f}%"
                )
            
            # Load campaigns for the period
            campaigns = await self.campaign_analytics_manager.get_campaigns_by_period(
                days=days,
                campaign_name=campaign_name,
                limit=100,
                force_refresh=force_refresh,
                show_hidden=self.show_hidden_campaigns
            )
            
            # Update table
            table = self.query_one("#campaign-analytics-table", DataTable)
            table.clear(columns=True)
            
            # Add columns including hard bounces, soft bounces, complaints, and rendering failures
            table.add_columns(
                "Campaign", "Start Date", "Finish Date", "Sent", "Delivered", 
                "Hard Bounces", "Soft Bounces", "Complaints", "Render Fails", "Opened", "Clicked", 
                "Delivery %", "Hard Bounce %", "Complaint %", "Render Fail %", "Open %", "Click %"
            )
            
            # Add rows with fallbacks for old data
            for campaign in campaigns:
                try:
                    # Handle missing bounce/complaint/rendering failure fields (old data compatibility)
                    hard_bounces = campaign.get('total_hard_bounces', '0') or '0'
                    soft_bounces = campaign.get('total_soft_bounces', '0') or '0'
                    complaints = campaign.get('total_complaints', '0') or '0'
                    rendering_failures = campaign.get('total_rendering_failures', '0') or '0'
                    hard_bounce_rate = float(campaign.get('overall_hard_bounce_rate', 0) or 0)
                    complaint_rate = float(campaign.get('overall_complaint_rate', 0) or 0)
                    
                    # Format numbers with commas
                    total_sent = campaign.get('total_sent', '0')
                    total_delivered = campaign.get('total_delivered', '0')
                    total_opened = campaign.get('total_opened', '0')
                    total_clicked = campaign.get('total_clicked', '0')
                    
                    # Convert to int and format with commas
                    try:
                        total_sent_fmt = f"{int(total_sent):,}"
                    except (ValueError, TypeError):
                        total_sent_fmt = str(total_sent)
                    
                    try:
                        total_delivered_fmt = f"{int(total_delivered):,}"
                    except (ValueError, TypeError):
                        total_delivered_fmt = str(total_delivered)
                    
                    try:
                        hard_bounces_fmt = f"{int(hard_bounces):,}"
                    except (ValueError, TypeError):
                        hard_bounces_fmt = str(hard_bounces)
                    
                    try:
                        soft_bounces_fmt = f"{int(soft_bounces):,}"
                    except (ValueError, TypeError):
                        soft_bounces_fmt = str(soft_bounces)
                    
                    try:
                        complaints_fmt = f"{int(complaints):,}"
                    except (ValueError, TypeError):
                        complaints_fmt = str(complaints)
                    
                    try:
                        rendering_failures_fmt = f"{int(rendering_failures):,}"
                    except (ValueError, TypeError):
                        rendering_failures_fmt = str(rendering_failures)
                    
                    try:
                        total_opened_fmt = f"{int(total_opened):,}"
                    except (ValueError, TypeError):
                        total_opened_fmt = str(total_opened)
                    
                    try:
                        total_clicked_fmt = f"{int(total_clicked):,}"
                    except (ValueError, TypeError):
                        total_clicked_fmt = str(total_clicked)
                    
                    # Handle rate fields with proper fallback for empty strings
                    delivery_rate = campaign.get('overall_delivery_rate', 0) or 0
                    open_rate = campaign.get('overall_open_rate', 0) or 0
                    click_rate = campaign.get('overall_click_rate', 0) or 0
                    rendering_failure_rate = campaign.get('overall_rendering_failure_rate', 0) or 0
                    
                    # Convert to float safely
                    try:
                        delivery_rate_val = float(delivery_rate) if delivery_rate != '' else 0.0
                    except (ValueError, TypeError):
                        delivery_rate_val = 0.0
                    
                    try:
                        open_rate_val = float(open_rate) if open_rate != '' else 0.0
                    except (ValueError, TypeError):
                        open_rate_val = 0.0
                    
                    try:
                        click_rate_val = float(click_rate) if click_rate != '' else 0.0
                    except (ValueError, TypeError):
                        click_rate_val = 0.0
                    
                    try:
                        rendering_failure_rate_val = float(rendering_failure_rate) if rendering_failure_rate != '' else 0.0
                    except (ValueError, TypeError):
                        rendering_failure_rate_val = 0.0
                    
                    # Check if campaign is hidden (only when showing hidden campaigns)
                    campaign_name_display = campaign.get('campaign_name', '')
                    campaign_id = campaign.get('campaign_id')
                    if self.show_hidden_campaigns:
                        # Check metadata to see if this campaign is hidden
                        if campaign_id and self.campaign_analytics_manager:
                            metadata = await self.campaign_analytics_manager.get_campaign_metadata(campaign_id)
                            if metadata and metadata.get('is_hidden', False):
                                campaign_name_display = f"🔒 {campaign_name_display}"
                    
                    # Use campaign_id as the row key for reliable lookups
                    table.add_row(
                        campaign_name_display,
                        campaign.get('first_send_date', ''),
                        campaign.get('last_send_date', ''),
                        total_sent_fmt,
                        total_delivered_fmt,
                        hard_bounces_fmt,
                        soft_bounces_fmt,
                        complaints_fmt,
                        rendering_failures_fmt,
                        total_opened_fmt,
                        total_clicked_fmt,
                        f"{delivery_rate_val:.1f}%",
                        f"{hard_bounce_rate:.1f}%",
                        f"{complaint_rate:.1f}%",
                        f"{rendering_failure_rate_val:.1f}%",
                        f"{open_rate_val:.1f}%",
                        f"{click_rate_val:.1f}%",
                        key=campaign_id
                    )
                except Exception as row_error:
                    if self.logger:
                        self.logger.warning(f"Error adding row for campaign: {row_error}")
            
            # Update status
            status = self.query_one("#campaign-table-status", Static)
            period_text = f"last {days} days"
            campaign_text = f" for '{campaign_name}'" if campaign_name else ""
            status.update(f"Showing {len(campaigns)} campaigns from {period_text}{campaign_text} (max 180-day range)")
            
            self.notify("Campaign analytics data loaded successfully", severity="success")
            
        except Exception as e:
            self.notify(f"Error loading campaign analytics data: {str(e)}", severity="error")
            if self.logger:
                self.logger.error(f"Error loading campaign analytics: {str(e)}")
    
    async def handle_refresh_stack_detection(self) -> None:
        """Handle refresh stack detection button."""
        try:
            if self.campaign_analytics_manager and self.current_profile and self.current_region:
                self.notify("Detecting CDK stack...", severity="information")
                await self.campaign_analytics_manager.detect_cdk_stack(
                    self.current_profile,
                    self.current_region
                )
                await self.update_campaign_analytics_display()
            else:
                self.notify("Campaign analytics manager not initialized", severity="warning")
        except Exception as e:
            self.notify(f"Error detecting stack: {str(e)}", severity="error")
    
    async def populate_campaign_dropdown(self) -> None:
        """Populate the campaign dropdown with available campaigns."""
        try:
            if not self.campaign_analytics_manager or not self.campaign_analytics_manager.stack_deployed:
                return
            
            # Load campaign list (30 days by default)
            campaigns = await self.campaign_analytics_manager.get_campaign_list(days=30)
            
            if not campaigns:
                return
            
            # Update dropdown with campaign list
            from textual.widgets import Select
            campaign_select = self.query_one("#campaign-select", Select)
            
            # Set options: All Campaigns + actual campaigns
            campaign_select.set_options(
                [("All Campaigns", "all")] + [(name, name) for name in campaigns]
            )
            
            if self.logger:
                self.logger.debug(f"Populated dropdown with {len(campaigns)} campaigns")
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error populating campaign dropdown: {str(e)}")
    
    async def handle_clear_campaign_search(self) -> None:
        """Handle clear campaign selection."""
        try:
            from textual.widgets import Select
            campaign_select = self.query_one("#campaign-select", Select)
            campaign_select.value = "all"
            
            # Reload all campaigns (30 days by default)
            await self.load_campaign_analytics_data(days=30)
            
        except Exception as e:
            self.notify(f"Error clearing search: {str(e)}", severity="error")
    
    async def handle_filter_campaigns_by_date(self) -> None:
        """Handle filter by date button with 180-day limit."""
        try:
            from textual.widgets import Input
            
            start_input = self.query_one("#start-date-input", Input)
            end_input = self.query_one("#end-date-input", Input)
            
            # Input widgets have a value property that returns a string
            start_date = start_input.value.strip() or None
            end_date = end_input.value.strip() or None
            
            if not start_date and not end_date:
                self.notify("Please enter at least one date", severity="warning")
                return
            
            self.notify("Filtering campaigns by date range...", severity="information")
            
            # Get selected campaign from dropdown
            campaign_name = None
            try:
                from textual.widgets import Select
                campaign_select = self.query_one("#campaign-select", Select)
                if campaign_select.value and campaign_select.value != 'all':
                    campaign_name = campaign_select.value
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
            campaigns, days_in_range = await self.campaign_analytics_manager.get_date_filtered_campaigns(
                start_date=start_date,
                end_date=end_date,
                campaign_name=campaign_name
            )
            
            # Check if date range exceeds 180 days
            if days_in_range > 180:
                self.notify(
                    f"⚠️  Date range exceeds maximum of 180 days ({days_in_range} days selected). "
                    f"Please select a shorter date range.",
                    severity="error",
                    timeout=10
                )
                return
            
            # Update performance overview for the same date range
            overview = await self.campaign_analytics_manager.get_performance_overview(
                start_date=start_date,
                end_date=end_date
            )
            if overview:
                overview_text = self.query_one("#performance-overview-text", Static)
                overview_text.update(
                    f"Total Campaigns: {int(float(overview.get('total_campaigns', 0) or 0)):,}\n"
                    f"Total Sent: {int(float(overview.get('total_sent', 0) or 0)):,}\n"
                    f"Total Delivered: {int(float(overview.get('total_delivered', 0) or 0)):,}\n"
                    f"Total Hard Bounces: {int(float(overview.get('total_hard_bounces', 0) or 0)):,}\n"
                    f"Total Soft Bounces: {int(float(overview.get('total_soft_bounces', 0) or 0)):,}\n"
                    f"Total Complaints: {int(float(overview.get('total_complaints', 0) or 0)):,}\n"
                    f"Total Rendering Failures: {int(float(overview.get('total_rendering_failures', 0) or 0)):,}\n"
                    f"Avg Delivery Rate: {float(overview.get('avg_delivery_rate', 0) or 0):.2f}%\n"
                    f"Avg Hard Bounce Rate: {float(overview.get('avg_hard_bounce_rate', 0) or 0):.2f}%\n"
                    f"Avg Complaint Rate: {float(overview.get('avg_complaint_rate', 0) or 0):.2f}%\n"
                    f"Avg Rendering Failure Rate: {float(overview.get('avg_rendering_failure_rate', 0) or 0):.2f}%\n"
                    f"Avg Open Rate: {float(overview.get('avg_open_rate', 0) or 0):.2f}%\n"
                    f"Avg Click Rate: {float(overview.get('avg_click_rate', 0) or 0):.2f}%"
                )
            
            # Update table
            await self.update_campaign_table(campaigns, is_filtered=True)
            
            status = self.query_one("#campaign-table-status", Static)
            date_range = f"{start_date or 'start'} to {end_date or 'end'}"
            campaign_text = f" for '{campaign_name}'" if campaign_name else ""
            status.update(f"Found {len(campaigns)} campaigns in date range: {date_range}{campaign_text} (max 180-day range)")
            
        except Exception as e:
            self.notify(f"Error filtering campaigns: {str(e)}", severity="error")
    
    async def handle_refresh_campaign_analytics(self) -> None:
        """Handle refresh analytics button."""
        try:
            self.notify("Refreshing campaign analytics...", severity="information")
            # Force refresh (30 days by default)
            campaigns = await self.campaign_analytics_manager.get_campaigns_by_period(
                days=30,
                force_refresh=True
            )
            await self.load_campaign_analytics_data(days=30)
        except Exception as e:
            self.notify(f"Error refreshing analytics: {str(e)}", severity="error")
    
    async def handle_campaign_selection_change(self, campaign_value: str) -> None:
        """Handle campaign dropdown selection change."""
        try:
            if campaign_value == "all":
                # Show all campaigns
                await self.load_campaign_analytics_data(days=30)
            else:
                # Show specific campaign
                await self.load_campaign_analytics_data(days=30, campaign_name=campaign_value)
        except Exception as e:
            self.notify(f"Error filtering by campaign: {str(e)}", severity="error")
    
    async def handle_invoke_refresh_lambda(self) -> None:
        """Handle invoke refresh Lambda button to manually trigger materialized view refresh."""
        try:
            if not self.campaign_analytics_manager or not self.campaign_analytics_manager.stack_deployed:
                self.notify("Campaign analytics stack not deployed", severity="warning")
                return
            
            # Get Lambda function name from stack outputs
            stack_info = self.campaign_analytics_manager.get_stack_info()
            lambda_name = stack_info['outputs'].get('RefreshLambdaName')
            
            if not lambda_name:
                self.notify("Refresh Lambda function name not found in stack outputs", severity="error")
                return
            
            # Update status indicator to "Processing..."
            try:
                status_indicator = self.query_one("#lambda-status-indicator", Static)
                status_indicator.update("⏳ Processing...")
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
            self.notify(f"Processing today's data...", severity="information")
            
            # Create Lambda client using same session/profile
            import boto3
            if self.current_profile and self.current_profile != 'default':
                session = boto3.Session(profile_name=self.current_profile)
            else:
                session = boto3.Session()
            
            lambda_client = session.client('lambda', region_name=self.current_region)
            
            # Prepare payload with today's date
            from datetime import date
            import json
            
            payload = {
                'date': date.today().strftime('%Y-%m-%d')
            }
            
            # Invoke Lambda asynchronously with today's date
            response = lambda_client.invoke(
                FunctionName=lambda_name,
                InvocationType='Event',  # Async invocation
                Payload=json.dumps(payload)
            )
            
            if response['StatusCode'] == 202:
                # Update status indicator to "Complete"
                try:
                    status_indicator = self.query_one("#lambda-status-indicator", Static)
                    status_indicator.update("✅ Job Started (1-2 min)")
                except:
                    pass  # nosec B110 - Intentional - widget may not be mounted
                
                self.notify(
                    "✅ Processing job started successfully!\n"
                    "Data will be available in 1-2 minutes. Click '🔄 Refresh Data & Check Today' to refresh.",
                    severity="success",
                    timeout=10
                )
                
                if self.logger:
                    self.logger.info(f"Successfully invoked Lambda: {lambda_name}")
                
                # Reset status after 120 seconds
                await asyncio.sleep(120)
                try:
                    status_indicator = self.query_one("#lambda-status-indicator", Static)
                    status_indicator.update("Ready")
                except:
                    pass  # nosec B110 - Intentional - widget may not be mounted
            else:
                # Update status indicator to error
                try:
                    status_indicator = self.query_one("#lambda-status-indicator", Static)
                    status_indicator.update("❌ Error")
                except:
                    pass  # nosec B110 - Intentional - widget may not be mounted
                
                self.notify(
                    f"Lambda invoked with status code: {response['StatusCode']}",
                    severity="warning"
                )
            
        except Exception as e:
            # Update status indicator to error
            try:
                status_indicator = self.query_one("#lambda-status-indicator", Static)
                status_indicator.update("❌ Error")
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
            self.notify(f"Error invoking refresh Lambda: {str(e)}", severity="error")
            if self.logger:
                self.logger.error(f"Error invoking Lambda: {str(e)}")
    
    async def handle_check_and_refresh(self) -> None:
        """Check if Lambda job is complete and refresh data."""
        try:
            if not self.campaign_analytics_manager or not self.campaign_analytics_manager.stack_deployed:
                self.notify("Campaign analytics stack not deployed", severity="warning")
                return
            
            # Update status indicator
            try:
                status_indicator = self.query_one("#lambda-status-indicator", Static)
                status_indicator.update("🔄 Refreshing...")
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
            self.notify("Refreshing campaign analytics data...", severity="information")
            
            # Get current date to check for today's data
            from datetime import date
            today = date.today().strftime('%Y-%m-%d')
            
            # First, always refresh the displayed data
            await self.load_campaign_analytics_data(days=30, force_refresh=True)
            
            # Then check if today's data exists
            try:
                import boto3
                if self.current_profile and self.current_profile != 'default':
                    session = boto3.Session(profile_name=self.current_profile)
                else:
                    session = boto3.Session()
                
                athena_client = session.client('athena', region_name=self.current_region)
                stack_info = self.campaign_analytics_manager.get_stack_info()
                database = stack_info['database']
                
                # Quick query to check if today's data exists
                # nosec B608 - No user input in SQL query
                check_query = f"""
                SELECT COUNT(*) as count
                FROM {database}.campaign_metrics_daily
                WHERE date = '{today}'
                """
                
                response = athena_client.start_query_execution(
                    QueryString=check_query,
                    QueryExecutionContext={'Database': database},
                    ResultConfiguration={
                        'OutputLocation': f"s3://{stack_info['outputs']['AthenaResultsBucketName']}/check-results/"
                    },
                    WorkGroup=stack_info['workgroup']
                )
                
                query_id = response['QueryExecutionId']
                
                # Wait for query to complete (max 30 seconds)
                import time
                for _ in range(30):
                    status_response = athena_client.get_query_execution(QueryExecutionId=query_id)
                    status = status_response['QueryExecution']['Status']['State']
                    
                    if status == 'SUCCEEDED':
                        # Get results
                        results = athena_client.get_query_results(QueryExecutionId=query_id)
                        if len(results['ResultSet']['Rows']) > 1:
                            count_value = results['ResultSet']['Rows'][1]['Data'][0].get('VarCharValue', '0')
                            has_data = int(count_value) > 0
                            
                            status_indicator = self.query_one("#lambda-status-indicator", Static)
                            
                            if has_data:
                                status_indicator.update("✅ Today's Data Available")
                                self.notify(
                                    f"✅ Data refreshed! Today's data is available.\n"
                                    f"Found {count_value} campaigns with data for {today}",
                                    severity="success",
                                    timeout=10
                                )
                            else:
                                status_indicator.update("⏳ Today's Data Pending")
                                self.notify(
                                    "✅ Data refreshed! However, no data for today yet.\n"
                                    "Lambda job may still be processing. Try again in a moment.",
                                    severity="information",
                                    timeout=10
                                )
                        break
                    elif status in ['FAILED', 'CANCELLED']:
                        raise Exception(f"Query failed: {status}")
                    
                    await asyncio.sleep(1)
                
            except Exception as check_error:
                if self.logger:
                    self.logger.error(f"Error checking job status: {str(check_error)}")
                
                status_indicator = self.query_one("#lambda-status-indicator", Static)
                status_indicator.update("Ready")
                
                self.notify(
                    f"✅ Data refreshed! (Could not verify today's data status)",
                    severity="information"
                )
                
        except Exception as e:
            try:
                status_indicator = self.query_one("#lambda-status-indicator", Static)
                status_indicator.update("❌ Error")
            except:
                pass  # nosec B110 - Intentional - widget may not be mounted
            
            self.notify(f"Error refreshing data: {str(e)}", severity="error")
            if self.logger:
                self.logger.error(f"Error in check and refresh: {str(e)}")
    
    async def handle_show_all_campaigns(self) -> None:
        """Handle show all campaigns button - resets to default 30-day view."""
        try:
            from textual.widgets import Input, Select
            
            # Reset campaign dropdown to "All Campaigns"
            campaign_select = self.query_one("#campaign-select", Select)
            campaign_select.value = "all"
            
            # Clear date inputs
            start_input = self.query_one("#start-date-input", Input)
            end_input = self.query_one("#end-date-input", Input)
            
            start_input.value = ""
            end_input.value = ""
            
            # Reload all campaigns (30 days by default)
            await self.load_campaign_analytics_data(days=30)
            
            self.notify("Reset to default view: Last 30 days, all campaigns", severity="success")
            
        except Exception as e:
            self.notify(f"Error resetting view: {str(e)}", severity="error")
    
    async def handle_toggle_show_hidden_checkbox(self, checked: bool) -> None:
        """Toggle showing/hiding hidden campaigns based on checkbox state."""
        try:
            self.show_hidden_campaigns = checked
            
            if self.show_hidden_campaigns:
                self.notify("Now showing hidden campaigns", severity="information")
            else:
                self.notify("Hidden campaigns are now filtered out", severity="information")
            
            # Re-display campaigns from cache without re-querying Athena
            if self.campaign_analytics_manager and self.campaign_analytics_manager.campaigns_cache:
                if self.logger:
                    self.logger.debug(f"Toggle show_hidden={checked}, using cache")
                await self.refresh_campaign_display_from_cache()
            else:
                # If no cache exists yet, load data normally (will create cache)
                await self.load_campaign_analytics_data(days=30, force_refresh=False)
            
        except Exception as e:
            self.notify(f"Error toggling hidden campaigns: {str(e)}", severity="error")
            if self.logger:
                self.logger.error(f"Error toggling hidden campaigns: {str(e)}")
    
    async def refresh_campaign_display_from_cache(self) -> None:
        """Refresh campaign display using cached data with current show_hidden filter."""
        try:
            if not self.campaign_analytics_manager or not self.campaign_analytics_manager.campaigns_cache:
                return
            
            from textual.widgets import DataTable, Static
            
            # Get cached campaigns
            all_campaigns = self.campaign_analytics_manager.campaigns_cache
            
            # Filter based on show_hidden state
            if not self.show_hidden_campaigns and self.campaign_analytics_manager.metadata_manager:
                filtered_campaigns = []
                for campaign in all_campaigns:
                    metadata = await self.campaign_analytics_manager.get_campaign_metadata(campaign['campaign_id'])
                    if not metadata or not metadata.get('is_hidden', False):
                        filtered_campaigns.append(campaign)
                campaigns_to_display = filtered_campaigns
                
                if self.logger:
                    hidden_count = len(all_campaigns) - len(filtered_campaigns)
                    if hidden_count > 0:
                        self.logger.debug(f"Filtered {hidden_count} hidden → {len(filtered_campaigns)} visible")
            else:
                campaigns_to_display = all_campaigns
            
            # Update the table with filtered campaigns
            table = self.query_one("#campaign-analytics-table", DataTable)
            table.clear(columns=True)
            
            # Add columns
            table.add_columns(
                "Campaign", "Start Date", "Finish Date", "Sent", "Delivered", 
                "Hard Bounces", "Soft Bounces", "Complaints", "Render Fails", "Opened", "Clicked", 
                "Delivery %", "Hard Bounce %", "Complaint %", "Render Fail %", "Open %", "Click %"
            )
            
            # Add rows
            for campaign in campaigns_to_display:
                try:
                    # Format numbers
                    hard_bounces = campaign.get('total_hard_bounces', '0') or '0'
                    soft_bounces = campaign.get('total_soft_bounces', '0') or '0'
                    complaints = campaign.get('total_complaints', '0') or '0'
                    rendering_failures = campaign.get('total_rendering_failures', '0') or '0'
                    hard_bounce_rate = float(campaign.get('overall_hard_bounce_rate', 0) or 0)
                    complaint_rate = float(campaign.get('overall_complaint_rate', 0) or 0)
                    
                    total_sent = campaign.get('total_sent', '0')
                    total_delivered = campaign.get('total_delivered', '0')
                    total_opened = campaign.get('total_opened', '0')
                    total_clicked = campaign.get('total_clicked', '0')
                    
                    # Format with commas
                    try:
                        total_sent_fmt = f"{int(total_sent):,}"
                        total_delivered_fmt = f"{int(total_delivered):,}"
                        hard_bounces_fmt = f"{int(hard_bounces):,}"
                        soft_bounces_fmt = f"{int(soft_bounces):,}"
                        complaints_fmt = f"{int(complaints):,}"
                        rendering_failures_fmt = f"{int(rendering_failures):,}"
                        total_opened_fmt = f"{int(total_opened):,}"
                        total_clicked_fmt = f"{int(total_clicked):,}"
                    except (ValueError, TypeError):
                        total_sent_fmt = str(total_sent)
                        total_delivered_fmt = str(total_delivered)
                        hard_bounces_fmt = str(hard_bounces)
                        soft_bounces_fmt = str(soft_bounces)
                        complaints_fmt = str(complaints)
                        rendering_failures_fmt = str(rendering_failures)
                        total_opened_fmt = str(total_opened)
                        total_clicked_fmt = str(total_clicked)
                    
                    # Handle rates
                    delivery_rate = float(campaign.get('overall_delivery_rate', 0) or 0)
                    open_rate = float(campaign.get('overall_open_rate', 0) or 0)
                    click_rate = float(campaign.get('overall_click_rate', 0) or 0)
                    rendering_failure_rate = float(campaign.get('overall_rendering_failure_rate', 0) or 0)
                    
                    # Check if campaign is hidden (only when showing hidden campaigns)
                    campaign_name_display = campaign.get('campaign_name', '')
                    campaign_id = campaign.get('campaign_id')
                    if self.show_hidden_campaigns:
                        if campaign_id and self.campaign_analytics_manager:
                            metadata = await self.campaign_analytics_manager.get_campaign_metadata(campaign_id)
                            if metadata and metadata.get('is_hidden', False):
                                campaign_name_display = f"🔒 {campaign_name_display}"
                    
                    # Use campaign_id as the row key for reliable lookups
                    table.add_row(
                        campaign_name_display,
                        campaign.get('first_send_date', ''),
                        campaign.get('last_send_date', ''),
                        total_sent_fmt,
                        total_delivered_fmt,
                        hard_bounces_fmt,
                        soft_bounces_fmt,
                        complaints_fmt,
                        rendering_failures_fmt,
                        total_opened_fmt,
                        total_clicked_fmt,
                        f"{delivery_rate:.1f}%",
                        f"{hard_bounce_rate:.1f}%",
                        f"{complaint_rate:.1f}%",
                        f"{rendering_failure_rate:.1f}%",
                        f"{open_rate:.1f}%",
                        f"{click_rate:.1f}%",
                        key=campaign_id
                    )
                except Exception as row_error:
                    if self.logger:
                        self.logger.warning(f"Error adding row: {row_error}")
            
            # Update status
            status = self.query_one("#campaign-table-status", Static)
            status.update(f"Showing {len(campaigns_to_display)} campaigns (filtered from cache)")
            
        except Exception as e:
            self.notify(f"Error refreshing display: {str(e)}", severity="error")
            if self.logger:
                self.logger.error(f"Error refreshing display from cache: {str(e)}")
    
    async def handle_hide_campaign(self) -> None:
        """Hide the currently selected campaign."""
        try:
            from textual.widgets import Static
            
            if not self.selected_campaign_id:
                self.notify("No campaign selected", severity="warning")
                return
            
            if not self.campaign_analytics_manager:
                self.notify("Campaign analytics manager not available", severity="error")
                return
            
            # Check if action buttons exist
            try:
                action_row = self.query_one("#campaign-action-row")
            except Exception as e:
                self.notify("Action buttons not found. Please restart the application.", severity="error")
                if self.logger:
                    self.logger.error(f"Action buttons not found: {e}")
                return
            
            # Confirm action
            campaign_name = self.selected_campaign_name or "this campaign"
            self.notify(f"Hiding campaign: {campaign_name}...", severity="information")
            
            # Hide the campaign
            success = self.campaign_analytics_manager.hide_campaign(self.selected_campaign_id)
            
            if success:
                self.notify(f"Campaign '{campaign_name}' hidden successfully", severity="success")
                # Reload campaigns to reflect changes
                await self.load_campaign_analytics_data(days=30, force_refresh=True)
                # Clear selection
                self.selected_campaign_id = None
                self.selected_campaign_name = None
                # Hide action buttons
                action_row.display = False
                # Reset metadata display
                metadata_display = self.query_one("#campaign-metadata-display", Static)
                metadata_display.update("👆 Select a campaign from the table above to view detailed information")
            else:
                self.notify(f"Failed to hide campaign '{campaign_name}'", severity="error")
            
        except Exception as e:
            self.notify(f"Error hiding campaign: {str(e)}", severity="error")
            if self.logger:
                self.logger.error(f"Error hiding campaign: {str(e)}")
    
    async def handle_unhide_campaign(self) -> None:
        """Unhide the currently selected campaign."""
        try:
            from textual.widgets import Static
            
            if not self.selected_campaign_id:
                self.notify("No campaign selected", severity="warning")
                return
            
            if not self.campaign_analytics_manager:
                self.notify("Campaign analytics manager not available", severity="error")
                return
            
            # Check if action buttons exist
            try:
                action_row = self.query_one("#campaign-action-row")
            except Exception as e:
                self.notify("Action buttons not found. Please restart the application.", severity="error")
                if self.logger:
                    self.logger.error(f"Action buttons not found: {e}")
                return
            
            # Confirm action
            campaign_name = self.selected_campaign_name or "this campaign"
            self.notify(f"Unhiding campaign: {campaign_name}...", severity="information")
            
            # Unhide the campaign
            success = self.campaign_analytics_manager.unhide_campaign(self.selected_campaign_id)
            
            if success:
                self.notify(f"Campaign '{campaign_name}' unhidden successfully", severity="success")
                # Reload campaigns to reflect changes
                await self.load_campaign_analytics_data(days=30, force_refresh=True)
                # Clear selection
                self.selected_campaign_id = None
                self.selected_campaign_name = None
                # Hide action buttons
                action_row.display = False
                # Reset metadata display
                metadata_display = self.query_one("#campaign-metadata-display", Static)
                metadata_display.update("👆 Select a campaign from the table above to view detailed information")
            else:
                self.notify(f"Failed to unhide campaign '{campaign_name}'", severity="error")
            
        except Exception as e:
            self.notify(f"Error unhiding campaign: {str(e)}", severity="error")
            if self.logger:
                self.logger.error(f"Error unhiding campaign: {str(e)}")
    
    async def update_campaign_table(self, campaigns: list, is_filtered: bool = False) -> None:
        """Update the campaign analytics table with new data."""
        try:
            table = self.query_one("#campaign-analytics-table", DataTable)
            table.clear(columns=True)
            
            # Same columns for both filtered and standard data (removed "Days Active")
            table.add_columns(
                "Campaign", "Start Date", "Finish Date", "Sent", "Delivered",
                "Opened", "Clicked", "Render Fails", "Delivery %", "Open %", "Click %"
            )
            
            for campaign in campaigns:
                try:
                    # Format numbers with commas
                    total_sent = campaign.get('total_sent', '0')
                    total_delivered = campaign.get('total_delivered', '0')
                    total_opened = campaign.get('total_opened', '0')
                    total_clicked = campaign.get('total_clicked', '0')
                    rendering_failures = campaign.get('total_rendering_failures', '0') or '0'
                    
                    try:
                        total_sent_fmt = f"{int(total_sent):,}"
                    except (ValueError, TypeError):
                        total_sent_fmt = str(total_sent)
                    
                    try:
                        total_delivered_fmt = f"{int(total_delivered):,}"
                    except (ValueError, TypeError):
                        total_delivered_fmt = str(total_delivered)
                    
                    try:
                        total_opened_fmt = f"{int(total_opened):,}"
                    except (ValueError, TypeError):
                        total_opened_fmt = str(total_opened)
                    
                    try:
                        total_clicked_fmt = f"{int(total_clicked):,}"
                    except (ValueError, TypeError):
                        total_clicked_fmt = str(total_clicked)
                    
                    try:
                        rendering_failures_fmt = f"{int(rendering_failures):,}"
                    except (ValueError, TypeError):
                        rendering_failures_fmt = str(rendering_failures)
                    
                    # Handle rate fields with proper fallback for empty strings
                    delivery_rate = campaign.get('overall_delivery_rate', 0) or campaign.get('avg_delivery_rate', 0) or 0
                    open_rate = campaign.get('overall_open_rate', 0) or campaign.get('avg_open_rate', 0) or 0
                    click_rate = campaign.get('overall_click_rate', 0) or campaign.get('avg_click_rate', 0) or 0
                    
                    # Convert to float safely
                    try:
                        delivery_rate_val = float(delivery_rate) if delivery_rate != '' else 0.0
                    except (ValueError, TypeError):
                        delivery_rate_val = 0.0
                    
                    try:
                        open_rate_val = float(open_rate) if open_rate != '' else 0.0
                    except (ValueError, TypeError):
                        open_rate_val = 0.0
                    
                    try:
                        click_rate_val = float(click_rate) if click_rate != '' else 0.0
                    except (ValueError, TypeError):
                        click_rate_val = 0.0
                    
                    # Use campaign_id as the row key for reliable lookups
                    campaign_id = campaign.get('campaign_id')
                    table.add_row(
                        campaign.get('campaign_name', ''),
                        campaign.get('first_send_date', ''),
                        campaign.get('last_send_date', ''),
                        total_sent_fmt,
                        total_delivered_fmt,
                        total_opened_fmt,
                        total_clicked_fmt,
                        rendering_failures_fmt,
                        f"{delivery_rate_val:.1f}%",
                        f"{open_rate_val:.1f}%",
                        f"{click_rate_val:.1f}%",
                        key=campaign_id
                    )
                except Exception as row_error:
                    if self.logger:
                        self.logger.warning(f"Error adding row: {row_error}")
            
        except Exception as e:
            self.notify(f"Error updating campaign table: {str(e)}", severity="error")
    
    async def handle_refresh_config_sets(self) -> None:
        """Handle refresh configuration sets button."""
        try:
            if self.ses_client:
                # Force refresh by clearing cache
                from modules.cache_manager import CacheManager
                cache_mgr = CacheManager(self.settings)
                cache_mgr.invalidate_cache("get_configuration_sets")
                
                # Get fresh list
                config_sets = self.ses_client.get_configuration_sets()
                
                # Update dropdown
                from textual.widgets import Select
                config_select = self.query_one("#default-config-set-select", Select)
                
                saved_default = self.settings_manager.get_default_configuration_set()
                options = [("None", "")] + [(cs, cs) for cs in config_sets]
                config_select.set_options(options)
                if saved_default:
                    config_select.value = saved_default
                
                self.notify(f"Refreshed {len(config_sets)} configuration sets", severity="success")
        except Exception as e:
            self.notify(f"Error refreshing config sets: {str(e)}", severity="error")
    
    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()


if __name__ == "__main__":
    app = SESManagerApp()
    app.run()
