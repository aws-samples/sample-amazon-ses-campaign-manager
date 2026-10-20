-- Campaign Real-time Status View
-- Queries raw events that arrived today (UTC), for monitoring ongoing campaigns
-- before the nightly materialized view refresh
-- Deployed automatically by the stack (TableCreationTrigger custom resource)

CREATE OR REPLACE VIEW campaign_status_realtime AS
SELECT
  COALESCE(element_at(mail.tags.campaign_id, 1), 'no-campaign-id') as campaign_id,
  COALESCE(element_at(mail.tags.campaign_name, 1), 'no-campaign-name') as campaign_name,

  -- Volume Metrics (events received today, for emails sent on any day)
  COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END) as emails_sent,
  COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END) as emails_delivered,
  COUNT(DISTINCT CASE WHEN eventType = 'Open' THEN mail.messageId END) as emails_opened,
  COUNT(DISTINCT CASE WHEN eventType = 'Click' THEN mail.messageId END) as emails_clicked,
  COUNT(DISTINCT CASE WHEN eventType = 'Bounce' THEN mail.messageId END) as bounces,
  COUNT(DISTINCT CASE WHEN eventType = 'Complaint' THEN mail.messageId END) as complaints,
  COUNT(DISTINCT CASE WHEN eventType = 'Rendering Failure' THEN mail.messageId END) as rendering_failures,

  -- Latest Activity (hour the most recent events arrived)
  MAX(ingest_timestamp) as last_activity_hour

FROM ses_events_raw
WHERE ingest_timestamp >= CAST(current_date AS timestamp)
GROUP BY
  COALESCE(element_at(mail.tags.campaign_id, 1), 'no-campaign-id'),
  COALESCE(element_at(mail.tags.campaign_name, 1), 'no-campaign-name')
