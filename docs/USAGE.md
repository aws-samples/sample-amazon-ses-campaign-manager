# Usage Guide

Step-by-step instructions, workflows, and best practices for common tasks.

## Table of Contents

- [Quick Start Workflow](#quick-start-workflow)
- [Template Management](#template-management)
- [Sending Single Emails](#sending-single-emails)
- [Bulk CSV Campaigns](#bulk-csv-campaigns)
- [Scheduled Campaigns](#scheduled-campaigns)
- [Campaign Analytics](#campaign-analytics)
- [Best Practices](#best-practices)

---

## Quick Start Workflow

### Your First Email Send

**Step 1: Verify Setup**
1. Launch TUI: `python3 ses_manager_modular.py`
2. Navigate to Dashboard tab
3. Verify:
   - Account details displayed
   - Send quota shows available
   - At least one verified identity listed

**Step 2: Create/Select Template**
1. Navigate to Templates tab
2. If no templates exist:
   - Click "Create Template"
   - Enter name (e.g., `welcome_email`)
   - Add subject: `Welcome to {{company}}!`
   - Add HTML: `<h1>Hello {{name}}!</h1><p>Welcome to {{company}}.</p>`
   - Add plain text version
   - Save
3. If templates exist, select one

**Step 3: Send Test Email**
1. Navigate to Send Email tab
2. Ensure mode is "Single Email"
3. Select your verified identity from "From Identity"
4. Select template
5. Template Data field auto-populates - fill in values:
   ```json
   {
     "name": "John Doe",
     "company": "Acme Corp"
   }
   ```
6. Enter your email in "To" field
7. Click "Send Email"
8. Check Email Log for success confirmation

**Step 4: Check Your Inbox**
- Email should arrive within seconds
- If not, check spam folder
- Review debug logs if issues occur

---

## Template Management

### Creating a Professional Template

**Best Practice Workflow**:

1. **Design in Visual Builder** (recommended):
   - Use [Stripo](https://stripo.email/), [Unlayer](https://unlayer.com/), or [BeeFree](https://beefree.io/)
   - Design your email layout visually
   - Add placeholders where needed: `{{variable_name}}`
   - Export as HTML

2. **Create in TUI**:
   - Templates tab → "Create Template"
   - Name: `newsletter_2024_q4` (descriptive, unique)
   - Subject: `{{subject_line}}` (can be dynamic)
   - Paste exported HTML
   - Create plain text version (simplified, no HTML)
   - Save

3. **Test Template**:
   - Select template in list
   - Click "Preview"
   - Browser opens showing rendered HTML
   - Verify layout, images, links work

4. **Send Test Email**:
   - Send Email tab
   - Use template with real placeholder data
   - Send to yourself
   - Verify rendering in email client

### Updating Existing Templates

**Workflow**:
1. Templates tab
2. Select template to edit
3. Click "Edit"
4. Modify HTML/subject/text
5. Save (overwrites existing)
6. Preview to verify changes
7. Send test email

**Important**: Template edits affect all future sends immediately. No versioning supported - consider naming templates with versions (e.g., `welcome_v1`, `welcome_v2`).

### Template Best Practices

**Naming**:
- Use lowercase, underscores
- Be descriptive: `welcome_email`, `password_reset`, `monthly_newsletter`
- Include version if needed: `promo_summer_2024_v2`

**Placeholders**:
- Use clear names: `{{first_name}}`, not `{{fn}}`
- Document required placeholders
- Provide fallback values where possible

**HTML**:
- Test in multiple email clients (Gmail, Outlook, Apple Mail)
- Use inline CSS (most email clients strip `<style>` tags)
- Keep width under 600px for mobile compatibility
- Always include plain text version

---

## Sending Single Emails

### Basic Single Email

**Workflow**:
1. Send Email tab → Mode: "Single Email"
2. From Identity: Select verified email/domain
3. Template: Choose template
4. Template Data: Auto-populated - fill in values
5. To: Recipient email
6. Click "Send Email"
7. Check Email Log for result

### Single Email with CC/BCC

**Workflow**:
1. Follow basic workflow above
2. Expand "Optional Settings"
3. CC: Enter comma-separated emails
   ```
   manager@example.com, team@example.com
   ```
4. BCC: Enter comma-separated emails
   ```
   archive@example.com
   ```
5. Send

### Single Email with Tracking

**Workflow**:
1. Ensure configuration set exists (Dashboard tab shows config sets)
2. Send Email tab → Optional Settings
3. Configuration Set: Select from dropdown
4. SES Tags: Add tracking tags
   ```json
   {
     "campaign_type": "transactional",
     "user_segment": "premium"
   }
   ```
5. Send
6. Events tracked via configuration set (sends, opens, clicks, bounces)

### Single Email with Unsubscribe

**Setup** (one-time):
1. Settings tab → Unsubscribe Configuration
2. Generate encryption key
3. Enter unsubscribe page URL
4. Enter one-click API URL
5. Save

**Send**:
1. Send Email tab
2. Expand "Unsubscribe Settings"
3. Type: Select "Both" (recommended)
4. Category: Enter (e.g., "newsletter")
5. Ensure template includes `{{unsubscribe_link}}` placeholder
6. Send

---

## Bulk CSV Campaigns

### CSV Validation

**Automatic Validation on File Selection**

When you browse and select a CSV file, it's automatically validated before loading. This prevents wasting time filling out the form only to discover the CSV is invalid.

**Validation Process**:
1. Click "Browse" button
2. Select CSV file
3. Validation runs immediately
4. See summary notification:
   - ✅ Valid: "CSV Valid: X recipients (Y warnings)"
   - ❌ Invalid: "CSV Invalid: X error(s), Y warning(s)"
5. Click "View Report" button to see full details

**Blocking Errors** (Prevent Sending):
- **File Issues**:
  - File not found or not readable
  - File size exceeds 50MB
  - File doesn't have .csv extension
- **Structure Issues**:
  - Missing required `To_Address` column
  - CSV file has no headers
  - CSV file is empty (no data rows)
  - File exceeds 50,000 row limit
- **Data Issues**:
  - Invalid email format in `To_Address` column
  - Empty email address in `To_Address` column
  - Empty substitution variables (e.g., `sub_name` is blank)
  - No valid recipients found
- **Template Variable Mismatch** (when template selected):
  - Missing template variables: CSV lacks columns for template placeholders
    - Example: Template has `{{FirstName}}` but CSV missing `sub_FirstName` column
  - Extra CSV columns: CSV has substitution columns not used in template
    - Example: CSV has `sub_LastName` but template doesn't use `{{LastName}}`
  - System variables (unsubscribe_link) automatically excluded from validation

**Non-Blocking Warnings** (Allow Sending):
- **Duplicate Emails**: Same email appears multiple times
  - Warning shows row numbers
  - Email will only be sent once per occurrence
- **No Substitution Columns**: CSV has no `sub_` prefixed columns
  - Template variables won't be replaced
  - Static template will be sent to all recipients
- **Column Issues**:
  - Empty column names in header
  - Duplicate column names

**Validation Report**:
- Click "View Report" button (enabled after validation)
- Modal popup shows:
  - Summary: file name, status, row counts, error/warning counts
  - Full list of all errors (scrollable)
  - Full list of all warnings (scrollable)
- Actions:
  - "Save Report": Saves to `bulk_email_csv/bulk_email_output/validation_report_[filename]_[timestamp].txt`
  - "Close": Closes the modal

**Best Practices**:
1. Fix all errors before attempting to send
2. Review warnings - they may indicate data quality issues
3. Save validation reports for record keeping
4. Test with small CSV first (10-20 rows) before full campaign

### Preparing Your CSV File

**Required Format**:
```csv
To_Address,sub_name,sub_company,sub_discount,sub_expires
john@example.com,John Doe,Acme Corp,20%,2024-12-31
jane@example.com,Jane Smith,Tech Inc,15%,2024-12-31
```

**Rules**:
- First column MUST be `To_Address` (case-sensitive)
- Other columns prefixed with `sub_` (e.g., `sub_name`)
- Prefix automatically removed for template data
- UTF-8 encoding
- No special characters in column names
- Maximum 50,000 rows
- Maximum 50MB file size

**Example Template for CSV Above**:
```html
<p>Hi {{name}},</p>
<p>Get {{discount}} off at {{company}}!</p>
<p>Offer expires {{expires}}</p>
```

**Column Naming**:
- `To_Address` → Recipient email (required)
- `sub_name` → Template variable `{{name}}`
- `sub_company` → Template variable `{{company}}`
- `sub_discount` → Template variable `{{discount}}`

### Basic Bulk Send

**Workflow**:
1. Prepare CSV file
2. Send Email tab → Mode: "Bulk Email (CSV)"
3. Browse and select CSV file
4. Campaign Name: `black_friday_2024`
5. From Identity: Select verified email/domain
6. Template: Select template
7. Configuration Set: Select (recommended for tracking)
8. Click "Send Email"
9. Monitor progress:
   - Progress bar shows completion
   - Statistics show success/fail counts
   - Watch for throttling warnings
10. Results automatically saved to `bulk_email_csv/bulk_email_output/`

### Bulk Send with Rate Limiting

**Scenario**: You have MaxSendRate of 100/sec but want to send slower.

**Workflow**:
1. Dashboard tab → Note your MaxSendRate
2. Send Email tab → Bulk mode
3. Expand "Bulk Email Settings"
4. Emails Per Second: Enter custom rate
   - Recommendation: 85-90% of MaxSendRate
   - Example: MaxSendRate=100 → enter 85-90
5. Send
6. Monitor "⚠️ Throttled" counter
7. If throttling occurs, reduce rate for next send

### Bulk Send with Campaign Metadata

**Purpose**: Track campaign in analytics, add context.

**Workflow**:
1. Send Email tab → Bulk mode
2. Campaign Name: `summer_promo_2024`
3. Campaign Description: `Summer sale 30% off all items`
4. Campaign Creator: `john.doe@example.com`
5. If analytics stack deployed:
   - Metadata saved to DynamoDB
   - Viewable in Campaign Analytics tab
6. Send

### Handling Bulk Send Issues

**Pause/Resume**:
- Click "⏸️ Pause" to pause sending
- Review current statistics
- Click "▶️ Resume" to continue
- Elapsed time excludes paused duration

**Cancel**:
- Click "🛑 Cancel" to stop immediately
- Partial results still saved
- Use "Clear Form" to reset for new send

**Reviewing Results**:
1. Navigate to `bulk_email_csv/bulk_email_output/`
2. Open latest results CSV
3. Columns:
   - `To_Address`: Recipient
   - `Status`: success or failed
   - `MessageId`: Amazon SES message ID (if successful)
   - `Timestamp`: When sent
   - `Error`: Error details (if failed)
4. Filter by Status to identify failures
5. Retry failed emails if needed

---

## Scheduled Campaigns

**Prerequisites**: [ses-scheduled-campaigns](../../ses_scheduled_campaigns/) CDK stack deployed.

### Scheduling a Campaign

**Workflow**:
1. Prepare CSV file (same format as bulk send)
2. Send Email tab → Mode: "Scheduled Campaign"
3. Fill bulk email fields:
   - CSV file
   - Campaign name, description, creator
   - From identity, template
   - Configuration set (recommended)
4. Schedule Date & Time: Enter future datetime
   ```
   2024-12-25 14:00
   ```
   Format: `YYYY-MM-DD HH:MM` (24-hour)
5. Validation ensures future time
6. Click "Send Email" (actually schedules, doesn't send immediately)
7. Confirmation shows:
   - Campaign ID
   - Scheduled time
   - Recipient count

**What Happens**:
1. CSV uploaded to S3
2. DynamoDB entry created
3. EventBridge rule scheduled
4. At scheduled time, Lambda executes automatically
5. Emails sent via SQS → Lambda pipeline
6. 1 hour after execution, resources cleaned up

### Managing Scheduled Campaigns

**View Upcoming**:
1. Scheduled Campaigns tab
2. Table shows only future campaigns
3. Click row to view details

**Delete Before Execution**:
1. Select campaign from table
2. Click "❌ Delete Selected"
3. Confirm deletion
4. Resources removed:
   - EventBridge rule
   - S3 CSV file
   - DynamoDB entry
   - Analytics metadata

**Important**: Cannot delete after execution starts. Automatic cleanup occurs 1 hour after execution.

### Configuring Sending Rate

**Scheduled campaigns use Lambda concurrency, not TUI rate setting.**

**To adjust**:
1. AWS Console → Lambda
2. Find EmailSender function
3. Configuration → Concurrency
4. Set Reserved Concurrency
5. Formula: `Concurrency × 20 = Target TPS`
   - Example: 5 concurrency = ~100 TPS
   - Example: 10 concurrency = ~200 TPS

**Recommendation**: Start conservative, monitor CloudWatch logs for throttling.

---

## Campaign Analytics

**Prerequisites**: [ses-campaign-analytics](../../ses_campaign_analytics/) CDK stack deployed.

### Viewing Campaign Performance

**Workflow**:
1. Campaign Analytics tab
2. Verify stack detected (shows configuration set name)
3. Default view: Last 30 days, all campaigns
4. Review Performance Overview:
   - Total campaigns
   - Emails sent/delivered
   - Average rates
5. Scroll to Campaign Performance Table
6. Click any campaign row for details

### Filtering by Date Range

**Scenario**: View campaigns from specific period.

**Workflow**:
1. Campaign Analytics tab
2. Enter Start Date: `2024-10-01`
3. Enter End Date: `2024-10-31`
4. Click "Apply Date Filter"
5. Campaign dropdown updates with campaigns from that range
6. Table shows filtered results

### Filtering by Campaign

**Scenario**: Focus on one campaign's performance.

**Workflow**:
1. First set date range (if needed)
2. Campaign dropdown shows campaigns from that range
3. Select specific campaign
4. Table updates automatically
5. Shows only that campaign's data

### Hiding Campaigns (Soft Delete)

**Scenario**: Remove old or test campaigns from main view without deleting analytics data.

**Hide a Campaign**:
1. Campaign Analytics tab
2. Click campaign row in table to select
3. Campaign details appear below table
4. Click "🗑️ Hide Campaign" button
5. Confirm action
6. Campaign removed from table (unless "Show Hidden" is enabled)

**Unhide a Campaign**:
1. Toggle "Show Hidden" switch to ON
2. Hidden campaigns appear with 🔒 icon prefix
3. Click hidden campaign row to select
4. Click "👁️ Unhide Campaign" button
5. Confirm action
6. Campaign returns to normal view

**Show/Hide Toggle**:
- Switch between showing all campaigns or only visible ones
- Uses cached data (instant, no Athena query)
- Hidden status shown in campaign details panel
- Useful for temporarily viewing archived campaigns

### Processing Today's Data

**Scenario**: Campaign sent today, want immediate analytics (not waiting for nightly refresh).

**Workflow**:
1. Campaign Analytics tab
2. Click "⚡ Process Today's Data"
3. Wait 1-2 minutes (Lambda processing)
4. Click "🔍 Check & Refresh"
5. If data available, table updates
6. If still processing, wait another minute and check again

### Understanding Metrics

**Sends**: Total emails accepted by Amazon SES  
**Deliveries**: Successfully delivered to recipient server  
**Opens**: Recipients who opened email (requires tracking pixel in HTML)  
**Clicks**: Recipients who clicked links (requires tracked links)  
**Bounces**: Failed deliveries (hard bounces = permanent, soft bounces = temporary)  
**Complaints**: Recipients who marked as spam

**Rates**:
- **Delivery Rate**: Deliveries / Sends × 100
- **Open Rate**: Opens / Deliveries × 100
- **Click Rate**: Clicks / Deliveries × 100
- **Bounce Rate**: Bounces / Sends × 100
- **Complaint Rate**: Complaints / Sends × 100

**Healthy Benchmarks**:
- Delivery Rate: >95%
- Open Rate: 15-25% (varies by industry)
- Click Rate: 2-5%
- Bounce Rate: <5%
- Complaint Rate: <0.1%

---

## Best Practices

### Rate Limiting Strategy

**Local Immediate Sending**:
1. Check Dashboard for MaxSendRate
2. Set bulk sending rate to 85-90% of MaxSendRate
3. Monitor "⚠️ Throttled" counter during first 1000 emails
4. If throttling > 5%, reduce rate by 10-20%
5. Find optimal rate through testing

**Scheduled Cloud Sending**:
1. Start with Lambda concurrency of 5 (≈100 TPS)
2. Monitor CloudWatch logs for throttling
3. If no throttling after several campaigns, increase gradually
4. Check DLQ for permanent errors

### Campaign Organization

**Naming Convention**:
- Use descriptive names: `newsletter_2024_nov`, `black_friday_promo`
- Include date/version: `welcome_series_v2`
- Avoid special characters (auto-sanitized anyway)

**Metadata**:
- Always add campaign description
- Include your name/email as creator
- Helps future analysis and troubleshooting

**Tagging**:
- Use consistent SES tags across campaigns
- Examples: `{"type": "promotional"}`, `{"segment": "premium_users"}`
- Enables filtering and analysis later

**Campaign Lifecycle Management**:
- Hide old campaigns to keep analytics view clean
- Use "Show Hidden" toggle to review archived campaigns
- Hidden campaigns still appear in analytics data (not deleted)
- Unhide campaigns if needed for comparison or reference

### Template Management

**Organization**:
- Use clear naming
- Keep templates focused (one purpose per template)
- Version templates when making major changes

**Testing**:
- Always send test email after creating/editing
- Test in multiple email clients
- Verify on mobile devices
- Check all links work

**Placeholders**:
- Document required placeholders
- Provide example CSV files
- Use fallback values: `{{name|"Customer"}}`

### Unsubscribe Compliance

**Gmail/Yahoo Requirements** (as of 2024):
- One-click unsubscribe REQUIRED for bulk senders
- Must use List-Unsubscribe headers
- Unsubscribe must process within 48 hours

**Implementation**:
1. Always use "Both" unsubscribe mode for bulk campaigns
2. Test unsubscribe flow before sending
3. Process unsubscribes promptly
4. Maintain suppression list
5. Don't send to unsubscribed addresses

### Monitoring and Troubleshooting

**Regular Monitoring**:
- Check bounce rates weekly
- Review complaint rates daily
- Monitor delivery rates
- Investigate sudden changes

**Debug Logging**:
- Enable for troubleshooting
- Disable during normal operation (reduces clutter)
- Check logs after any failures

**Cache Management**:
- Clear cache if data seems stale
- Cache should refresh automatically based on TTL
- Manual clear: Settings → Cache Management

### Security Best Practices

**AWS Credentials**:
- Use IAM users with minimum required permissions
- Rotate credentials regularly
- Never share credentials
- Use separate profiles for prod/dev/staging

**Unsubscribe Encryption**:
- Generate unique Fernet key
- Store securely
- Don't share publicly
- Rotate periodically

**Email Content**:
- Never send sensitive data unencrypted
- Use HTTPS for all links
- Verify recipient before including personal data

### Cost Optimization

**SES Costs**:
- First 1000 emails: $0.00 (per month if using AWS Free Tier)
- After: $0.10 per 1,000 emails
- No extra charge for templates, configuration sets

**Optional Stack Costs**:
- Monitor usage in AWS Cost Explorer
- Set billing alerts
- Clean up unused resources
- Scheduled campaigns: Pause if not actively using

**Athena Query Optimization**:
- Use date range filters (max 180 days)
- Query specific campaigns vs "All Campaigns"
- Materialized views reduce costs significantly
- Manual data processing only when needed

---

For feature details, see [Features Guide](FEATURES.md).  
For setup instructions, see [Installation Guide](INSTALLATION.md).
