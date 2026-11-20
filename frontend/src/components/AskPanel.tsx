import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Check, ChevronDown, LoaderCircle, Mic } from "lucide-react";
import { useOmni } from "../lib/store";
import { api } from "../lib/api";
import { roomLabel, t } from "../lib/i18n";
import { AnswerView } from "./AnswerView";

type EngineOption = { id: string; label_en: string; label_tr: string };

const FALLBACK_ENGINES: EngineOption[] = [
  { id: "local", label_en: "Local • Qwen 2.5 (3B) — 100% offline", label_tr: "Yerel • Qwen 2.5 (3B) — %100 Çevrimdışı" },
  { id: "groq-llama", label_en: "Groq • Llama 3 (70B) — ultra fast", label_tr: "Groq • Llama 3 (70B) — Ultra Hızlı / Açık Ağırlık" },
  { id: "gemma-4-26b", label_en: "Google • Gemma 4 (26B)", label_tr: "Google • Gemma 4 (26B) — Yüksek Kota" },
  { id: "gemini-flash", label_en: "Google • Gemini 3.8 Flash", label_tr: "Google • Gemini 3.8 Flash — Hızlı Kurumsal Analiz" },
];

function EngineMenu({
  engine,
  engines,
  lang,
  onChange,
}: {
  engine: string;
  engines: EngineOption[];
  lang: string;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const selected = engine === "gemma-2-27b" ? "gemma-4-26b" : ["local", "groq-llama", "gemma-4-26b", "gemini-flash"].includes(engine) ? engine : "local";
  const current = engines.find((item) => item.id === selected) || engines[0];
  const label = lang === "tr" ? current?.label_tr : current?.label_en;

  useEffect(() => {
    if (!open) return;
    function close(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  return (
    <div className="engine-menu" ref={root}>
      <button type="button" className="engine-trigger" aria-expanded={open} aria-haspopup="listbox" onClick={() => setOpen((value) => !value)}>
        <span>{label}</span>
        <ChevronDown size={16} />
      </button>
      {open && (
        <div className="engine-panel" role="listbox">
          {engines.map((item) => {
            const active = item.id === selected;
            return (
              <button
                key={item.id}
                type="button"
                role="option"
                aria-selected={active}
                className={active ? "active" : ""}
                onClick={() => {
                  onChange(item.id);
                  setOpen(false);
                }}
              >
                <span>{lang === "tr" ? item.label_tr : item.label_en}</span>
                {active && <Check size={16} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function AskPanel() {
  const ask = useOmni((s) => s.ask);
  const querying = useOmni((s) => s.querying);
  const turns = useOmni((s) => s.turns);
  const lastQuestion = useOmni((s) => s.lastQuestion);
  const startTopic = useOmni((s) => s.startTopic);
  const documents = useOmni((s) => s.documents);
  const lang = useOmni((s) => s.lang);
  const department = useOmni((s) => s.department);
  const engine = useOmni((s) => s.engine);
  const setEngine = useOmni((s) => s.setEngine);
  const settings = useOmni((s) => s.settings);
  const [question, setQuestion] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [listening, setListening] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const tick = useRef<number>(0);
  const box = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [awayFromEnd, setAwayFromEnd] = useState(false);

  const readyCount = useMemo(() => documents.filter((doc) => doc.status === "ready").length, [documents]);
  const pending = querying && Boolean(lastQuestion) && turns.at(-1)?.question !== lastQuestion;
  const chatting = turns.length > 0 || pending;

  useEffect(() => {
    if (!readyCount) {
      setSuggestions([]);
      return;
    }
    api.suggestions(lang).then((result) => setSuggestions(result.questions)).catch(() => setSuggestions([]));
  }, [readyCount, lang, department, documents]);

  function scrollToLatest() {
    const node = scroller.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }

  useEffect(() => {
    scrollToLatest();
  }, [turns, querying, lastQuestion, pending]);

  function onScroll() {
    const node = scroller.current;
    if (!node) return;
    const gap = node.scrollHeight - node.scrollTop - node.clientHeight;
    setAwayFromEnd(gap > 80);
  }

  const persona =
    department === "all"
      ? t(lang, "deptAll")
      : department === "public"
      ? t(lang, "deptPublic")
      : department === "hr"
        ? t(lang, "deptHr")
        : department === "finance"
          ? t(lang, "deptFinance")
          : department === "engineering"
            ? t(lang, "deptEngineering")
            : department === "legal"
              ? t(lang, "deptLegal")
              : department === "sales"
                ? t(lang, "deptSales")
                : roomLabel(lang, department, department);

  function resize(node: HTMLTextAreaElement) {
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || querying) return;
    const saved = await ask(trimmed);
    if (saved) {
      setQuestion("");
      if (box.current) {
        box.current.style.height = "auto";
      }
    }
  }

  async function toggleMic() {
    if (listening) {
      recorder.current?.stop();
      window.clearInterval(tick.current);
      setListening(false);
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const media = new MediaRecorder(stream);
    const parts: Blob[] = [];
    media.ondataavailable = (event) => {
      if (event.data.size) parts.push(event.data);
    };
    media.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(parts, { type: media.mimeType || "audio/webm" });
      const result = await api.transcribe(blob, "question.webm", lang);
      if (result.text) {
        setQuestion(result.text);
        if (box.current) resize(box.current);
      }
    };
    media.start();
    recorder.current = media;
    setElapsed(0);
    tick.current = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    setListening(true);
  }

  return (
    <section className="chat-workspace">
      <header className="chat-header">
        <div className="chat-meta">
          <div className="chat-pills">
            <span className="chip">
              {t(lang, "assistantLabel")}: {persona}
            </span>
            <span className="chip">
              {readyCount} {t(lang, "assetsReady")}
            </span>
          </div>
          <button type="button" className="new-chat-btn" onClick={() => startTopic()}>
            {t(lang, "newChat")}
          </button>
        </div>
        <div className="chat-tools">
          <EngineMenu
            engine={engine}
            engines={settings?.engines?.length ? settings.engines : FALLBACK_ENGINES}
            lang={lang}
            onChange={setEngine}
          />
        </div>
      </header>

      <div className="chat-stage">
      <div ref={scroller} className={chatting ? "chat-scroll" : "chat-scroll chat-scroll-empty"} onScroll={onScroll}>
        {!chatting && (
          <div className="chat-welcome">
            <h2>{t(lang, "askTitle")}</h2>
            <p>{t(lang, "askSubtitle")}</p>
            {suggestions.length > 0 && (
              <div className="suggest-list">
                <div className="suggest-label">{t(lang, "suggested")}</div>
                {suggestions.map((item) => (
                  <button key={item} type="button" className="suggest-item" onClick={() => void send(item)}>
                    {item}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {turns.map((turn, index) => (
          <div key={`${turn.question}-${index}`} className="contents">
            <div className="user-bubble">{turn.question}</div>
            <AnswerView answer={turn.answer} turnIndex={index} />
          </div>
        ))}
        {pending && <div className="user-bubble">{lastQuestion}</div>}
        {querying && <div className="assistant-bubble text-[14px] text-[#6e6e73]">{t(lang, "asking")}</div>}
        <div ref={messagesEndRef} />
      </div>
      {awayFromEnd && chatting && (
        <button
          type="button"
          className="jump-latest"
          onClick={() => scrollToLatest()}
        >
          {t(lang, "jumpToLatest")}
        </button>
      )}
      </div>

      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send(question);
        }}
      >
        {chatting && suggestions.length > 0 && (
          <div className="prompt-row">
            <span className="text-[12px] text-[#6e6e73]">{t(lang, "suggested")}:</span>
            {suggestions.map((item) => (
              <button key={item} type="button" className="source-pill" onClick={() => void send(item)}>
                {item}
              </button>
            ))}
          </div>
        )}
        {listening && (
          <div className="listen-line">
            {t(lang, "listening")} {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
          </div>
        )}
        <div className="composer-box">
          <button type="button" className={listening ? "voice-btn mic-live" : "voice-btn"} aria-label={t(lang, "voiceAsk")} onClick={() => void toggleMic()}>
            <Mic size={16} />
          </button>
          <textarea
            ref={box}
            rows={1}
            value={question}
            placeholder={t(lang, "composerPlaceholder")}
            onChange={(event) => {
              setQuestion(event.target.value);
              resize(event.target);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(question);
              }
            }}
          />
          <button type="submit" className="send-btn" aria-label={t(lang, "send")} disabled={querying || !question.trim()}>
            {querying ? <LoaderCircle className="animate-spin" size={16} /> : <ArrowUp size={16} />}
          </button>
        </div>
      </form>
    </section>
  );
}
