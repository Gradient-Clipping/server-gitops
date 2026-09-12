-- Provision both accounts in the trusted deployment first; lock agent_view_owner.

-- agent_snapshot must have no existing broad grants or active roles.

USE `agent_source_database`;

GRANT SELECT (`created_at`, `daily_quota`, `display_name`, `enabled`, `id`, `inherit_limits`, `last_login_at`, `rate_limit`, `role`, `username`) ON `agent_source_database`.`platform_users` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_users` AS
SELECT
  `id` AS `id`,
  `username` AS `username`,
  `display_name` AS `display_name`,
  `role` AS `role`,
  `enabled` AS `enabled`,
  `daily_quota` AS `daily_quota`,
  `rate_limit` AS `rate_limit`,
  `inherit_limits` AS `inherit_limits`,
  `created_at` AS `created_at`,
  `last_login_at` AS `last_login_at`
FROM `agent_source_database`.`platform_users`;

GRANT SELECT ON `agent_source_database`.`agent_users` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `daily_quota`, `enabled`, `expires_at`, `id`, `last_used_at`, `name`, `quota`, `rate_limit`, `revoked_at`, `scopes`, `suspended`, `used_quota`, `user_id`) ON `agent_source_database`.`platform_tokens` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_applications` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `name` AS `name`,
  `scopes` AS `scopes`,
  `enabled` AS `enabled`,
  `suspended` AS `suspended`,
  `revoked_at` AS `revoked_at`,
  `daily_quota` AS `daily_quota`,
  `rate_limit` AS `rate_limit`,
  `quota` AS `quota`,
  `used_quota` AS `used_quota`,
  `expires_at` AS `expires_at`,
  `created_at` AS `created_at`,
  `last_used_at` AS `last_used_at`
FROM `agent_source_database`.`platform_tokens`;

GRANT SELECT ON `agent_source_database`.`agent_applications` TO 'agent_snapshot'@'%';

GRANT SELECT (`admitted`, `created_at`, `duration_ms`, `endpoint`, `error_code`, `id`, `limit_scope`, `method`, `request_id`, `retry_after`, `status`, `token_id`, `token_name`, `user_id`, `user_name`) ON `agent_source_database`.`platform_logs` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_request_logs` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `token_id` AS `token_id`,
  `request_id` AS `request_id`,
  `endpoint` AS `endpoint`,
  `method` AS `method`,
  `status` AS `status`,
  `admitted` AS `admitted`,
  `user_name` AS `user_name`,
  `token_name` AS `token_name`,
  `error_code` AS `error_code`,
  `retry_after` AS `retry_after`,
  `limit_scope` AS `limit_scope`,
  `duration_ms` AS `duration_ms`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`platform_logs`;

GRANT SELECT ON `agent_source_database`.`agent_request_logs` TO 'agent_snapshot'@'%';

GRANT SELECT (`bucket`, `day`, `requests`) ON `agent_source_database`.`platform_daily_usage` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_daily_usage` AS
SELECT
  `bucket` AS `bucket`,
  `day` AS `day`,
  `requests` AS `requests`
FROM `agent_source_database`.`platform_daily_usage`;

GRANT SELECT ON `agent_source_database`.`agent_daily_usage` TO 'agent_snapshot'@'%';

GRANT SELECT (`action`, `actor_id`, `created_at`, `id`, `target`) ON `agent_source_database`.`platform_audits` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_audits` AS
SELECT
  `id` AS `id`,
  `actor_id` AS `actor_id`,
  `action` AS `action`,
  `target` AS `target`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`platform_audits`;

GRANT SELECT ON `agent_source_database`.`agent_audits` TO 'agent_snapshot'@'%';

GRANT SELECT (`cooldown`, `daily_quota`, `description`, `message`, `name`, `path`, `rate_limit`, `scope`, `status`, `updated_at`) ON `agent_source_database`.`platform_endpoints` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_endpoints` AS
SELECT
  `path` AS `path`,
  `name` AS `name`,
  `scope` AS `scope`,
  `description` AS `description`,
  `status` AS `status`,
  `message` AS `message`,
  `rate_limit` AS `rate_limit`,
  `daily_quota` AS `daily_quota`,
  `cooldown` AS `cooldown`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`platform_endpoints`;

GRANT SELECT ON `agent_source_database`.`agent_endpoints` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `id`, `title`) ON `agent_source_database`.`platform_announcements` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_announcements` AS
SELECT
  `id` AS `id`,
  `title` AS `title`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`platform_announcements`;

GRANT SELECT ON `agent_source_database`.`agent_announcements` TO 'agent_snapshot'@'%';

GRANT SELECT (`email_enabled`, `error_minimum`, `error_percent`, `errors_enabled`, `expiry_days`, `expiry_enabled`, `in_app`, `language`, `quota_enabled`, `quota_percent`, `rate_enabled`, `updated_at`, `user_id`) ON `agent_source_database`.`platform_notification_preferences` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_notification_preferences` AS
SELECT
  `user_id` AS `user_id`,
  `language` AS `language`,
  `in_app` AS `in_app`,
  `email_enabled` AS `email_enabled`,
  `quota_enabled` AS `quota_enabled`,
  `quota_percent` AS `quota_percent`,
  `errors_enabled` AS `errors_enabled`,
  `error_percent` AS `error_percent`,
  `error_minimum` AS `error_minimum`,
  `rate_enabled` AS `rate_enabled`,
  `expiry_enabled` AS `expiry_enabled`,
  `expiry_days` AS `expiry_days`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`platform_notification_preferences`;

GRANT SELECT ON `agent_source_database`.`agent_notification_preferences` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `email_attempts`, `email_status`, `id`, `in_app`, `kind`, `next_attempt_at`, `read_at`, `title`, `user_id`) ON `agent_source_database`.`platform_notifications` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_notifications` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `kind` AS `kind`,
  `title` AS `title`,
  `read_at` AS `read_at`,
  `in_app` AS `in_app`,
  `email_status` AS `email_status`,
  `email_attempts` AS `email_attempts`,
  `next_attempt_at` AS `next_attempt_at`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`platform_notifications`;

GRANT SELECT ON `agent_source_database`.`agent_notifications` TO 'agent_snapshot'@'%';
