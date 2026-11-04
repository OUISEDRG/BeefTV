package repository

import (
	"errors"
	"infinite-canvas/backend/internal/model"
	"testing"
)

func TestCanvasPictureVoiceSampleProtectedAtDeletionCommit(t *testing.T) {
	for _, operation := range []string{"asset", "detached"} {
		t.Run(operation, func(t *testing.T) {
			repo, db := newAdmissionRepo(t)
			pool, poolErr := db.DB()
			if poolErr != nil {
				t.Fatal(poolErr)
			}
			t.Cleanup(func() { _ = pool.Close() })
			if err := db.AutoMigrate(&model.CanvasWorkflowResource{}, &model.ProjectAssetLink{}, &model.ProjectAssetCandidate{}, &model.ShotAssetReference{}, &model.AssetVersion{}); err != nil {
				t.Fatal(err)
			}
			sample := seedReadyResource(t, db, "voice-sample", "owner")
			asset := model.Asset{ID: "audio-asset", UserID: "owner"}
			if err := db.Create(&asset).Error; err != nil {
				t.Fatal(err)
			}
			canvas := model.CanvasProject{ID: "picture-canvas", UserID: "owner", PayloadJSON: `{"nodes":[{"id":"picture","type":"image","metadata":{"dreamaVoiceBinding":{"version":1,"source":"resource","name":"voice","sampleResourceId":"voice-sample"}}}]}`}
			if err := db.Create(&canvas).Error; err != nil {
				t.Fatal(err)
			}
			var err error
			if operation == "asset" {
				err = repo.DeleteAssetAndResources("owner", asset.ID, []string{sample.ID}, nil)
			} else {
				err = repo.DeleteDetachedResources([]model.Resource{sample}, nil)
			}
			if !errors.Is(err, ErrResourceCleanupStillReferenced) {
				t.Fatalf("bound voice sample deletion was not blocked: %v", err)
			}
			var count int64
			if err := db.Model(&model.Resource{}).Where("id = ?", sample.ID).Count(&count).Error; err != nil || count != 1 {
				t.Fatal("bound sample not preserved")
			}
		})
	}
}
