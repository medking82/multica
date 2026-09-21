"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { LoaderCircle, Mic, Square } from "lucide-react";
import { toast } from "sonner";
import { ApiError, api } from "@multica/core/api";
import { useConfigStore } from "@multica/core/config";
import { useDictationAdapter } from "@multica/core/platform/dictation";
import { Button } from "@multica/ui/components/ui/button";
import { cn } from "@multica/ui/lib/utils";
import { useT } from "../i18n";
import type { ContentEditorRef } from "./content-editor";
import { NativeDictationButton } from "./native-dictation-button";

const MAX_RECORDING_MS = 2 * 60 * 1000;

const MIME_TYPE_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
] as const;

type VoiceInputState = "idle" | "requesting" | "recording" | "transcribing";

interface VoiceInputButtonProps {
  editorRef: RefObject<ContentEditorRef | null>;
  disabled?: boolean;
  className?: string;
  size?: "sm" | "default";
  /** Readonly-first composers use this to mount their real editor before the
   * permission prompt opens. A normal always-mounted editor omits it. */
  onBeforeRecord?: () => void;
}

function supportedRecorderOptions(): MediaRecorderOptions | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const mimeType of MIME_TYPE_CANDIDATES) {
    if (MediaRecorder.isTypeSupported?.(mimeType)) return { mimeType };
  }
  return undefined;
}

function recordingExtension(mimeType: string): string {
  const baseType = mimeType.split(";", 1)[0]?.toLowerCase();
  switch (baseType) {
    case "audio/flac":
      return "flac";
    case "audio/mp4":
    case "audio/m4a":
    case "audio/x-m4a":
      return "m4a";
    case "audio/mpeg":
    case "audio/mp3":
      return "mp3";
    case "audio/ogg":
      return "ogg";
    case "audio/wav":
    case "audio/x-wav":
      return "wav";
    default:
      return "webm";
  }
}

function isPermissionError(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    (error.name === "NotAllowedError" || error.name === "SecurityError")
  );
}

function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}

