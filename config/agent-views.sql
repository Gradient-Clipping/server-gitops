-- Provision both accounts in the trusted deployment first; lock agent_view_owner.

-- agent_snapshot must have no existing broad grants or active roles.

USE `agent_source_database`;

GRANT SELECT (`account_active`, `created_at`, `display_name`, `id`, `last_active_at`, `last_login_at`, `profile_json`, `swu_account`, `updated_at`) ON `agent_source_database`.`users` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_users` AS
SELECT
  `id` AS `id`,
  `swu_account` AS `swu_account`,
  `display_name` AS `display_name`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`,
  `last_login_at` AS `last_login_at`,
  `last_active_at` AS `last_active_at`,
  `account_active` AS `account_active`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.name')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.name')) ELSE NULL END AS `name`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.gender')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.gender')) ELSE NULL END AS `gender`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.grade')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.grade')) ELSE NULL END AS `grade`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.grade')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.grade')) ELSE NULL END AS `cohort_year`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.majorName')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.majorName')) ELSE NULL END AS `major_name`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.organizationName')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.organizationName')) ELSE NULL END AS `college_name`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.collegeName')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.collegeName')) ELSE NULL END AS `college_name_alternate`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.className')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.className')) ELSE NULL END AS `class_name`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.studentType')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.studentType')) ELSE NULL END AS `student_type`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.studentStatus')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.studentStatus')) ELSE NULL END AS `student_status`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.enrollmentDate')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.enrollmentDate')) ELSE NULL END AS `enrollment_date`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.programLength')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.programLength')) ELSE NULL END AS `program_length`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.nationality')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.nationality')) ELSE NULL END AS `nationality`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.studentOrigin')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.studentOrigin')) ELSE NULL END AS `student_origin`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.phone')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.phone')) ELSE NULL END AS `phone`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`profile_json`, '$.registeredPhone')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`profile_json`, '$.registeredPhone')) ELSE NULL END AS `registered_phone`
FROM `agent_source_database`.`users`;

GRANT SELECT ON `agent_source_database`.`agent_users` TO 'agent_snapshot'@'%';

GRANT SELECT (`action`, `actor`, `created_at`, `id`, `target_id`, `target_type`) ON `agent_source_database`.`admin_audit_logs` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_admin_audit_logs` AS
SELECT
  `id` AS `id`,
  `actor` AS `actor`,
  `action` AS `action`,
  `target_type` AS `target_type`,
  `target_id` AS `target_id`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`admin_audit_logs`;

GRANT SELECT ON `agent_source_database`.`agent_admin_audit_logs` TO 'agent_snapshot'@'%';

GRANT SELECT (`amount_cents`, `created_at`, `credited_at`, `grant_seconds`, `grant_uses`, `id`, `paid_amount_cents`, `paid_at`, `payment_channel`, `plan_code`, `plan_name`, `provider`, `quota_amount`, `quota_unit`, `refunded_cents`, `status`, `updated_at`, `user_id`) ON `agent_source_database`.`auto_dorm_check_orders` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_dorm_orders` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `plan_code` AS `plan_code`,
  `plan_name` AS `plan_name`,
  `quota_unit` AS `quota_unit`,
  `quota_amount` AS `quota_amount`,
  `amount_cents` AS `amount_cents`,
  `paid_amount_cents` AS `paid_amount_cents`,
  `refunded_cents` AS `refunded_cents`,
  `grant_uses` AS `grant_uses`,
  `grant_seconds` AS `grant_seconds`,
  `status` AS `status`,
  `provider` AS `provider`,
  `payment_channel` AS `payment_channel`,
  `paid_at` AS `paid_at`,
  `credited_at` AS `credited_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`auto_dorm_check_orders`;

GRANT SELECT ON `agent_source_database`.`agent_dorm_orders` TO 'agent_snapshot'@'%';

GRANT SELECT (`amount_cents`, `created_at`, `id`, `last_error`, `order_id`, `quota_amount`, `status`, `succeeded_at`, `updated_at`) ON `agent_source_database`.`auto_dorm_check_refunds` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_dorm_refunds` AS
SELECT
  `id` AS `id`,
  `order_id` AS `order_id`,
  `status` AS `status`,
  `amount_cents` AS `amount_cents`,
  `quota_amount` AS `quota_amount`,
  `last_error` AS `last_error`,
  `succeeded_at` AS `succeeded_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`auto_dorm_check_refunds`;

GRANT SELECT ON `agent_source_database`.`agent_dorm_refunds` TO 'agent_snapshot'@'%';

GRANT SELECT (`completed_at`, `created_at`, `execution_origin`, `failure_code`, `id`, `retry_at`, `retry_count`, `scheduled_at`, `source_timezone`, `started_at`, `status`, `updated_at`, `user_id`) ON `agent_source_database`.`auto_dorm_check_tasks` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_dorm_tasks` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `status` AS `status`,
  `scheduled_at` AS `scheduled_at`,
  `source_timezone` AS `source_timezone`,
  `started_at` AS `started_at`,
  `completed_at` AS `completed_at`,
  `failure_code` AS `failure_code`,
  `retry_count` AS `retry_count`,
  `retry_at` AS `retry_at`,
  `execution_origin` AS `execution_origin`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`auto_dorm_check_tasks`;

