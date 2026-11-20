import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useOmni } from "../lib/store";
import { api } from "../lib/api";
import { roomLabel, t } from "../lib/i18n";
import { DocumentCard } from "./DocumentCard";

type Intake = "files" | "voice" | "note" | "url";

export function VaultPanel() {
  const documents = useOmni((s) => s.documents);
  const upload = useOmni((s) => s.upload);
  const saveNote = useOmni((s) => s.saveNote);
  const lang = useOmni((s) => s.lang);
  const role = useOmni((s) => s.role);
  const user = useOmni((s) => s.user);
  const readOnly = user?.role !== "admin" && user?.permission === "read_only";
  const activeDepartment = useOmni((s) => s.department);
  const setWorkspace = useOmni((s) => s.setDepartment);
  const rooms = useOmni((s) => s.rooms);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [intake, setIntake] = useState<Intake>("files");
  const [filter, setFilter] = useState<"all" | "pdf" | "audio" | "image" | "text" | "office">("all");
  const [audioName, setAudioName] = useState("");
  const [customDepartments, setCustomDepartments] = useState<Array<{ slug: string; name: string }>>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [pageUrl, setPageUrl] = useState("");
  const [voiceText, setVoiceText] = useState("");
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [linkDraft, setLinkDraft] = useState("");
  const [linkTitle, setLinkTitle] = useState("");
  const [linkBusy, setLinkBusy] = useState(false);
  const [linkError, setLinkError] = useState("");

  useEffect(() => {
    if (role !== "admin") return;
    api.departments().then((rows) => setCustomDepartments(rows)).catch(() => setCustomDepartments([]));
  }, [role]);

  const lockedDepartment =
    role === "hr" || role === "finance" || role === "engineering" || role === "legal" || role === "sales"
      ? role
      : activeDepartment === "all"
        ? "yonetim"
        : activeDepartment;

  const filtered = useMemo(() => {
    if (filter === "all") return documents;
    return documents.filter((doc) => doc.modality === filter);
  }, [documents, filter]);

  async function onFiles(fileList: FileList | null) {
    if (!fileList?.length) return;
    const files = Array.from(fileList);
    const audio = files.filter((file) => /\.(mp3|wav|m4a|webm|ogg)$/i.test(file.name));
    const rest = files.filter((file) => !audio.includes(file));
    if (rest.length) await upload(rest, lockedDepartment);
    if (!audio[0]) return;
    setVoiceBusy(true);
    try {
      const result = await api.transcribe(audio[0], audio[0].name, lang);
      setVoiceText(result.text || "");
      setAudioName(audio[0].name.replace(/\.[^.]+$/, ""));
    } finally {
      setVoiceBusy(false);
    }
  }

  async function onNote(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !body.trim()) return;
    await saveNote({ title: title.trim(), body: body.trim(), department: lockedDepartment });
    setTitle("");
    setBody("");
  }

  return (
    <div className="space-y-5">
      <section className="card p-6">
        <h2 className="text-[28px] font-semibold tracking-tight">{t(lang, "vaultTitle")}</h2>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-[#6e6e73]">{t(lang, "vaultSubtitle")}</p>
        {readOnly ? <p className="mt-4 text-sm text-[#6e6e73]">Bu hesap yalnızca görüntüleyebilir ve soru sorabilir.</p> : <>
        <div className="mt-5 flex flex-wrap gap-2">
          {(
            [
              ["files", "tabFiles"],
              ["voice", "tabVoice"],
              ["note", "tabNote"],
              ["url", "tabUrl"],
            ] as const
          ).map(([id, key]) => (
            <button
              key={id}
              type="button"
              onClick={() => setIntake(id)}
              className={
                intake === id
                  ? "rounded-full bg-[#1d1d1f] px-3 py-1.5 text-[13px] text-white"
                  : "rounded-full bg-[#f5f5f7] px-3 py-1.5 text-[13px]"
              }
            >
              {t(lang, key)}
            </button>
          ))}
        </div>

        {role === "admin" && (
          <div className="dept-tag-row">
            <span className="dept-tag-label">{t(lang, "noteDept")}</span>
            <select className="field" value={lockedDepartment} onChange={(event) => void setWorkspace(event.target.value)}>
              {(rooms.length ? rooms : customDepartments).map((item) => (
                <option key={item.slug} value={item.slug}>
                  {roomLabel(lang, item.slug, item.name)}
                </option>
              ))}
            </select>
          </div>
        )}

        {intake === "files" && (
          <div
            className="mt-4 rounded-2xl border border-dashed border-black/15 bg-[#f5f5f7] p-8 text-center"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              void onFiles(event.dataTransfer.files);
            }}
          >
            <p className="text-[15px] text-[#6e6e73]">{t(lang, "dropHint")}</p>
            <p className="mx-auto mt-2 max-w-[680px] text-[12px] leading-5 text-[#86868b]">{t(lang, "dropFormats")}</p>
            <button type="button" className="btn-primary mt-4" onClick={() => inputRef.current?.click()}>
              {t(lang, "upload")}
            </button>
            {audioName && (
              <div className="mt-4 space-y-3 text-left">
                <p className="text-[13px] text-[#6e6e73]">{audioName}</p>
                <textarea className="field min-h-36" value={voiceText} onChange={(event) => setVoiceText(event.target.value)} />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!voiceText.trim()}
                  onClick={async () => {
                    await saveNote({ title: audioName, body: voiceText.trim(), department: lockedDepartment });
                    setVoiceText("");
                    setAudioName("");
                  }}
                >
                  {t(lang, "saveVault")}
                </button>
              </div>
            )}
            <input
              ref={inputRef}
              type="file"
              multiple
              className="hidden"
              accept=".pdf,.txt,.md,.markdown,.csv,.xlsx,.xls,.docx,.mp3,.wav,.m4a,.ogg,.flac,.aac,.webm,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff"
              onChange={(event) => void onFiles(event.target.files)}
            />
          </div>
        )}

        {intake === "voice" && (
          <div className="mt-4 space-y-3">
            <Recorder
              onBlob={async (blob) => {
                setVoiceBusy(true);
                try {
                  const result = await api.transcribe(blob, "voice-note.webm", lang);
                  setVoiceText(result.text || "");
                } finally {
                  setVoiceBusy(false);
                }
              }}
            />
            {voiceBusy && <p className="text-sm text-[#6e6e73]">{t(lang, "asking")}</p>}
            <p className="text-[13px] text-[#6e6e73]">{t(lang, "voicePreview")}</p>
            <textarea className="field min-h-36" value={voiceText} onChange={(event) => setVoiceText(event.target.value)} />
            <button
              type="button"
              className="btn-primary"
              disabled={!voiceText.trim()}
              onClick={async () => {
                await saveNote({ title: voiceText.trim().slice(0, 48), body: voiceText.trim(), department: lockedDepartment });
                setVoiceText("");
              }}
            >
              {t(lang, "saveVault")}
            </button>
          </div>
        )}

        {intake === "note" && (
          <form onSubmit={onNote} className="mt-4 space-y-3">
            <input className="field" placeholder={t(lang, "noteTitle")} value={title} onChange={(e) => setTitle(e.target.value)} />
            <textarea
              className="field min-h-36"
              placeholder={t(lang, "noteBody")}
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={!title.trim() || !body.trim()}>
              {t(lang, "noteSave")}
            </button>
          </form>
        )}

        {intake === "url" && (
          <>
          <form
            className="mt-4 flex flex-wrap gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!pageUrl.trim() || linkBusy) return;
              setLinkBusy(true);
              setLinkError("");
              try {
                const preview = await api.previewUrl(pageUrl.trim());
                setLinkTitle(preview.title);
                setLinkDraft(preview.text);
              } catch (error) {
                setLinkError(error instanceof Error ? error.message : "Could not read that page");
              } finally {
                setLinkBusy(false);
              }
            }}
          >
            <input
              className="field min-w-72 flex-1"
              placeholder={t(lang, "urlPlaceholder")}
              value={pageUrl}
              onChange={(event) => setPageUrl(event.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={!pageUrl.trim() || linkBusy}>
              {linkBusy ? t(lang, "asking") : t(lang, "urlSave")}
            </button>
          </form>
          {linkError && <p className="mt-2 text-sm text-[#b42318]">{linkError}</p>}
          {linkDraft && (
            <div className="mt-3 space-y-3">
              <p className="text-[13px] text-[#6e6e73]">{t(lang, "voicePreview")}</p>
              <textarea className="field min-h-36" value={linkDraft} onChange={(event) => setLinkDraft(event.target.value)} />
              <button
                type="button"
                className="btn-primary"
                disabled={!linkDraft.trim()}
                onClick={async () => {
                  await saveNote({ title: linkTitle || pageUrl, body: linkDraft.trim(), department: lockedDepartment });
                  setLinkDraft("");
                  setPageUrl("");
                }}
              >
                {t(lang, "saveVault")}
              </button>
            </div>
          )}
          </>
        )}
        </>}
      </section>

      <h3 className="text-lg font-semibold">{t(lang, "activeFiles")}</h3>
      <div className="flex flex-wrap gap-2">
        {(["all", "pdf", "office", "audio", "image", "text"] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setFilter(value)}
            className={
              filter === value
                ? "rounded-full bg-[#1d1d1f] px-3 py-1.5 text-[13px] text-white"
                : "rounded-full bg-white px-3 py-1.5 text-[13px] shadow-sm"
            }
          >
            {value === "all" ? t(lang, "all") : value === "office" ? t(lang, "officePill") : value.toUpperCase()}
          </button>
        ))}
      </div>
      {filtered.length === 0 ? (
        <section className="card p-8 text-[15px] text-[#6e6e73]">{t(lang, "emptyVault")}</section>
      ) : (
        <div className="grid gap-4">
          {filtered.map((document) => (
            <DocumentCard key={document.id} document={document} />
          ))}
        </div>
      )}
    </div>
  );
}

