-- Campaign Real-time Status View
-- Queries raw events for today's activity
-- Use this for real-time monitoring of ongoing campaigns

CREATE OR REPLACE VIEW campaign_status_realtime AS
SELECT
  COALESCE(mail.tags['campaign'][1], 'no-campaign') as campaign_name,
  DATE(from_iso8601_timestamp(mail.timestamp)) as date,
  
  -- Volume Metrics
  COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END) as emails_sent,
  COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END) as emails_delivered,
  COUNT(DISTINCT CASE WHEN eventType = 'Open' THEN mail.messageId END) as emails_opened,
  COUNT(DISTINCT CASE WHEN eventType = 'Click' THEN mail.messageId END) as emails_clicked,
  COUNT(DISTINCT CASE WHEN eventType = 'Bounce' THEN mail.messageId END) as bounces,
  COUNT(DISTINCT CASE WHEN eventType = 'Complaint' THEN mail.messageId END) as complaints,
  COUNT(DISTINCT CASE WHEN eventType = 'Rendering Failure' THEN mail.messageId END) as rendering_failures,
  
  -- Latest Activity
  MAX(from_iso8601_timestamp(mail.timestamp)) as last_activity,
  
  -- Quick Rates
  CAST(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END) AS DOUBLE) / 
    NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Send' THEN mail.messageId END), 0) * 100 as delivery_rate,
  
  CAST(COUNT(DISTINCT CASE WHEN eventType = 'Open' THEN mail.messageId END) AS DOUBLE) / 
    NULLIF(COUNT(DISTINCT CASE WHEN eventType = 'Delivery' THEN mail.messageId END), 0) * 100 as open_rate
  
FROM ses_events
WHERE year = YEAR(CURRENT_DATE)
  AND month = MONTH(CURRENT_DATE)
  AND day = DAY(CURRENT_DATE)
GROUP BY 
  COALESCE(mail.tags['campaign'][1], 'no-campaign'),
  DATE(from_iso8601_timestamp(mail.timestamp))
ORDER BY last_activity DESC;
