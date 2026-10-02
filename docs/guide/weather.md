# Check forecasts and cloud conditions

These steps use the alternative interface. Open `#/next`, then open **WEATHER** > **CONDITIONS** (`#/weather/conditions`). The server root opens the classic interface; see [Interface routes](next-ui.md) if your screen looks different.

Weather views require the weather-view capability; some accounts do not see the hub. Forecasts, radar and satellite products are advice. They do not replace a connected safety monitor.

<a id="turning-it-on"></a>

<a id="the-sky-conditions-panel"></a>

## Enable forecasts

1. Confirm the saved observing site, then select **WEATHER SETTINGS** and enable **Weather enabled**.

2. Review **Cloud threshold (%)** and **Sustained for (min)** for cloud advice. Return to **CONDITIONS** and inspect the forecast and update state.

3. Use **RADAR** for the map and **SKY** for the local sky conditions view. Read unavailable or stale-data messages; an empty layer is not a clear-sky measurement.

<a id="auto-resume-weather-veto-fail-open"></a>

<a id="ignore-weather-tonight"></a>

<a id="the-high-cloud-night-warning"></a>

<a id="the-scope-pierce-point-overlay--what-it-means-physically"></a>

## Know what can hold a restart

Positive forecast precipitation within the next hour can veto auto-resume. Forecast cloud cover does not. Disabled, missing or failed forecast data does not provide a rain veto; this is not the same as an unsafe or stale reading from a connected safety monitor.

**IGNORE WEATHER TONIGHT** overrides that forecast-rain veto until the next dusk. It does not disable the safety monitor. Read the warning before enabling it; it is not a way to clear a hardware safety fault.

<a id="the-radar-map"></a>

<a id="the-scope-pierce-point-overlay-what-it-means-physically"></a>

## Cloud maps and image checks

Radar and satellite coverage depend on the selected product and location. Satellite cloud estimates and motion displays are advisory; they do not certify conditions at the telescope. Image-based cloud checks instead measure captured frames and can drive a configured cloud hold.

<a id="astrospheric-optional-seeing-transparency"></a>

<a id="astrospheric-optional-seeing--transparency"></a>

## Optional Astrospheric data

The settings include an optional Astrospheric API key for its forecast data. Supply your own authorized access; do not assume a paid account or its API terms cover redistribution or every client use. Keep the key out of screenshots and reports.

<a id="weather"></a>

## Related

[Safety and automation](safety-and-automation.md) · [Unattended nights](unattended-nights.md) · [Monitor](monitor.md)

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