function Recorder({ onBlob }: { onBlob: (blob: Blob) => Promise<void> }) {
  const lang = useOmni((s) => s.lang);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const frame = useRef<number>(0);
  const startedAt = useRef(0);
  const canvas = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  async function start() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 64;
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    startedAt.current = performance.now();
    const draw = () => {
      analyser.getByteFrequencyData(data);
      const surface = canvas.current;
      if (surface) {
        const ctx = surface.getContext("2d");
        if (ctx) {
          const width = surface.width;
          const height = surface.height;
          ctx.clearRect(0, 0, width, height);
          const bars = 32;
          const barWidth = 4;
          const gap = 3;
          const total = bars * barWidth + (bars - 1) * gap;
          const start = Math.max(0, (width - total) / 2);
          for (let index = 0; index < bars; index += 1) {
            const value = data[index % data.length] / 255;
            const barHeight = Math.max(6, value * (height - 8));
            const x = start + index * (barWidth + gap);
            ctx.fillStyle = "#1d1d1f";
            ctx.beginPath();
            ctx.roundRect(x, (height - barHeight) / 2, barWidth, barHeight, 2);
            ctx.fill();
          }
        }
      }
      const elapsed = Math.floor((performance.now() - startedAt.current) / 1000);
      setSeconds((current) => (current === elapsed ? current : elapsed));
      frame.current = requestAnimationFrame(draw);
    };
    frame.current = requestAnimationFrame(draw);
    const media = new MediaRecorder(stream);
    chunks.current = [];
    media.ondataavailable = (event) => {
      if (event.data.size) chunks.current.push(event.data);
    };
    media.onstop = async () => {
      cancelAnimationFrame(frame.current);
      stream.getTracks().forEach((track) => track.stop());
      await context.close();
      const blob = new Blob(chunks.current, { type: media.mimeType || "audio/webm" });
      if (blob.size) await onBlob(blob);
    };
    media.start();
    recorder.current = media;
    setSeconds(0);
    setRecording(true);
  }

  function stop() {
    cancelAnimationFrame(frame.current);
    recorder.current?.stop();
    setRecording(false);
  }

  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  return (
    <div className="mt-4 rounded-2xl bg-[#f5f5f7] p-6">
      <canvas ref={canvas} width={560} height={72} className="mx-auto mb-3 h-[72px] w-full max-w-[420px]" />
      <div className="flex items-center justify-center gap-3">
        {recording && <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-[#ff3b30]" />}
        <span className="font-medium tabular-nums">{clock.padStart(5, "0")}</span>
        {recording ? (
          <button type="button" className="btn-primary" onClick={stop}>
            {t(lang, "stopRecord")}
          </button>
        ) : (
          <button type="button" className="btn-primary" onClick={() => void start()}>
            {t(lang, "record")}
          </button>
        )}
      </div>
    </div>
  );
}
