package main

import (
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"infinite-canvas/backend/internal/bootstrap"

	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

type OwnedMediaBatchEntry struct {
	Name       string `json:"name"`
	ResourceID string `json:"resourceId"`
}

type OwnedMediaBatchResult struct {
	Cancelled  bool `json:"cancelled"`
	Downloaded int  `json:"downloaded"`
	Failed     int  `json:"failed"`
}

var reservedWindowsMediaName = regexp.MustCompile(`(?i)^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]) *(?:\.|$)`)

func (a *DesktopApp) SaveOwnedMediaBatch(entries []OwnedMediaBatchEntry) (OwnedMediaBatchResult, error) {
	var result OwnedMediaBatchResult
	if len(entries) == 0 || len(entries) > 50_000 {
		return result, errors.New("导出文件数量无效")
	}
	names := make([]string, len(entries))
	for index, entry := range entries {
		name := entry.Name
		if name == "" || name != strings.TrimSpace(name) || name != strings.Trim(name, ".") || !utf8.ValidString(name) ||
			strings.ContainsAny(name, `\/:*?"<>|`) || strings.ContainsFunc(name, unicode.IsControl) {
			return result, errors.New("导出文件名无效")
		}
		names[index] = bootstrap.SanitizeSaveFileName(name)
		if reservedWindowsMediaName.MatchString(names[index]) {
			return result, errors.New("导出文件名无效")
		}
	}
	ctx, err := a.dialogContext()
	if err != nil {
		return result, err
	}
	a.mu.RLock()
	choose := a.chooseMediaDirectory
	a.mu.RUnlock()
	var path string
	if choose != nil {
		path, err = choose(ctx)
	} else {
		path, err = wailsruntime.OpenDirectoryDialog(ctx, wailsruntime.OpenDialogOptions{Title: "选择素材下载文件夹", CanCreateDirectories: true})
	}
	if err != nil {
		return result, err
	}
	if path == "" {
		result.Cancelled = true
		return result, nil
	}
	info, err := os.Stat(path)
	if err != nil || !info.IsDir() || !filepath.IsAbs(path) {
		return result, errors.New("无法写入所选文件夹")
	}
	runtime := a.runtime()
	if runtime == nil {
		return result, errors.New("本地后端尚未就绪")
	}
	for index, entry := range entries {
		if ctx.Err() != nil {
			result.Failed += len(entries) - index
			break
		}
		dest := filepath.Join(path, names[index])
		if err := runtime.CopyOwnedResourceToNewFile(entry.ResourceID, dest); err != nil {
			result.Failed++
		} else {
			result.Downloaded++
		}
	}
	return result, nil
}