GRANT SELECT ON `agent_source_database`.`agent_dorm_tasks` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `remaining_seconds`, `remaining_uses`, `time_settled_at`, `updated_at`, `user_id`) ON `agent_source_database`.`auto_dorm_check_wallets` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_dorm_wallets` AS
SELECT
  `user_id` AS `user_id`,
  `remaining_uses` AS `remaining_uses`,
  `remaining_seconds` AS `remaining_seconds`,
  `time_settled_at` AS `time_settled_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`auto_dorm_check_wallets`;

GRANT SELECT ON `agent_source_database`.`agent_dorm_wallets` TO 'agent_snapshot'@'%';

GRANT SELECT (`enabled`, `enabled_at`, `updated_at`, `user_id`) ON `agent_source_database`.`auto_dorm_check_preferences` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_dorm_preferences` AS
SELECT
  `user_id` AS `user_id`,
  `enabled` AS `enabled`,
  `enabled_at` AS `enabled_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`auto_dorm_check_preferences`;

GRANT SELECT ON `agent_source_database`.`agent_dorm_preferences` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `id`, `resolved`, `resolved_at`, `resolved_by`, `type`, `updated_at`, `user_id`) ON `agent_source_database`.`user_feedback` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_feedback` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `type` AS `type`,
  `resolved` AS `resolved`,
  `resolved_by` AS `resolved_by`,
  `resolved_at` AS `resolved_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`user_feedback`;

GRANT SELECT ON `agent_source_database`.`agent_feedback` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `created_by`, `deleted_at`, `expires_at`, `id`, `kind`, `reminder_mode`, `starts_at`, `target_scope`, `title`, `updated_at`, `visibility_mode`) ON `agent_source_database`.`publications` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_publications` AS
SELECT
  `id` AS `id`,
  `kind` AS `kind`,
  `title` AS `title`,
  `visibility_mode` AS `visibility_mode`,
  `reminder_mode` AS `reminder_mode`,
  `target_scope` AS `target_scope`,
  `starts_at` AS `starts_at`,
  `expires_at` AS `expires_at`,
  `created_by` AS `created_by`,
  `deleted_at` AS `deleted_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`publications`;

GRANT SELECT ON `agent_source_database`.`agent_publications` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `first_seen_at`, `last_popup_at`, `popup_count`, `publication_id`, `read_at`, `updated_at`, `user_id`) ON `agent_source_database`.`publication_receipts` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_publication_receipts` AS
SELECT
  `publication_id` AS `publication_id`,
  `user_id` AS `user_id`,
  `first_seen_at` AS `first_seen_at`,
  `read_at` AS `read_at`,
  `popup_count` AS `popup_count`,
  `last_popup_at` AS `last_popup_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`publication_receipts`;

GRANT SELECT ON `agent_source_database`.`agent_publication_receipts` TO 'agent_snapshot'@'%';

