#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { SesScheduledCampaignsStack } from './ses-scheduled-campaigns-stack';
import * as config from '../config.json';

const app = new cdk.App();

new SesScheduledCampaignsStack(app, 'SesScheduledCampaignsStack', {
  campaignRetentionDays: 90, // Hardcoded: Days to retain campaign metadata
  csvRetentionDays: 30, // Hardcoded: Days to retain CSV files in S3
  enableNotifications: config.enableNotifications,
  notificationEmail: config.notificationEmail || undefined,
  lambdaTimeout: 15, // Hardcoded: Campaign Processor timeout in minutes
  lambdaMemory: 1024, // Hardcoded: Campaign Processor memory in MB
  emailSenderMemory: 512, // Hardcoded: Email Sender memory in MB
  sendingRateTPS: config.sendingRateTPS,
  sqsVisibilityTimeout: config.sqsVisibilityTimeout,
  sqsMessageRetention: 345600, // Hardcoded: 4 days in seconds
  sqsMaxReceiveCount: 3, // Hardcoded: Max retry attempts before DLQ
  dlqMessageRetention: 1209600, // Hardcoded: 14 days in seconds
  enablePointInTimeRecovery: false, // Hardcoded: DynamoDB PITR disabled
  unsubscribeEncryptionKey: config.unsubscribeEncryptionKey,
  unsubscribeBaseUrl: config.unsubscribeBaseUrl,
  unsubscribeEndpointUrl: config.unsubscribeEndpointUrl,
  unsubscribeMailto: config.unsubscribeMailto,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  description: 'Cloud-based scheduled campaign system for Amazon SES',
});

app.synth();
