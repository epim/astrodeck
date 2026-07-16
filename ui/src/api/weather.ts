// api/weather.ts — typed client for the weather surfaces (weather spec §7/§9).
// Thin over the shared `api` fetch wrapper (api.ts): same ApiError throwing.
// Every weather route is view.site_precise-gated server-side — callers gate on
// useCanViewSitePrecise() so a non-holder client never issues a request (§8).
import { api } from "../api";
import type { AppConfig, WeatherState } from "../types";

/** GET /api/weather. view.site_precise. Full spec-§7 payload. */
export const getWeather = (): Promise<WeatherState> =>
  api.get<WeatherState>("/api/weather");

/** POST /api/weather/ignore-tonight {ignore}. control.capture.
 *  409 {code:"no_night"} when no site/night resolves. Returns the updated
 *  weather payload (the server also broadcasts it on the bus). */
export const setIgnoreTonight = (ignore: boolean): Promise<WeatherState> =>
  api.post<WeatherState>("/api/weather/ignore-tonight", { ignore });

/** Save body for POST /api/config/weather (server WeatherSaveBody.weather).
 *  Key contract (deadman_url precedent): null/empty key = KEEP the stored key
 *  (the UI round-trips the masked config); clearKey=true clears it. */
export interface WeatherConfigInput {
  enabled: boolean;
  cloud_threshold_pct: number;
  sustain_minutes: number;
  astrospheric_api_key: string | null;
}

/** POST /api/config/weather {weather, version, clear_astrospheric_key}.
 *  config.site_optics. 409 on a version conflict (reload-and-retoast). */
export const saveWeatherConfig = (
  weather: WeatherConfigInput,
  version: number | null,
  clearKey = false,
): Promise<AppConfig> =>
  api.post<AppConfig>("/api/config/weather", {
    weather,
    version,
    clear_astrospheric_key: clearKey,
  });