GRANT SELECT (`attempt_key`, `calculation_score`, `course_key`, `course_name`, `course_type`, `display_name`, `grade_json`, `reviewable`, `teacher_name`, `user_id`) ON `agent_source_database`.`course_assistant_grade_index` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_assistant_grades` AS
SELECT
  `user_id` AS `user_id`,
  `attempt_key` AS `attempt_key`,
  `course_key` AS `course_key`,
  `course_type` AS `course_type`,
  `display_name` AS `display_name`,
  `course_name` AS `course_name`,
  `teacher_name` AS `teacher_name`,
  `calculation_score` AS `calculation_score`,
  `reviewable` AS `reviewable`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.courseNature')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.courseNature')) ELSE NULL END AS `course_nature`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.sportName')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.sportName')) ELSE NULL END AS `sport_name`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.academicYear')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.academicYear')) ELSE NULL END AS `academic_year`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.academicYearStart')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.academicYearStart')) ELSE NULL END AS `academic_year_start`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.term')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.term')) ELSE NULL END AS `term`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.cohortYear')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.cohortYear')) ELSE NULL END AS `cohort_year`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.finalScore')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.finalScore')) ELSE NULL END AS `final_score`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.credits')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.credits')) ELSE NULL END AS `credits`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.sourceFetchedAt')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.sourceFetchedAt')) ELSE NULL END AS `source_fetched_at`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`grade_json`, '$.courseOwnership')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`grade_json`, '$.courseOwnership')) ELSE NULL END AS `course_ownership`
FROM `agent_source_database`.`course_assistant_grade_index`;

GRANT SELECT ON `agent_source_database`.`agent_course_assistant_grades` TO 'agent_snapshot'@'%';

GRANT SELECT (`academic_year`, `academic_year_start`, `active`, `assistant_course_key`, `calculation_score`, `content`, `course_key`, `course_name`, `course_type`, `created_at`, `deactivated_at`, `display_name`, `grade_attempt_key`, `id`, `keywords_json`, `rating`, `teacher_name`, `teacher_names_json`, `term`, `updated_at`, `user_id`) ON `agent_source_database`.`course_assistant_reviews` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_assistant_reviews` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `course_key` AS `course_key`,
  `assistant_course_key` AS `assistant_course_key`,
  `grade_attempt_key` AS `grade_attempt_key`,
  `course_type` AS `course_type`,
  `course_name` AS `course_name`,
  `display_name` AS `display_name`,
  `academic_year` AS `academic_year`,
  `academic_year_start` AS `academic_year_start`,
  `term` AS `term`,
  `calculation_score` AS `calculation_score`,
  `teacher_name` AS `teacher_name`,
  `rating` AS `rating`,
  `content` AS `content`,
  `active` AS `active`,
  `deactivated_at` AS `deactivated_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`,
  `teacher_names_json` AS `teacher_names`,
  `keywords_json` AS `keywords`
FROM `agent_source_database`.`course_assistant_reviews`;

GRANT SELECT ON `agent_source_database`.`agent_course_assistant_reviews` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `review_id`, `user_id`) ON `agent_source_database`.`course_assistant_review_likes` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_assistant_review_likes` AS
SELECT
  `review_id` AS `review_id`,
  `user_id` AS `user_id`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`course_assistant_review_likes`;

GRANT SELECT ON `agent_source_database`.`agent_course_assistant_review_likes` TO 'agent_snapshot'@'%';

GRANT SELECT (`amount_cents`, `created_at`, `credited_at`, `id`, `paid_amount_cents`, `paid_at`, `payment_channel`, `payment_env`, `plan_code`, `plan_name`, `provider`, `quota_amount`, `quota_unit`, `refunded_cents`, `status`, `updated_at`, `user_id`) ON `agent_source_database`.`course_grab_orders` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_grab_orders` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `plan_code` AS `plan_code`,
  `plan_name` AS `plan_name`,
  `quota_unit` AS `quota_unit`,
  `quota_amount` AS `quota_amount`,
  `amount_cents` AS `amount_cents`,
  `paid_amount_cents` AS `paid_amount_cents`,
  `refunded_cents` AS `refunded_cents`,
  `status` AS `status`,
  `provider` AS `provider`,
  `payment_channel` AS `payment_channel`,
  `payment_env` AS `payment_env`,
  `paid_at` AS `paid_at`,
  `credited_at` AS `credited_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`course_grab_orders`;

GRANT SELECT ON `agent_source_database`.`agent_course_grab_orders` TO 'agent_snapshot'@'%';

GRANT SELECT (`amount_cents`, `created_at`, `id`, `last_error`, `order_id`, `quota_amount`, `status`, `succeeded_at`, `updated_at`) ON `agent_source_database`.`course_grab_refunds` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_grab_refunds` AS
SELECT
  `id` AS `id`,
  `order_id` AS `order_id`,
  `status` AS `status`,
  `amount_cents` AS `amount_cents`,
  `quota_amount` AS `quota_amount`,
  `last_error` AS `last_error`,
  `succeeded_at` AS `succeeded_at`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`course_grab_refunds`;

