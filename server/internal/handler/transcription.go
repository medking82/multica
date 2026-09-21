package handler

import (
	"bytes"
	"errors"
	"io"
	"log/slog"
	"mime"
	"net/http"
	"path"
	"strings"

	"github.com/multica-ai/multica/server/pkg/llm"
)

const (
	// The browser stops normal recordings at two minutes, and this independent
	// server bound protects the upstream key from direct API clients.
	maxTranscriptionAudioBytes = 10 * 1024 * 1024
	transcriptionFormOverhead  = 64 * 1024
)

var transcriptionContentTypes = map[string]string{
	"audio/flac":  ".flac",
	"audio/m4a":   ".m4a",
	"audio/mp4":   ".mp4",
	"audio/mpeg":  ".mp3",
	"audio/mp3":   ".mp3",
	"audio/ogg":   ".ogg",
	"audio/wav":   ".wav",
	"audio/webm":  ".webm",
	"audio/x-m4a": ".m4a",
	"audio/x-wav": ".wav",
}

var transcriptionExtensions = map[string]string{
	".flac": "audio/flac",
	".m4a":  "audio/m4a",
	".mp3":  "audio/mpeg",
	".mp4":  "audio/mp4",
	".mpeg": "audio/mpeg",
	".mpga": "audio/mpeg",
	".ogg":  "audio/ogg",
	".wav":  "audio/wav",
	".webm": "audio/webm",
}

// TranscribeAudio accepts one user-triggered recording and returns text. The
// audio is read into a bounded in-memory buffer only; it is never sent to the
// attachment store or database. The workspace membership and human-actor
// gates are applied by the router before this handler runs.
func (h *Handler) TranscribeAudio(w http.ResponseWriter, r *http.Request) {
	if h.LLM == nil || !h.LLM.TranscriptionEnabled() {
		writeError(w, http.StatusServiceUnavailable, "audio transcription is not configured")
		return
	}

	r.Body = http.MaxBytesReader(
		w,
		r.Body,
		maxTranscriptionAudioBytes+transcriptionFormOverhead,
	)
	reader, err := r.MultipartReader()
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid multipart recording")
		return
	}

	var recording []byte
	var filename, contentType string
	for {
		part, nextErr := reader.NextPart()
		if nextErr == io.EOF {
			break
		}
		if nextErr != nil {
			writeTranscriptionReadError(w, nextErr)
			return
		}
		if part.FormName() != "file" || part.FileName() == "" {
			_ = part.Close()
			continue
		}

		filename, contentType, err = normalizeTranscriptionMetadata(
			part.FileName(),
			part.Header.Get("Content-Type"),
		)
		if err != nil {
			_ = part.Close()
			writeError(w, http.StatusUnsupportedMediaType, err.Error())
			return
		}
		recording, err = io.ReadAll(io.LimitReader(part, maxTranscriptionAudioBytes+1))
		_ = part.Close()
		if err != nil {
			writeTranscriptionReadError(w, err)
			return
		}
		if len(recording) > maxTranscriptionAudioBytes {
			writeError(w, http.StatusRequestEntityTooLarge, "recording is too large")
			return
		}
		break
	}

	if len(recording) == 0 {
		writeError(w, http.StatusBadRequest, "a non-empty audio file is required")
		return
	}

	text, err := h.LLM.Transcribe(r.Context(), llm.AudioInput{
		Reader:      bytes.NewReader(recording),
		Filename:    filename,
		ContentType: contentType,
	})
	if err != nil {
		if errors.Is(err, llm.ErrNotConfigured) {
			writeError(w, http.StatusServiceUnavailable, "audio transcription is not configured")
			return
		}
		slog.Error("audio transcription failed", "error", err)
		writeError(w, http.StatusBadGateway, "audio transcription failed")
		return
	}
	if strings.TrimSpace(text) == "" {
		writeError(w, http.StatusUnprocessableEntity, "no speech detected")
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"text": text})
}

func writeTranscriptionReadError(w http.ResponseWriter, err error) {
	var tooLarge *http.MaxBytesError
	if errors.As(err, &tooLarge) {
		writeError(w, http.StatusRequestEntityTooLarge, "recording is too large")
		return
	}
	writeError(w, http.StatusBadRequest, "failed to read recording")
}

func normalizeTranscriptionMetadata(filename, rawContentType string) (string, string, error) {
	filename = path.Base(strings.ReplaceAll(strings.TrimSpace(filename), "\\", "/"))
	ext := strings.ToLower(path.Ext(filename))
	contentType, _, err := mime.ParseMediaType(rawContentType)
	if err != nil {
		contentType = ""
	}
	contentType = strings.ToLower(strings.TrimSpace(contentType))

	canonicalFromExtension, extensionOK := transcriptionExtensions[ext]
	preferredExtension, contentTypeOK := transcriptionContentTypes[contentType]
	if !extensionOK && !contentTypeOK {
		return "", "", errors.New("unsupported audio format")
	}
	if !contentTypeOK {
		contentType = canonicalFromExtension
	}
	if !extensionOK {
		if filename == "" {
			filename = "recording"
		}
		filename += preferredExtension
	}
	return filename, contentType, nil
}
