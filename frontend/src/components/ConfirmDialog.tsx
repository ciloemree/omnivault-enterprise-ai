import { useOmni } from "../lib/store";
import { t } from "../lib/i18n";

export function ConfirmDialog() {
  const confirm = useOmni((s) => s.confirm);
  const lang = useOmni((s) => s.lang);
  if (!confirm) return null;

  function close(ok: boolean) {
    confirm?.resolve(ok);
    useOmni.setState({ confirm: null });
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 className="text-xl font-semibold">{t(lang, "confirmDelete")}</h2>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={() => close(false)}>
            {t(lang, "cancel")}
          </button>
          <button type="button" className="rounded-full bg-[#ff3b30] px-4 py-2 text-sm font-semibold text-white" onClick={() => close(true)}>
            {t(lang, "confirmYes")}
          </button>
        </div>
      </div>
    </div>
  );
}