function ApiVoiceInputButton({
  editorRef,
  disabled = false,
  className,
  size = "default",
  onBeforeRecord,
}: VoiceInputButtonProps) {
  const { t } = useT("editor");
  const transcriptionEnabled = useConfigStore(
    (state) => state.audioTranscriptionEnabled,
  );
  const [state, setState] = useState<VoiceInputState>("idle");
  const mountedRef = useRef(true);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transcriptionAbortRef = useRef<AbortController | null>(null);
  const recorderFailedRef = useRef(false);
  const microphoneRequestRef = useRef(0);

  const supported =
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof MediaRecorder !== "undefined";

  const clearRecordingTimer = useCallback(() => {
    if (recordingTimerRef.current !== null) {
      clearTimeout(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
  }, []);

  const releaseRecording = useCallback(() => {
    clearRecordingTimer();
    stopStream(streamRef.current);
    streamRef.current = null;
    recorderRef.current = null;
  }, [clearRecordingTimer]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      microphoneRequestRef.current += 1;
      clearRecordingTimer();
      transcriptionAbortRef.current?.abort();
      transcriptionAbortRef.current = null;
      const recorder = recorderRef.current;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onerror = null;
        recorder.onstop = null;
        if (recorder.state !== "inactive") recorder.stop();
      }
      releaseRecording();
    };
  }, [clearRecordingTimer, releaseRecording]);

  useEffect(() => {
    if (transcriptionEnabled) return;
    microphoneRequestRef.current += 1;
    transcriptionAbortRef.current?.abort();
    transcriptionAbortRef.current = null;
    recorderFailedRef.current = true;
    chunksRef.current = [];
    const recorder = recorderRef.current;
    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onerror = null;
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
    }
    releaseRecording();
    setState("idle");
  }, [releaseRecording, transcriptionEnabled]);

  const finishRecording = useCallback(
    async (mimeType: string) => {
      const chunks = chunksRef.current;
      chunksRef.current = [];
      releaseRecording();
      if (!mountedRef.current || recorderFailedRef.current) return;
      if (chunks.length === 0 || chunks.every((chunk) => chunk.size === 0)) {
        setState("idle");
        toast.error(t(($) => $.voice_input.empty));
        return;
      }

      const resolvedMimeType =
        mimeType || chunks.find((chunk) => chunk.type)?.type || "audio/webm";
      const recording = new File(
        chunks,
        `recording.${recordingExtension(resolvedMimeType)}`,
        { type: resolvedMimeType },
      );
      const controller = new AbortController();
      transcriptionAbortRef.current = controller;
      setState("transcribing");

      try {
        const text = await api.transcribeAudio(recording, controller.signal);
        if (!mountedRef.current) return;
        if (!editorRef.current?.insertPlainTextAtSelection(text.trim())) {
          toast.error(t(($) => $.voice_input.insert_failed));
        }
      } catch (error) {
        if (!mountedRef.current || controller.signal.aborted) return;
        toast.error(
          error instanceof ApiError && error.status === 422
            ? t(($) => $.voice_input.empty)
            : t(($) => $.voice_input.failed),
        );
      } finally {
        if (transcriptionAbortRef.current === controller) {
          transcriptionAbortRef.current = null;
        }
        if (mountedRef.current) setState("idle");
      }
    },
    [editorRef, releaseRecording, t],
  );

  const startRecording = useCallback(async () => {
    if (!supported || disabled || state !== "idle") return;
    onBeforeRecord?.();
    setState("requesting");
    recorderFailedRef.current = false;
    const requestId = ++microphoneRequestRef.current;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current || requestId !== microphoneRequestRef.current) {
        stopStream(stream);
        return;
      }
      streamRef.current = stream;

      const options = supportedRecorderOptions();
      const recorder = options
        ? new MediaRecorder(stream, options)
        : new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        recorderFailedRef.current = true;
        releaseRecording();
        if (mountedRef.current) {
          setState("idle");
          toast.error(t(($) => $.voice_input.failed));
        }
      };
      recorder.onstop = () => {
        void finishRecording(recorder.mimeType || options?.mimeType || "");
      };
      recorder.start();
      setState("recording");
      recordingTimerRef.current = setTimeout(() => {
        const activeRecorder = recorderRef.current;
        if (!activeRecorder || activeRecorder.state === "inactive") return;
        toast.info(t(($) => $.voice_input.recording_limit));
        activeRecorder.stop();
      }, MAX_RECORDING_MS);
    } catch (error) {
      releaseRecording();
      if (!mountedRef.current) return;
      setState("idle");
      toast.error(
        isPermissionError(error)
          ? t(($) => $.voice_input.permission_denied)
          : t(($) => $.voice_input.failed),
      );
    }
  }, [disabled, finishRecording, onBeforeRecord, releaseRecording, state, supported, t]);

  const stopRecording = useCallback(() => {
    clearRecordingTimer();
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    recorder.stop();
  }, [clearRecordingTimer]);

  const label = useMemo(() => {
    switch (state) {
      case "requesting":
        return t(($) => $.voice_input.requesting);
      case "recording":
        return t(($) => $.voice_input.recording);
      case "transcribing":
        return t(($) => $.voice_input.transcribing);
      default:
        return supported
          ? t(($) => $.voice_input.start)
          : t(($) => $.voice_input.unavailable);
    }
  }, [state, supported, t]);

  if (!transcriptionEnabled) return null;

  const iconClassName = size === "sm" ? "size-3.5" : "size-4";
  return (
    <Button
      type="button"
      variant="ghost"
      size={size === "sm" ? "icon-xs" : "icon-sm"}
      aria-label={label}
      aria-pressed={state === "recording"}
      aria-busy={state === "requesting" || state === "transcribing" || undefined}
      title={label}
      disabled={
        ((!supported || disabled) && state !== "recording") ||
        state === "requesting" ||
        state === "transcribing"
      }
      onClick={state === "recording" ? stopRecording : () => void startRecording()}
      className={cn(
        "text-muted-foreground",
        state === "recording" &&
          "bg-destructive/10 text-destructive hover:bg-destructive/20 hover:text-destructive",
        className,
      )}
    >
      {state === "recording" ? (
        <Square className={cn(iconClassName, "fill-current")} />
      ) : state === "requesting" || state === "transcribing" ? (
        <LoaderCircle className={cn(iconClassName, "animate-spin")} />
      ) : (
        <Mic className={iconClassName} />
      )}
    </Button>
  );
}

function VoiceInputButton(props: VoiceInputButtonProps) {
  const adapter = useDictationAdapter();
  // Do not mount MediaRecorder/API behavior at all when a native owner exists.
  return adapter ? (
    <NativeDictationButton {...props} adapter={adapter} />
  ) : (
    <ApiVoiceInputButton {...props} />
  );
}

export { VoiceInputButton, type VoiceInputButtonProps };
