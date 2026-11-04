/** Voice choices this browser profile remembers (ADR 0069). They stay on this
 * device: they are not settings, memories or synced data. The microphone flag
 * is the user's intent only; the OS still decides consent every time. The
 * audio devices are browser device IDs, empty for the system default. */
export type VoicePreferences = {
  engine: "chat" | "lfm"; mode: "ptt" | "handsfree"; microphoneEnabled: boolean; inputDevice: string; outputDevice: string;
};

const key = "imprint.voice.preferences.v1";

/** Unknown, malformed or unreadable storage falls back to typed push-to-talk with the microphone off. */
export function readVoicePreferences(): VoicePreferences {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    const device = (id: unknown) => typeof id === "string" ? id.slice(0, 512) : "";
    return { engine: value?.engine === "lfm" ? "lfm" : "chat", mode: value?.mode === "handsfree" ? "handsfree" : "ptt",
      microphoneEnabled: value?.microphoneEnabled === true, inputDevice: device(value?.inputDevice), outputDevice: device(value?.outputDevice) };
  } catch { return { engine: "chat", mode: "ptt", microphoneEnabled: false, inputDevice: "", outputDevice: "" }; }
}

export function saveVoicePreferences(value: Partial<VoicePreferences>) {
  try { localStorage.setItem(key, JSON.stringify({ ...readVoicePreferences(), ...value })); }
  catch { /* Voice still works when browser storage is unavailable; the choice is just not kept. */ }
}
