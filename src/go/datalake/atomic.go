package datalake

import (
	"os"
	"path/filepath"
)

func WriteAtomically(targetPath string, data []byte) error {
	parentDir := filepath.Dir(targetPath)
	if err := os.MkdirAll(parentDir, 0755); err != nil {
		return err
	}
	
	partPath := targetPath + ".path"

	file, err := os.OpenFile(partPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0644)
	if err != nil {
		return err
	}

	if _, err := file.Write(data); err != nil {
		file.Close()
		os.Remove(partPath)
		return err
	}

	if err := file.Sync(); err != nil {
		file.Close()
		os.Remove(partPath)
		return err
	}

	if err := file.Close(); err != nil {
		os.Remove(partPath)
		return err
	}

	if err := os.Rename(partPath, targetPath); err != nil {
		os.Remove(partPath)
		return err
	}

	dirFile, err := os.Open(parentDir)
	if err == nil {
		dirFile.Sync()
		dirFile.Close()
	}
	
	return nil
}
