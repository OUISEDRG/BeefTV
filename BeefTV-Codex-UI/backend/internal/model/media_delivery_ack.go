package model

import "time"

// MediaDeliveryAck records a stable wire payload after durable ingestion and binding.
// It never contains a credential, upstream media URL, or image bytes.
type MediaDeliveryAck struct {
	TaskID         string    `gorm:"primaryKey;size:36"`
	OutputIndex    int       `gorm:"primaryKey;autoIncrement:false"`
	PayloadJSON    string    `gorm:"type:text;not null"`
	IdempotencyKey string    `gorm:"size:80;not null"`
	NextAttemptAt  time.Time `gorm:"index:idx_media_ack_due,priority:2"`
	Attempts       int
	LastError      string     `gorm:"size:48"`
	ConfirmedAt    *time.Time `gorm:"index:idx_media_ack_due,priority:1"`
}