GRANT SELECT ON `agent_source_database`.`agent_course_grab_refunds` TO 'agent_snapshot'@'%';

GRANT SELECT (`enabled`, `id`, `updated_at`, `version`) ON `agent_source_database`.`course_grab_settings` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_grab_settings` AS
SELECT
  `id` AS `id`,
  `enabled` AS `enabled`,
  `version` AS `version`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`course_grab_settings`;

GRANT SELECT ON `agent_source_database`.`agent_course_grab_settings` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `enabled`, `generation`, `id`, `negative_keywords`, `positive_keywords`, `preferred_enabled`, `result_code`, `scheduled_at`, `search_keywords`, `selected_course`, `source_timezone`, `state`, `updated_at`, `user_id`) ON `agent_source_database`.`course_grab_tasks` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_grab_tasks` AS
SELECT
  `id` AS `id`,
  `user_id` AS `user_id`,
  `scheduled_at` AS `scheduled_at`,
  `source_timezone` AS `source_timezone`,
  `preferred_enabled` AS `preferred_enabled`,
  `enabled` AS `enabled`,
  `state` AS `state`,
  `result_code` AS `result_code`,
  `generation` AS `generation`,
  `created_at` AS `created_at`,
  `updated_at` AS `updated_at`,
  `search_keywords` AS `search_keywords`,
  `positive_keywords` AS `positive_keywords`,
  `negative_keywords` AS `negative_keywords`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`selected_course`, '$.sectionId')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`selected_course`, '$.sectionId')) ELSE NULL END AS `selected_section_id`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`selected_course`, '$.courseName')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`selected_course`, '$.courseName')) ELSE NULL END AS `selected_course_name`
FROM `agent_source_database`.`course_grab_tasks`;

GRANT SELECT ON `agent_source_database`.`agent_course_grab_tasks` TO 'agent_snapshot'@'%';

GRANT SELECT (`id`, `order_id`, `source_change_id`, `state`, `task_id`, `updated_at`, `user_id`) ON `agent_source_database`.`course_grab_units` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_grab_units` AS
SELECT
  `id` AS `id`,
  `order_id` AS `order_id`,
  `user_id` AS `user_id`,
  `state` AS `state`,
  `task_id` AS `task_id`,
  `source_change_id` AS `source_change_id`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`course_grab_units`;

GRANT SELECT ON `agent_source_database`.`agent_course_grab_units` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `created_by`, `id`, `input_json`, `kind`, `result_json`) ON `agent_source_database`.`course_grab_quota_changes` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_grab_quota_changes` AS
SELECT
  `id` AS `id`,
  `kind` AS `kind`,
  `created_by` AS `created_by`,
  `created_at` AS `created_at`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`input_json`, '$.operation')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`input_json`, '$.operation')) ELSE NULL END AS `operation`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`input_json`, '$.quotaAmount')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`input_json`, '$.quotaAmount')) ELSE NULL END AS `quota_amount`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`result_json`, '$.recipientCount')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`result_json`, '$.recipientCount')) ELSE NULL END AS `recipient_count`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`result_json`, '$.totalUses')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`result_json`, '$.totalUses')) ELSE NULL END AS `total_uses`
