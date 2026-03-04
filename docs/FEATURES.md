# Features Guide

Comprehensive overview of all features, UI components, and technical mechanics.

## Table of Contents

- [Dashboard Tab](#dashboard-tab)
- [Templates Tab](#templates-tab)
- [Send Email Tab](#send-email-tab)
- [Campaign Analytics Tab](#campaign-analytics-tab)
- [Scheduled Campaigns Tab](#scheduled-campaigns-tab)
- [Settings Tab](#settings-tab)
- [Technical Mechanics](#technical-mechanics)

---

## Dashboard Tab

Quick overview of your Amazon SES account status and recent email metrics.

### Account Overview

Displays cached SES account details (refreshed on demand):
- **Send Quota**: 24-hour limit for your account
- **Sent Count**: Emails sent in last 24 hours
- **Maximum Send Rate**: Emails per second allowed
- **Account Status**: Production or sandbox mode

**Why cached?** Amazon SES management APIs are limited to 1 TPS. Caching prevents rate limit errors while keeping data reasonably fresh (60-minute TTL).

### Metrics Visualization

Email delivery metrics from CloudWatch API:
- **Time periods**: 1h, 24h, 2d, 7d, 30d
- **Metrics tracked**: Sends, Deliveries, Opens, Clicks, Bounces, Complaints
- **Calculated rates**: Delivery rate, Open rate, Click rate
- **Format**: Table display with color coding

### Data Caching

Automatic caching with configurable TTL:
- **Account details**: 60 minutes
- **Metrics data**: 5 minutes
- **Templates**: 30 minutes
- **Identities**: 60 minutes
- **Configuration sets**: 120 minutes

**Rationale**: Amazon SES has strict rate limits (1 TPS) on management APIs like `list_configuration_sets` and `list_templates`. Caching prevents hitting these limits during normal usage.

---

## Templates Tab

Manage SES email templates with CRUD operations and browser preview.

### Template Management

**List View**:
- Template name, subject, creation date
- Sortable and searchable
- Click to select for editing or deletion

**Create Template**:
1. Click "Create Template" button
2. Enter template name (unique identifier)
3. Enter subject line (can include placeholders like `{{subject_variable}}`)
4. Add HTML content (full email body)
5. Add plain text version (fallback for non-HTML clients)
6. Save

**Edit Template**:
1. Select template from list
2. Click "Edit" button
3. Modify fields as needed
4. Save (overwrites existing template)

**Delete Template**:
1. Select template from list
2. Click "Delete" button
3. Confirm deletion (irreversible)

**Preview in Browser**:
1. Select template from list
2. Click "Preview" button
3. Browser opens showing rendered HTML
4. Placeholders displayed as-is (e.g., `{{name}}`)

### Template Features

**Automatic Placeholder Detection**:
- Scans template for `{{placeholder}}` syntax
- Extracts all placeholder names
- Used to auto-populate template data fields in Send Email tab

**Recommendation**: Use third-party visual email builders:
- [Stripo](https://stripo.email/)
- [Unlayer](https://unlayer.com/)
- [BeeFree](https://beefree.io/)
- [MJML](https://mjml.io/)

Design your email visually, export HTML, then paste into template.

---

## Send Email Tab

Unified interface for single emails, bulk CSV campaigns, and scheduled sends.

### Mode Selection

Three modes available:
1. **Single Email**: Send one email immediately
2. **Bulk Email (CSV)**: Send to many recipients immediately with progress tracking
3. **Scheduled Campaign**: Schedule bulk send for future execution (requires CDK stack)

### Single Email Mode

**Form Fields**:
- **From Identity** (required): Verified email or domain
- **Custom Email Prefix**: For domain identities (e.g., `user@domain.com`)
- **Template** (required): Select from available templates
- **Template Data**: Auto-populated JSON with placeholders
- **To Address** (required): Recipient email
- **CC**: Comma-separated email addresses
- **BCC**: Comma-separated email addresses
- **Configuration Set**: For event tracking (optional)
- **SES Tags**: JSON object for categorization (optional)
- **Unsubscribe Settings**: See [Unsubscribe Handling](#unsubscribe-handling)

**Template Data Auto-Population**:
When you select a template, the Template Data field automatically fills with a JSON object containing all placeholders found in that template. For example, if your template contains `{{name}}` and `{{company}}`, you'll see:
```json
{
  "name": "",
  "company": ""
}
```
Simply replace the empty strings with actual values.

**Email Log**:
After sending, displays the Amazon SES v2 SendEmail API response including:
- Success/failure status
- Message ID (if successful)
- Error details (if failed)

### Bulk Email Mode (Local Execution)

**CSV Format**:
```csv
To_Address,sub_name,sub_company,sub_discount
user1@example.com,John Doe,Acme Corp,20%
user2@example.com,Jane Smith,Tech Inc,15%
```

- **Required column**: `To_Address`
- **Substitution columns**: Prefix with `sub_` (e.g., `sub_name`)
- Prefix automatically removed when creating template data (`sub_name` → `{{name}}`)
- **Template variable validation**: CSV columns automatically validated against selected template
  - Missing template variables in CSV → Error (blocks sending)
  - Extra CSV columns not in template → Error (blocks sending)
  - System variables (unsubscribe_link) automatically excluded from validation

**Form Fields**:
- **CSV File Path**: Browse and select file
- **Campaign Name**: Identifier for tracking (auto-sanitized for SES tags)
- **Campaign Description**: Optional metadata
- **Campaign Creator**: Optional metadata (e.g., your name/email)
- **From Identity**: Verified email or domain
- **Template**: Select from available templates
- **Base Template Data**: Default values for all emails (optional)
- **Configuration Set**: For event tracking
- **SES Tags**: JSON object (campaign_id and campaign_name auto-added)
- **Emails Per Second**: Custom rate or auto (uses MaxSendRate)

**Campaign Metadata**:
If you provide campaign name, description, and creator, these are stored in DynamoDB (if analytics CDK stack is deployed) for later reference in Campaign Analytics tab.

**Rate Limiting**:
- **Automatic Detection**: Queries Amazon SES for MaxSendRate
- **Custom Override**: Specify emails per second (0 to MaxSendRate)
- **Semaphore-Based**: Uses asyncio semaphore to limit concurrent sends
- **Important**: Set to 85-90% of MaxSendRate to account for async efficiency boost (~10-15% higher actual TPS)

**Progress Tracking**:
- **Real-time Progress Bar**: Shows completion % and ETA
- **Live Statistics**: Success, Failed, Throttled counts, Total retries
- **Time Display**: Elapsed time, Emails/sec rate, Average API duration
- **Controls**: Pause, Resume, Cancel buttons

**Results Export**:
Automatically saved to `bulk_email_csv/bulk_email_output/`:
- Filename: `{original_name}_results_{timestamp}.csv`
- Columns: To_Address, Status, MessageId, Timestamp, Error (if failed)

### Scheduled Campaign Mode (Cloud Execution)

**Requirements**: [ses-scheduled-campaigns](../../ses_scheduled_campaigns/) CDK stack deployed in same region.

**Form Fields**:
All bulk email fields (CSV, campaign name, etc.) plus:
- **Schedule Date & Time**: Future datetime in `YYYY-MM-DD HH:MM` format (24-hour)
- Validation ensures schedule time is in the future

**How It Works**:
1. CSV uploaded to S3 bucket
2. DynamoDB entry created with TTL (execution time + 1 hour)
3. EventBridge rule created for scheduled time
4. Campaign metadata written to analytics table (if deployed)
5. At scheduled time, EventBridge triggers Lambda
6. Lambda reads CSV, processes recipients, adds messages to SQS
7. Separate Lambda (EmailSender) reads from SQS and sends emails
8. 1 hour after execution, TTL expires and cleanup Lambda removes resources

**Sending Rate Configuration**:
Rate controlled by EmailSender Lambda concurrency settings (not per-campaign):
- **Default**: 1 TPS
- **To adjust**: AWS Console → Lambda → EmailSender → Configuration → Concurrency
- **Formula**: Reserved Concurrency × 20 = Target TPS
- **Example**: 5 reserved concurrency = ~100 TPS

**Benefits**:
- Schedule days/weeks in advance
- No need to keep TUI running
- Automatic execution at specified time
- SQS provides buffering and retry capabilities

### Unsubscribe Handling

**Why It Matters**: Gmail and Yahoo now require one-click unsubscribe for bulk senders ([AWS Blog](https://aws.amazon.com/blogs/messaging-and-targeting/using-one-click-unsubscribe-with-amazon-ses/)).

**Unsubscribe Types**:
1. **None**: No unsubscribe functionality (not recommended for bulk email)
2. **Link in Email**: Adds `{{unsubscribe_link}}` to template data
   - You must add this placeholder to your email template
   - User clicks link to unsubscribe
3. **List-Unsubscribe Headers**: Adds RFC 8058 compliant headers
   - `List-Unsubscribe`: URL and/or mailto
   - `List-Unsubscribe-Post`: For one-click unsubscribe
   - Email clients display native unsubscribe button
4. **Both**: Combines link and headers (recommended)

**Campaign Topic** (Optional):
Categorize unsub

scribes by type (e.g., "newsletter", "promotions"). Helps track which email types users are unsubscribing from.

**Implementation Required**:
- You must build landing page to handle unsubscribe requests
- You must build API endpoint for one-click unsubscribe (POST requests)
- Email addresses are encrypted with Fernet for security
- Configure URLs and encryption key in Settings tab

---

## Campaign Analytics Tab

Monitor and analyze email campaign performance using AWS CDK-deployed analytics infrastructure.

**Requirements**: [ses-campaign-analytics](../../ses_campaign_analytics/) CDK stack deployed in same region.

### Stack Detection

**Automatic**: Detects deployed CDK stack on tab load by querying CloudFormation for required outputs:
- `SesConfigurationSetName`
- `GlueDatabaseName`
- `AthenaWorkGroupName`
- `AthenaResultsBucketName`
- `RefreshLambdaName`

**Manual Refresh**: Click "Refresh Stack Detection" if stack not found.

### Data Source

**Materialized View**: All queries use `campaign_metrics_daily` table
- Pre-aggregated data stored as Parquet in S3
- Cost-efficient (~$0.00 per query within free tier)
- Data refreshed nightly by Lambda

**Manual Processing**: Click "⚡ Process Today's Data" button
- Triggers Lambda to aggregate today's SES events
- Async processing (1-2 minutes)
- Click "🔍 Check & Refresh" to verify and update table

### Campaign Performance View

**Performance Overview** (Last 30 days by default):
- Total campaigns
- Total emails sent/delivered
- Average delivery/open/click rates
- Bounce and complaint metrics

**Campaign Performance Table**:
- Campaign name
- Start/finish dates (first and last send dates)
- Total sends, deliveries, opens, clicks, bounces, complaints
- Delivery/open/click/bounce/complaint percentages
- Click row to view details

**Campaign Details** (Selected Row):
If campaign metadata was stored:
- Campaign name, ID, description
- Creator, template name
- From address, configuration set
- Creation timestamp, total recipients

### Filtering Options

**Three-Step Process**:

1. **Date Range Filter** (affects campaign list):
   - Enter start/end dates (YYYY-MM-DD)
   - Leave empty for default last 30 days
   - Maximum: 180 days (prevents expensive queries)
   - Click "Apply Date Filter"

2. **Campaign Filter** (optional):
   - Dropdown auto-populated with campaigns from date range
   - Select specific campaign or keep "All Campaigns"
   - Updates table automatically

3. **Show Hidden Campaigns** (toggle):
   - Switch to show/hide campaigns marked as hidden
   - Uses cached data (no Athena query)
   - Hidden campaigns display with 🔒 icon prefix

### Campaign Management

**Hide/Unhide Campaigns** (Soft Delete):
- Select campaign from table
- Click "🗑️ Hide Campaign" to remove from main view
- Click "👁️ Unhide Campaign" to restore visibility
- Hidden status shown in campaign details (🔒 HIDDEN or ✅ Visible)
- Useful for archiving old campaigns or removing test campaigns from view
- Data preserved in analytics (not permanently deleted)

**Performance Optimization**:
- Toggling "Show Hidden" uses cached data only (instant)
- No Athena queries when switching visibility
- Date range changes still query Athena as expected

### Important Notes

- **Campaign Tag Required**: Emails must include `campaign` tag to appear in analytics
- **Configuration Set**: Must use CDK-deployed config set when sending
- **Data Timing**: Automatic nightly processing + optional manual processing for current day
- **Hidden Campaigns**: Soft delete feature for organizing campaign view

---

## Scheduled Campaigns Tab

View and manage scheduled email campaigns awaiting execution.

**Requirements**: [ses-scheduled-campaigns](../../ses_scheduled_campaigns/) CDK stack deployed in same region.

### Campaign Management

**View Upcoming Campaigns**:
- Table shows only future campaigns (past campaigns auto-filtered)
- Columns: Name, ID, Scheduled Time, Template, From Email, Recipients, Status
- Click row to view details

**Campaign Details Panel**:
- Campaign metadata: name, ID, scheduled time
- Configuration: template, from address, config set
- S3 CSV file location
- Creation timestamp
- Total recipient count

**Delete Campaign** (Before Execution):
1. Select campaign from table
2. Click "❌ Delete Selected" button
3. Confirmation dialog explains what will be deleted:
   - EventBridge rule (prevents execution)
   - S3 CSV file (recipient list)
   - Scheduler DynamoDB entry
   - Analytics metadata entry (if deployed)
4. Confirm (irreversible)

### Automatic Cleanup (TTL)

**Process**:
1. Campaign executes at scheduled time via EventBridge
2. 1 hour later: DynamoDB TTL expires campaign record
3. DynamoDB Stream triggers cleanup Lambda
4. Cleanup Lambda removes:
   - EventBridge rule
   - S3 CSV file
   - DynamoDB scheduler entry
5. Analytics data preserved in analytics table

**Data Lifecycle**:
- **Transient** (1 hour after execution): DynamoDB entries, EventBridge rules, S3 CSV files
- **Permanent**: Campaign analytics, SES event data in S3

---

## Settings Tab

Configure application behavior, AWS access, and feature settings.

### AWS Configuration

- **Profile Selection**: Switch between AWS CLI profiles
- **Region Selection**: Choose AWS region for SES operations
- **Current Configuration**: Display active profile and region

### Debug Logging

- **Toggle**: Enable/disable verbose logging
- **When enabled**: Writes detailed logs to `debug_logs.txt`
- **Categories**: API calls, Cache operations, UI events, Email operations
- **Use for**: Troubleshooting API errors, cache issues, unexpected behavior

### Email Settings

**Default Configuration Set**:
- Set default config set for event tracking
- Auto-selected in Send Email form

**Retry Settings**:
- **Max Retries**: 0-10 (default: 3)
- **Base Delay**: 0.1-10.0 seconds (default: 1.0s)
- Applies to local immediate sending only

### Unsubscribe Configuration

**Encryption Key**:
- Click "Generate Key" to create new Fernet key
- Or paste existing key
- Used to encrypt email addresses in unsubscribe URLs

**URL Configuration**:
- **Unsubscribe Base URL**: Landing page (e.g., `https://example.com/unsubscribe`)
  - Receives encrypted email in `user` parameter
  - Optional `topic` parameter
- **One-Click Unsubscribe Endpoint**: API endpoint (e.g., `https://api.example.com/unsubscribe`)
  - Receives encrypted email in `address` parameter
  - Must handle POST requests
- **Mailto Address**: Alternative email-based unsubscribe (optional)

**Validation**: Click "Validate Configuration" to check completeness.

### Cache Management

- **View Statistics**: Hit/miss rates, file count, total size
- **Clear Cache**: Remove all cached files
- **Automatic Cleanup**: Expired entries removed on access

### Settings Management

- **Export**: Save all settings to JSON file
- **Import**: Load settings from JSON file
- **Reset**: Restore default settings

---

## Technical Mechanics

### Rate Limiting Deep Dive

**Local (Immediate Sending)**:
- Uses asyncio semaphore to limit concurrent tasks
- Semaphore value = configured emails per second
- **Why 85-90% of MaxSendRate?** Async I/O efficiency means actual TPS will be 10-15% higher
- Example: MaxSendRate=100, set semaphore=85-90 → actual rate ~95-105/sec
- Monitor "⚠️ Throttled" counter and adjust if needed

**Scheduled (Cloud Sending)**:
- SQS queue with Lambda concurrency controls
- Lambda processes SQS messages in batches
- Formula: Reserved Concurrency × 20 = Target TPS
  - 20 is rough estimate of SendEmail calls per second per concurrent execution
- Backpressure: If Lambda can't keep up, messages stay in queue
- No semaphore needed - SQS provides natural buffering

### Retry Logic Deep Dive

**Local (Immediate Sending)**:
- **Exponential Backoff**: `delay = base_delay × (2^attempt)`
- **Throttling Errors**: Use `2 × base_delay` for longer waits
- **Max Delay**: Capped at 30 seconds
- **Retryable Errors**: Throttling, ServiceUnavailable, InternalError, RequestTimeout, HTTP 429/500/502/503/504
- **Non-Retryable**: InvalidParameterValue, MessageRejected, AccountSuspended, HTTP 400/403/404

**Scheduled (Cloud Sending)**:
- **Transient Errors**: Message returned to SQS for retry
- **Permanent Errors**: Message moved to DLQ
- **SQS Configuration**: Visibility timeout, max receives, retry delay
- **DLQ Analysis**: Check DLQ for permanent failures

### Caching Layer Deep Dive

**Why Critical**:
Amazon SES management APIs have strict 1 TPS rate limits:
- `list_templates`
- `list_configuration_sets`
- `list_identities`
- `get_account`

Without caching, normal TUI usage would quickly exceed these limits.

**How It Works**:
- First request: Fetch from AWS, store in `cache/` directory as JSON
- Subsequent requests: Load from cache if not expired
- Expiration: Each operation has configurable TTL
- Manual invalidation: Settings → Cache Management → Clear Cache

**Cache Files**:
- `get_templates.json`: Template list (30 min TTL)
- `get_identities.json`: Verified identities (60 min TTL)
- `get_configuration_sets.json`: Config sets (120 min TTL)
- `get_account_details.json`: Account quota/limits (60 min TTL)
- `get_metrics_data_*.json`: CloudWatch metrics (5 min TTL, varies by period)

### Unsubscribe Encryption

**Process**:
1. Generate Fernet key (256-bit symmetric encryption)
2. Store key in settings
3. When sending email with unsubscribe:
   - Encrypt recipient email with Fernet
   - Generate URL with encrypted email as parameter
   - Add to template data or headers
4. User clicks unsubscribe link
5. Your landing page/API receives encrypted email
6. Decrypt with same Fernet key to identify user
7. Process unsubscribe in your system

**Security**: Email addresses never exposed in plain text in URLs. Fernet provides authenticated encryption with expiration support.

---

For deployment details, see [Installation Guide](INSTALLATION.md).  
For usage instructions, see [Usage Guide](USAGE.md).
