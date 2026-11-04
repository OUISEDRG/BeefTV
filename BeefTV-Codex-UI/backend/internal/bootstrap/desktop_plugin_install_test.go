package bootstrap

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"reflect"
	"testing"
	"time"

	"infinite-canvas/backend/internal/agentops"
	"infinite-canvas/backend/internal/database"
	"infinite-canvas/backend/internal/model"
)

func TestDesktopPluginInstallPersistsAuditAfterMigration(t *testing.T) {
	for _, previousVersion := range []int{0, 28} {
		name := "fresh"
		if previousVersion != 0 {
			name = "upgrade-v28"
		}
		t.Run(name, func(t *testing.T) {
			cfg := Config{Profile: ProfileDesktop, DataDir: t.TempDir(), ListenAddr: "127.0.0.1:0", AutoMigrate: true, ShutdownTimeout: 5 * time.Second}
			type migration struct {
				Version   int64
				Name      string
				AppliedAt time.Time
			}
			var previous []migration
			if previousVersion != 0 {
				old, err := Open(context.Background(), cfg)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = old.Close(context.Background()) })
				// Reproduce the shipped v28 desktop structure in this temporary workspace.
				if err := old.db.Migrator().DropTable(&model.AdminAuditEvent{}); err != nil {
					t.Fatal(err)
				}
				if err := old.db.Exec("DELETE FROM local_schema_migrations WHERE version > ?", previousVersion).Error; err != nil {
					t.Fatal(err)
				}
				if err := old.db.Table("local_schema_migrations").Order("version").Find(&previous).Error; err != nil {
					t.Fatal(err)
				}
				if err := old.Close(context.Background()); err != nil {
					t.Fatal(err)
				}
			}

			runtime, err := Open(context.Background(), cfg)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = runtime.Close(context.Background()) })
			if err := runtime.Start(); err != nil {
				t.Fatal(err)
			}
			ownerToken, err := agentops.EnsureOwnerToken(cfg.DataDir)
			if err != nil {
				t.Fatal(err)
			}
			var body bytes.Buffer
			form := multipart.NewWriter(&body)
			file, err := form.CreateFormFile("file", "desktop-audit-fixture.beeftv-plugin")
			if err != nil {
				t.Fatal(err)
			}
			if _, err := file.Write(desktopAuditPluginPackage(t)); err != nil {
				t.Fatal(err)
			}
			if err := form.Close(); err != nil {
				t.Fatal(err)
			}
			request, err := http.NewRequest(http.MethodPost, runtime.BaseURL()+"/plugins", &body)
			if err != nil {
				t.Fatal(err)
			}
			request.Header.Set("Content-Type", form.FormDataContentType())
			request.Header.Set("X-Desktop-Token", runtime.LaunchToken())
			request.Header.Set("X-Beeftv-Owner", ownerToken)
			response, err := (&http.Client{Timeout: 10 * time.Second}).Do(request)
			if err != nil {
				t.Fatal(err)
			}
			responseBody, err := io.ReadAll(response.Body)
			_ = response.Body.Close()
			if err != nil {
				t.Fatal(err)
			}
			var envelope struct {
				Code int `json:"code"`
				Data struct {
					Plugin struct {
						Manifest struct{ ID string } `json:"manifest"`
						Status   string              `json:"status"`
					} `json:"plugin"`
				} `json:"data"`
			}
			if response.StatusCode != http.StatusOK || json.Unmarshal(responseBody, &envelope) != nil || envelope.Code != 0 || envelope.Data.Plugin.Manifest.ID != "desktop-audit-fixture" || envelope.Data.Plugin.Status != "enabled" {
				t.Fatalf("formal desktop plugin install: status=%d body=%s", response.StatusCode, responseBody)
			}
			var audits []model.AdminAuditEvent
			if err := runtime.db.Find(&audits).Error; err != nil {
				t.Fatal(err)
			}
			if len(audits) != 1 || audits[0].Action != "plugin.install" || audits[0].TargetType != "plugin" || audits[0].TargetID != "desktop-audit-fixture" || audits[0].ActorUserID == "" {
				t.Fatalf("plugin install audit = %#v", audits)
			}
			if err := database.MigrateLocalSchema(runtime.db); err != nil {
				t.Fatal(err)
			}
			var retained []model.AdminAuditEvent
			if err := runtime.db.Find(&retained).Error; err != nil || !reflect.DeepEqual(audits, retained) {
				t.Fatalf("repeated migration changed audit: %+v, %v", retained, err)
			}
			if previousVersion != 0 {
				var retained []migration
				if err := runtime.db.Table("local_schema_migrations").Where("version <= ?", previousVersion).Order("version").Find(&retained).Error; err != nil || !reflect.DeepEqual(previous, retained) {
					t.Fatalf("upgrade changed historical migration ledger: %+v, %v", retained, err)
				}
			}
		})
	}
}

func desktopAuditPluginPackage(t *testing.T) []byte {
	t.Helper()
	var data bytes.Buffer
	archive := zip.NewWriter(&data)
	manifest, err := archive.Create("manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.WriteString(manifest, `{"apiVersion":"beeftv.plugin/v1","id":"desktop-audit-fixture","version":"1.0.0","name":"Desktop Audit Fixture","author":"Test","documentation":"# Desktop Audit Fixture","contributes":{"providers":[{"id":"desktop-audit-fixture","label":"Desktop Audit Fixture","capabilities":["video"],"scopes":["canvas"],"create":{"method":"POST","path":"/tasks","fields":{"prompt":"request.prompt"}},"response":{"statusPaths":["status"]}}]}}`); err != nil {
		t.Fatal(err)
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}
