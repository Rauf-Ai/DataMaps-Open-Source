import { useEffect, useRef, useState } from "react";
import { Button } from "@gravity-ui/uikit";
import { Microphone, PaperPlane, Stop } from "@gravity-ui/icons";
import { Logo } from "../../Logo.jsx";
import { api } from "../../api.js";

const EXAMPLE_QUERIES = [
  "Найди 200 стоматологий Москвы с рейтингом от 4,5 и без сайта",
  "Собери поставщиков стройматериалов Татарстана с телефоном и email",
  "Покажи рестораны Дубая с Instagram и более чем 100 отзывами",
  "Найди конкурентов по установке ГБО в городах-миллионниках",
  "Найди салоны красоты Новосибирска без Telegram и выгрузи в Excel",
];

function useTypingPlaceholder(active) {
  const [text, setText] = useState("");
  const [phraseIndex, setPhraseIndex] = useState(0);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!active) {
      setText("");
      return undefined;
    }

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setText(EXAMPLE_QUERIES[0]);
      return undefined;
    }

    const phrase = EXAMPLE_QUERIES[phraseIndex];
    let delay = deleting ? 22 : 45;
    let nextText = deleting ? phrase.slice(0, Math.max(0, text.length - 1)) : phrase.slice(0, text.length + 1);
    let nextAction = () => setText(nextText);

    if (!deleting && text === phrase) {
      delay = 2200;
      nextAction = () => setDeleting(true);
    } else if (deleting && text === "") {
      delay = 450;
      nextAction = () => {
        setDeleting(false);
        setPhraseIndex((current) => (current + 1) % EXAMPLE_QUERIES.length);
      };
    }

    const timer = window.setTimeout(nextAction, delay);
    return () => window.clearTimeout(timer);
  }, [active, deleting, phraseIndex, text]);

  return text;
}

export function ChatComposer({ onSend, disabled, centered = false }) {
  const [value, setValue] = useState("");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const inputRef = useRef(null);
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const valueRef = useRef("");
  const sendAfterTranscriptionRef = useRef(false);
  const stoppingRef = useRef(false);
  const animatedPlaceholder = useTypingPlaceholder(centered && !value);

  useEffect(() => {
    if (!disabled) inputRef.current?.focus();
  }, [disabled]);

  useEffect(() => () => streamRef.current?.getTracks().forEach((track) => track.stop()), []);

  const transcribe = async (blob, sendAfterTranscription) => {
    setTranscribing(true);
    setVoiceError("");
    try {
      const form = new FormData();
      form.append("audio", blob, `voice.${blob.type.includes("ogg") ? "ogg" : "webm"}`);
      const response = await api("/api/chat/transcribe", { method: "POST", body: form });
      const transcript = String(response.text || "").trim();
      if (!transcript) throw new Error("Не удалось распознать речь");
      const content = [valueRef.current.trim(), transcript].filter(Boolean).join(" ");
      valueRef.current = content;
      if (sendAfterTranscription) {
        setValue("");
        valueRef.current = "";
        onSend(content);
      } else {
        setValue(content);
        window.requestAnimationFrame(() => inputRef.current?.focus());
      }
    } catch (requestError) {
      setVoiceError(requestError.message);
    } finally {
      setTranscribing(false);
    }
  };

  const finishRecording = (sendAfterTranscription) => {
    if (stoppingRef.current || !recorderRef.current) return;
    stoppingRef.current = true;
    sendAfterTranscriptionRef.current = sendAfterTranscription;
    recorderRef.current.stop();
    setRecording(false);
  };

  const toggleRecording = async () => {
    if (recording) {
      finishRecording(false);
      return;
    }
    setVoiceError("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setVoiceError("Голосовой ввод не поддерживается этим браузером");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferred = MediaRecorder.isTypeSupported?.("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus" : "audio/webm";
      const recorder = new MediaRecorder(stream, { mimeType: preferred });
      streamRef.current = stream;
      recorderRef.current = recorder;
      chunksRef.current = [];
      stoppingRef.current = false;
      sendAfterTranscriptionRef.current = false;
      recorder.ondataavailable = (event) => { if (event.data?.size) chunksRef.current.push(event.data); };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        recorderRef.current = null;
        stoppingRef.current = false;
        if (blob.size) transcribe(blob, sendAfterTranscriptionRef.current);
        else setVoiceError("Аудиозапись пустая");
      };
      recorder.start();
      setRecording(true);
    } catch {
      setVoiceError("Разрешите доступ к микрофону в настройках браузера");
    }
  };

  const submit = () => {
    const content = value.trim();
    if (!content || disabled) return;
    valueRef.current = "";
    setValue("");
    onSend(content);
  };

  const onKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className={`dm-chat-composer-wrap${centered ? " is-centered" : ""}`}>
      {centered && (
        <div className="dm-chat-welcome">
          <Logo className="dm-chat-welcome__logo" />
          <h1>Привет. Кого ищем сегодня?</h1>
        </div>
      )}
      <div className="dm-chat-composer">
        <textarea
          ref={inputRef}
          value={value}
          onChange={(event) => {
            valueRef.current = event.target.value;
            setValue(event.target.value);
          }}
          onKeyDown={onKeyDown}
          disabled={disabled}
          rows={1}
          maxLength={2000}
          placeholder={centered ? animatedPlaceholder : "Напишите сообщение"}
          aria-label="Сообщение AI-помощнику"
        />
        <Button
          view={recording ? "outlined-danger" : "flat"}
          size="l"
          onClick={toggleRecording}
          disabled={disabled || transcribing}
          loading={transcribing}
          aria-label={recording ? "Остановить запись" : "Голосовой ввод"}
          title={recording ? "Остановить и распознать" : "Голосовой ввод"}
          className={`dm-chat-voice${recording ? " is-recording" : ""}`}
        >
          {recording ? <Stop /> : <Microphone />}
        </Button>
        {recording ? (
          <Button
            view="action"
            size="l"
            onClick={() => finishRecording(true)}
            disabled={disabled || transcribing}
            aria-label="Расшифровать и отправить"
            title="Расшифровать и отправить"
            className="dm-chat-voice-send"
          >
            <PaperPlane />
          </Button>
        ) : (
          <Button
            view="action"
            size="l"
            onClick={submit}
            disabled={disabled || !value.trim()}
            loading={disabled}
            aria-label="Отправить сообщение"
            title="Отправить"
            className="dm-chat-send"
          >
            <PaperPlane />
          </Button>
        )}
      </div>
      {recording && <span className="dm-chat-voice-status"><i /> Слушаю… Квадрат — расшифровать текст, стрелка — расшифровать и отправить</span>}
      {voiceError && <span className="dm-chat-voice-error">{voiceError}</span>}
      <span className="dm-chat-composer__hint">Enter — отправить · Shift+Enter — новая строка</span>
    </div>
  );
}
