import { FormEvent, useState } from "react";
import { useOmni } from "../lib/store";
import { t } from "../lib/i18n";

const DEMOS = [
  { persona: "admin", label: "Admin" },
  { persona: "ik", label: "İK" },
  { persona: "finans", label: "Finans" },
  { persona: "teknik", label: "Teknik" },
] as const;

export function LoginScreen() {
  const lang = useOmni((s) => s.lang);
  const login = useOmni((s) => s.login);
  const loginDemo = useOmni((s) => s.loginDemo);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(nextUser: string, nextPassword: string) {
    setBusy(true);
    setError("");
    try {
      await login(nextUser, nextPassword);
    } catch (err) {
      setError(err instanceof Error ? err.message : t(lang, "loginFailed"));
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit(username.trim(), password);
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-5">
      <section className="card w-full max-w-[420px] p-8">
        <div className="text-[13px] font-semibold tracking-wide text-[#6e6e73]">OMNIVAULT</div>
        <h1 className="mt-4 text-[22px] font-semibold">{t(lang, "signIn")}</h1>
        <p className="mt-2 text-[15px] text-[#6e6e73]">{t(lang, "loginSubtitle")}</p>
        <form onSubmit={onSubmit} className="mt-6 space-y-3" autoComplete="on">
          <input
            className="field"
            autoComplete="username"
            placeholder={t(lang, "username")}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <input
            className="field"
            type="password"
            autoComplete="current-password"
            placeholder={t(lang, "password")}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && <p className="text-sm text-[#b42318]">{error}</p>}
          <button type="submit" className="btn-primary w-full" disabled={busy || !username.trim() || !password}>
            {t(lang, "signIn")}
          </button>
        </form>
        <div className="mt-6 text-[13px] text-[#6e6e73]">{t(lang, "demoLogin")}</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {DEMOS.map((demo) => (
            <button
              key={demo.persona}
              type="button"
              className="rounded-full bg-[#f5f5f7] px-3 py-1.5 text-[13px] hover:bg-[#e8e8ed]"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setError("");
                void loginDemo(demo.persona)
                  .catch((err) => setError(err instanceof Error ? err.message : t(lang, "loginFailed")))
                  .finally(() => setBusy(false));
              }}
            >
              {demo.label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
