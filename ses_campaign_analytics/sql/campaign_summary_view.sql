-- Campaign Summary View
-- Aggregates all-time campaign metrics from the daily materialized view
-- This view provides a high-level overview of each campaign's performance

CREATE OR REPLACE VIEW campaign_summary AS
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
  SUM(rejects) as total_rejects,
  SUM(rendering_failures) as total_rendering_failures,
  
  -- Overall Rates (calculated from totals)
  CAST(SUM(emails_delivered) AS DOUBLE) / NULLIF(SUM(emails_sent), 0) * 100 as overall_delivery_rate,
  CAST(SUM(emails_opened) AS DOUBLE) / NULLIF(SUM(emails_delivered), 0) * 100 as overall_open_rate,
  CAST(SUM(emails_clicked) AS DOUBLE) / NULLIF(SUM(emails_delivered), 0) * 100 as overall_click_rate,
  CAST(SUM(hard_bounces + soft_bounces) AS DOUBLE) / NULLIF(SUM(emails_sent), 0) * 100 as overall_bounce_rate,
  CAST(SUM(complaints) AS DOUBLE) / NULLIF(SUM(emails_delivered), 0) * 100 as overall_complaint_rate,
  CAST(SUM(rendering_failures) AS DOUBLE) / NULLIF(SUM(emails_sent), 0) * 100 as overall_rendering_failure_rate,
  
  SUM(unique_recipients) as total_unique_recipients,
  AVG(avg_delivery_time_ms) as avg_delivery_time_ms,
  
  COUNT(DISTINCT date) as days_active,
  
  arbitrary(from_address) as from_address,
  arbitrary(sample_subject) as sample_subject
  
FROM campaign_metrics_daily
GROUP BY campaign_id, campaign_name
ORDER BY total_sent DESC;
