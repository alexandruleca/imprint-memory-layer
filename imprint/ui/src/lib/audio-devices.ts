"use client";

import { useEffect, useState } from "react";

export type AudioDevice = { id: string; label: string };
export type AudioDevices = {
  inputs: AudioDevice[];
  outputs: AudioDevice[];
  /** What "System default" currently is, when the browser names it. */
  defaultInput: string;
  defaultOutput: string;
  /** Whether Echo's speaker can be chosen: Web Audio's setSinkId (Chromium). */
  outputSupported: boolean;
  /** Browsers name devices only once the page may use the microphone. */
  labelled: boolean;
};

const empty: AudioDevices = { inputs: [], outputs: [], defaultInput: "", defaultOutput: "", outputSupported: false, labelled: false };
// Chromium lists the system default and, on Windows, the communications
// device as extra entries that follow the system's choice.
const ALIASES = new Set(["default", "communications"]);

/** The microphones and speakers this device has while ``open`` (the
 * settings that show them), kept current as they are plugged in or removed.
 * ``refresh`` changes when names may have become readable, such as after the
 * microphone is allowed. */
export function useAudioDevices(open: boolean, refresh: unknown): AudioDevices {
  const [devices, setDevices] = useState<AudioDevices>(empty);
  useEffect(() => {
    const media = navigator.mediaDevices;
    if (!open || !media?.enumerateDevices) return;
    let current = true;
    async function list() {
      const found = await media.enumerateDevices().catch(() => [] as MediaDeviceInfo[]);
      if (!current) return;
      const named = (kind: MediaDeviceKind, fallback: string) => found.filter(device => device.kind === kind && !ALIASES.has(device.deviceId))
        .map((device, index) => ({ id: device.deviceId, label: device.label || `${fallback} ${index + 1}` }));
      const systemDefault = (kind: MediaDeviceKind) =>
        found.find(device => device.kind === kind && device.deviceId === "default")?.label.replace(/^Default - /, "") ?? "";
      setDevices({
        inputs: named("audioinput", "Microphone"), outputs: named("audiooutput", "Speaker"),
        defaultInput: systemDefault("audioinput"), defaultOutput: systemDefault("audiooutput"),
        outputSupported: typeof AudioContext !== "undefined" && "setSinkId" in AudioContext.prototype,
        labelled: found.some(device => device.label),
      });
    }
    void list();
    media.addEventListener("devicechange", list);
    return () => { current = false; media.removeEventListener("devicechange", list); };
  }, [open, refresh]);
  return devices;
}