FROM `agent_source_database`.`course_grab_quota_changes`;

GRANT SELECT ON `agent_source_database`.`agent_course_grab_quota_changes` TO 'agent_snapshot'@'%';

GRANT SELECT (`change_id`, `delta`, `user_id`) ON `agent_source_database`.`course_grab_quota_change_users` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_course_grab_quota_change_users` AS
SELECT
  `change_id` AS `change_id`,
  `user_id` AS `user_id`,
  `delta` AS `delta`
FROM `agent_source_database`.`course_grab_quota_change_users`;

GRANT SELECT ON `agent_source_database`.`agent_course_grab_quota_change_users` TO 'agent_snapshot'@'%';

GRANT SELECT (`color`, `enabled`, `enhanced`, `selected`, `shape`, `skipped`, `updated_at`, `user_id`) ON `agent_source_database`.`user_companion_preferences` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_user_companion_preferences` AS
SELECT
  `user_id` AS `user_id`,
  `selected` AS `selected`,
  `skipped` AS `skipped`,
  `enabled` AS `enabled`,
  `enhanced` AS `enhanced`,
  `shape` AS `shape`,
  `color` AS `color`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`user_companion_preferences`;

GRANT SELECT ON `agent_source_database`.`agent_user_companion_preferences` TO 'agent_snapshot'@'%';

GRANT SELECT (`expires_at`, `last_manual_day`, `user_id`) ON `agent_source_database`.`timetable_companion_codes` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_timetable_companion_codes` AS
SELECT
  `user_id` AS `user_id`,
  `expires_at` AS `expires_at`,
  `last_manual_day` AS `last_manual_day`
FROM `agent_source_database`.`timetable_companion_codes`;

GRANT SELECT ON `agent_source_database`.`agent_timetable_companion_codes` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `id`, `recipient_id`, `sender_id`) ON `agent_source_database`.`timetable_companion_requests` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_timetable_companion_requests` AS
SELECT
  `id` AS `id`,
  `sender_id` AS `sender_id`,
  `recipient_id` AS `recipient_id`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`timetable_companion_requests`;

GRANT SELECT ON `agent_source_database`.`agent_timetable_companion_requests` TO 'agent_snapshot'@'%';

GRANT SELECT (`created_at`, `user_high`, `user_low`) ON `agent_source_database`.`timetable_companions` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_timetable_companions` AS
SELECT
  `user_low` AS `user_low`,
  `user_high` AS `user_high`,
  `created_at` AS `created_at`
FROM `agent_source_database`.`timetable_companions`;

GRANT SELECT ON `agent_source_database`.`agent_timetable_companions` TO 'agent_snapshot'@'%';

GRANT SELECT (`source_key`, `user_id`, `viewed_at`) ON `agent_source_database`.`school_notice_views` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_school_notice_views` AS
SELECT
  `source_key` AS `source_key`,
  `user_id` AS `user_id`,
  `viewed_at` AS `viewed_at`
FROM `agent_source_database`.`school_notice_views`;

GRANT SELECT ON `agent_source_database`.`agent_school_notice_views` TO 'agent_snapshot'@'%';

GRANT SELECT (`cache_key`, `fetched_at`, `payload_json`, `updated_at`) ON `agent_source_database`.`school_notice_cache` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_school_notice_cache` AS
SELECT
  `cache_key` AS `cache_key`,
  `payload_json` AS `payload_json`,
  `fetched_at` AS `fetched_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`school_notice_cache`;

GRANT SELECT ON `agent_source_database`.`agent_school_notice_cache` TO 'agent_snapshot'@'%';

