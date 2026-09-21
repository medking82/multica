package handler

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"strings"
	"testing"

	"github.com/multica-ai/multica/server/pkg/llm"
)

func transcriptionRequest(t *testing.T, filename, contentType string, body []byte) *http.Request {
	t.Helper()
	var payload bytes.Buffer
	form := multipart.NewWriter(&payload)
	header := make(textproto.MIMEHeader)
	header.Set("Content-Disposition", fmt.Sprintf(`form-data; name="file"; filename="%s"`, filename))
	header.Set("Content-Type", contentType)
	part, err := form.CreatePart(header)
	if err != nil {
		t.Fatalf("CreatePart: %v", err)
	}
	if _, err := part.Write(body); err != nil {
		t.Fatalf("write audio: %v", err)
	}
	if err := form.Close(); err != nil {
		t.Fatalf("close multipart: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/transcriptions", &payload)
	req.Header.Set("Content-Type", form.FormDataContentType())
	return req
}

func TestTranscribeAudioForwardsAcceptedRecordingWithoutPersistingIt(t *testing.T) {
	var upstreamAudio string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reader, err := r.MultipartReader()
		if err != nil {
			t.Fatalf("upstream MultipartReader: %v", err)
		}
		for {
			part, err := reader.NextPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				t.Fatalf("upstream NextPart: %v", err)
			}
			if part.FormName() == "file" {
				data, _ := io.ReadAll(part)
				upstreamAudio = string(data)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"text":"Ship the review."}`)
	}))
	t.Cleanup(upstream.Close)

	retries, err := llm.Retries(0)
	if err != nil {
		t.Fatalf("Retries: %v", err)
	}
	h := &Handler{LLM: llm.New(llm.Config{
		APIKey:             "test",
		BaseURL:            upstream.URL,
		TranscriptionModel: "gpt-4o-mini-transcribe",
		MaxRetries:         retries,
	})}
	w := httptest.NewRecorder()
	h.TranscribeAudio(w, transcriptionRequest(t, "recording.webm", "audio/webm;codecs=opus", []byte("voice-bytes")))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
	}
	var response struct {
		Text string `json:"text"`
	}
	if err := json.NewDecoder(w.Body).Decode(&response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if response.Text != "Ship the review." || upstreamAudio != "voice-bytes" {
		t.Fatalf("response = %q, upstream audio = %q", response.Text, upstreamAudio)
	}
}

func TestTranscribeAudioFailsClosedWhenNotConfigured(t *testing.T) {
	h := &Handler{LLM: llm.New(llm.Config{APIKey: "test"})}
	w := httptest.NewRecorder()
	h.TranscribeAudio(w, transcriptionRequest(t, "recording.webm", "audio/webm", []byte("voice")))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", w.Code)
	}
}

func TestTranscribeAudioRejectsUnsupportedAndOversizedAudio(t *testing.T) {
	h := &Handler{LLM: llm.New(llm.Config{
		APIKey:             "test",
		TranscriptionModel: "gpt-4o-mini-transcribe",
	})}

	t.Run("unsupported", func(t *testing.T) {
		w := httptest.NewRecorder()
		h.TranscribeAudio(w, transcriptionRequest(t, "recording.txt", "text/plain", []byte("not audio")))
		if w.Code != http.StatusUnsupportedMediaType {
			t.Fatalf("status = %d, want 415", w.Code)
		}
	})

	t.Run("oversized", func(t *testing.T) {
		w := httptest.NewRecorder()
		h.TranscribeAudio(w, transcriptionRequest(t, "recording.webm", "audio/webm", bytes.Repeat([]byte("x"), maxTranscriptionAudioBytes+1)))
		if w.Code != http.StatusRequestEntityTooLarge {
			t.Fatalf("status = %d, want 413; body = %s", w.Code, strings.TrimSpace(w.Body.String()))
		}
	})
}
