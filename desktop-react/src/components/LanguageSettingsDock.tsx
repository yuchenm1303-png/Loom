import { Globe2 } from "lucide-react";
import { LOOM_LANGUAGES, currentLanguageLabel, useI18n, type LoomLanguage } from "../i18n";
import "./language-settings-dock.css";

export function LanguageSettingsDock() {
  const { language, setLanguage, t } = useI18n();

  const chooseLanguage = (next: LoomLanguage) => {
    if (next === language) return;
    setLanguage(next);
    window.dispatchEvent(new CustomEvent("loom:language-updated", {
      detail: { language: next },
    }));
  };

  return (
    <section className="language-settings-dock" aria-label={t("settings.general.language")}>
      <div className="language-settings-dock-copy">
        <span className="language-settings-dock-icon" aria-hidden="true">
          <Globe2 size={16} strokeWidth={1.8} />
        </span>
        <div>
          <strong>{t("settings.general.language")}</strong>
          <span>{t("settings.general.languageDesc")}</span>
        </div>
      </div>

      <div className="language-settings-options" role="group" aria-label={t("settings.general.language")}>
        {LOOM_LANGUAGES.map((item) => (
          <button
            type="button"
            key={item.value}
            className={item.value === language ? "active" : ""}
            onClick={() => chooseLanguage(item.value)}
            aria-pressed={item.value === language}
            title={item.label}
          >
            {item.nativeLabel}
          </button>
        ))}
      </div>

      <span className="language-settings-current">{currentLanguageLabel(language)}</span>
    </section>
  );
}