GRANT SELECT (`content_html`, `fetched_at`, `last_accessed_at`, `published_at`, `publisher`, `schema_version`, `source_id`, `source_url`, `title`, `updated_at`) ON `agent_source_database`.`school_notice_content_cache` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_school_notice_content_cache` AS
SELECT
  `source_id` AS `source_id`,
  `source_url` AS `source_url`,
  `schema_version` AS `schema_version`,
  `title` AS `title`,
  `publisher` AS `publisher`,
  `published_at` AS `published_at`,
  `content_html` AS `content_html`,
  `fetched_at` AS `fetched_at`,
  `last_accessed_at` AS `last_accessed_at`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`school_notice_content_cache`;

GRANT SELECT ON `agent_source_database`.`agent_school_notice_content_cache` TO 'agent_snapshot'@'%';

GRANT SELECT (`deleted_at`, `fetched_at`, `payload_bytes`, `payload_json`, `resource_key`, `revision`, `schema_version`, `updated_at`, `user_id`) ON `agent_source_database`.`user_data_snapshots` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_schedule_snapshots` AS
SELECT
  `user_id` AS `user_id`,
  `resource_key` AS `resource_key`,
  `schema_version` AS `schema_version`,
  `payload_json` AS `payload_json`,
  `fetched_at` AS `fetched_at`,
  `revision` AS `revision`,
  `deleted_at` AS `deleted_at`,
  `payload_bytes` AS `payload_bytes`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`user_data_snapshots` WHERE `resource_key` = 'schedule';

GRANT SELECT ON `agent_source_database`.`agent_schedule_snapshots` TO 'agent_snapshot'@'%';

GRANT SELECT (`deleted_at`, `fetched_at`, `payload_bytes`, `payload_json`, `resource_key`, `revision`, `schema_version`, `updated_at`, `user_id`) ON `agent_source_database`.`user_data_snapshots` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_timetable_snapshots` AS
SELECT
  `user_id` AS `user_id`,
  `resource_key` AS `resource_key`,
  `schema_version` AS `schema_version`,
  `payload_json` AS `payload_json`,
  `fetched_at` AS `fetched_at`,
  `revision` AS `revision`,
  `deleted_at` AS `deleted_at`,
  `payload_bytes` AS `payload_bytes`,
  `updated_at` AS `updated_at`
FROM `agent_source_database`.`user_data_snapshots` WHERE `resource_key` LIKE 'timetable:%';

GRANT SELECT ON `agent_source_database`.`agent_timetable_snapshots` TO 'agent_snapshot'@'%';

GRANT SELECT (`buyer_id`, `created_at`, `document`, `external_order_no`, `id`, `platform_code`) ON `agent_source_database`.`service_orders` TO 'agent_view_owner'@'localhost';

CREATE OR REPLACE ALGORITHM=UNDEFINED DEFINER='agent_view_owner'@'localhost' SQL SECURITY DEFINER VIEW `agent_service_orders` AS
SELECT
  `id` AS `id`,
  `platform_code` AS `platform_code`,
  `external_order_no` AS `external_order_no`,
  `buyer_id` AS `buyer_id`,
  `created_at` AS `created_at`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.platform_name')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.platform_name')) ELSE NULL END AS `platform_name`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.payment_status')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.payment_status')) ELSE NULL END AS `payment_status`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.paid_at')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.paid_at')) ELSE NULL END AS `paid_at`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.payment_channel')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.payment_channel')) ELSE NULL END AS `payment_channel`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.amount_fen')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.amount_fen')) ELSE NULL END AS `amount_fen`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.service_units')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.service_units')) ELSE NULL END AS `service_units`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.billable_units')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.billable_units')) ELSE NULL END AS `billable_units`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.version')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.version')) ELSE NULL END AS `version`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.customer_ref')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.customer_ref')) ELSE NULL END AS `customer_ref`,
  CASE WHEN JSON_TYPE(JSON_EXTRACT(`document`, '$.selection_version')) IN ('STRING','INTEGER','DOUBLE','BOOLEAN') THEN JSON_UNQUOTE(JSON_EXTRACT(`document`, '$.selection_version')) ELSE NULL END AS `selection_version`,
  JSON_EXTRACT(`document`, '$.service_items') AS `service_items_json`,
  JSON_EXTRACT(`document`, '$.refunds') AS `refunds_json`,
  JSON_EXTRACT(`document`, '$.fulfillment') AS `fulfillment_json`
FROM `agent_source_database`.`service_orders`;

GRANT SELECT ON `agent_source_database`.`agent_service_orders` TO 'agent_snapshot'@'%';
