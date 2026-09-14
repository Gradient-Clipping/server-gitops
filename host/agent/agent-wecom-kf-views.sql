-- Provision both accounts in the trusted deployment first; lock agent_view_owner.

-- agent_snapshot must have no existing broad grants or active roles.

USE `agent_source_database`;

GRANT SELECT (`external_userid`, `id`, `last_input`, `open_kfid`, `reply_count`, `updated_at`) ON `agent_source_database`.`kf_customers` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_customers` AS
SELECT
  `id` AS `id`,
  `external_userid` AS `external_userid`,
  `open_kfid` AS `open_kfid`,
  `reply_count` AS `reply_count`,
  `last_input` AS `last_input_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`kf_customers`;

GRANT SELECT ON `agent_source_database`.`agent_customers` TO 'agent_snapshot'@'%';

GRANT SELECT (`account`, `created_at`, `customer_id`, `login_no`, `service`) ON `agent_source_database`.`kf_bindings` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_bindings` AS
SELECT
  `customer_id` AS `customer_id`,
  `service` AS `service`,
  `account` AS `account`,
  `login_no` AS `login_no`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`kf_bindings`;

GRANT SELECT ON `agent_source_database`.`agent_bindings` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `id`, `open_kfid`, `seq`, `status`) ON `agent_source_database`.`kf_messages` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_messages` AS
SELECT
  `seq` AS `seq`,
  `id` AS `id`,
  `open_kfid` AS `open_kfid`,
  `status` AS `status`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`kf_messages`;

GRANT SELECT ON `agent_source_database`.`agent_messages` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `customer_id`, `id`, `kind`, `status`, `updated_at`) ON `agent_source_database`.`kf_jobs` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_jobs` AS
SELECT
  `id` AS `id`,
  `customer_id` AS `customer_id`,
  `kind` AS `kind`,
  `status` AS `status`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`kf_jobs`;

GRANT SELECT ON `agent_source_database`.`agent_jobs` TO 'agent_snapshot'@'%';

GRANT SELECT (`attempts`, `available_at`, `created_at`, `customer_id`, `error_code`, `expires_at`, `id`, `seq`, `status`) ON `agent_source_database`.`kf_outbox` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_outbox` AS
SELECT
  `seq` AS `seq`,
  `id` AS `id`,
  `customer_id` AS `customer_id`,
  `status` AS `status`,
  `attempts` AS `attempts`,
  `error_code` AS `error_code`,
  `created_at` AS `created_at`,
  `available_at` AS `available_at`,
  `expires_at` AS `expires_at`
FROM `agent_source_database`.`kf_outbox`;

GRANT SELECT ON `agent_source_database`.`agent_outbox` TO 'agent_snapshot'@'%';

GRANT SELECT (`heartbeat`, `role`) ON `agent_source_database`.`kf_workers` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_workers` AS
SELECT
  `role` AS `role`,
  `heartbeat` AS `heartbeat_at`
FROM `agent_source_database`.`kf_workers`;

GRANT SELECT ON `agent_source_database`.`agent_workers` TO 'agent_snapshot'@'%';

GRANT SELECT (`delivery_count`, `event_digest`, `last_seen_at`, `processed_at`, `received_at`) ON `agent_source_database`.`wecom_callback_inbox` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_callback_events` AS
SELECT
  `event_digest` AS `event_digest`,
  `received_at` AS `received_at`,
  `last_seen_at` AS `last_seen_at`,
  `delivery_count` AS `delivery_count`,
  `processed_at` AS `processed_at`
FROM `agent_source_database`.`wecom_callback_inbox`;

GRANT SELECT ON `agent_source_database`.`agent_callback_events` TO 'agent_snapshot'@'%';
