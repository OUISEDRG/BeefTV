package repository

import (
	"time"

	"infinite-canvas/backend/internal/model"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func (r *Repository) MediaDeliveryAckIndexes(taskID string) ([]int, error) {
	var indexes []int
	err := r.db.Model(&model.MediaDeliveryAck{}).Where("task_id = ?", taskID).Pluck("output_index", &indexes).Error
	return indexes, err
}

func (r *Repository) EnqueueMediaDeliveryAck(row model.MediaDeliveryAck) error {
	return r.db.Clauses(clause.OnConflict{DoNothing: true}).Create(&row).Error
}

func (r *Repository) DueMediaDeliveryAcks(now time.Time, limit int) ([]model.MediaDeliveryAck, error) {
	var rows []model.MediaDeliveryAck
	err := r.db.Where("confirmed_at IS NULL AND next_attempt_at <= ?", now).
		Order("next_attempt_at, task_id, output_index").Limit(limit).Find(&rows).Error
	return rows, err
}

func (r *Repository) ClaimMediaDeliveryAck(row model.MediaDeliveryAck, now, next time.Time) (bool, error) {
	update := r.db.Model(&model.MediaDeliveryAck{}).
		Where("task_id = ? AND output_index = ? AND confirmed_at IS NULL AND next_attempt_at <= ? AND attempts = ?",
			row.TaskID, row.OutputIndex, now, row.Attempts).
		Updates(map[string]any{"next_attempt_at": next, "attempts": gorm.Expr("attempts + 1")})
	return update.RowsAffected == 1, update.Error
}

func (r *Repository) FinishMediaDeliveryAck(row model.MediaDeliveryAck, reason string, confirmed *time.Time) error {
	return r.db.Model(&model.MediaDeliveryAck{}).
		Where("task_id = ? AND output_index = ? AND confirmed_at IS NULL AND attempts = ?", row.TaskID, row.OutputIndex, row.Attempts+1).
		Updates(map[string]any{"confirmed_at": confirmed, "last_error": reason}).Error
}

func (r *Repository) MediaBindingReceipt(userID, opID, op string) (string, error) {
	var row model.AgentOpRecord
	err := r.db.Where("user_id = ? AND op_id = ? AND op = ? AND status = ?", userID, opID, op, "succeeded").Take(&row).Error
	return row.ResultJSON, err
}

// HasRegisteredWorkflowOutput checks the saved result, beyond the task link
// that can be created during admission. Shot output is stored per resource.
func (r *Repository) HasRegisteredWorkflowOutput(task model.Task, stepID, resourceID string) (bool, error) {
	var count int64
	err := r.db.Table("production_task_links AS link").
		Joins("JOIN workflow_step_instances AS step ON step.id = link.workflow_step_id").
		Joins("JOIN workflow_instances AS workflow ON workflow.id = step.workflow_instance_id AND workflow.project_id = link.project_id").
		Joins("JOIN projects AS project ON project.id = link.project_id").
		Joins("JOIN workflow_step_tasks AS task_link ON task_link.workflow_step_id = step.id AND task_link.task_id = link.task_id").
		Where("link.task_id = ? AND step.id = ? AND project.user_id = ? AND project.status <> ?",
			task.ID, stepID, task.UserID, model.ProjectStatusArchived).
		Where(`(COALESCE(link.shot_id, '') = '' AND step.output_json = ?) OR EXISTS (
			SELECT 1 FROM shot_artifacts AS artifact
			WHERE artifact.project_id = link.project_id AND artifact.shot_id = link.shot_id
			  AND artifact.task_id = link.task_id AND artifact.type = link.artifact_type AND artifact.resource_id = ?
		)`, task.ResultJSON, resourceID).
		Count(&count).Error
	return count > 0, err
}
