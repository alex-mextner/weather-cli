# Changelog

## 0.1.0

- Decision functions: rounded temperature, feels-like only on a ≥ 6° gap, humidity against the
  month/hour norm, wind by umbrella category (gusts included), next-hour lookahead, tomorrow
  (automatic after 21:00 local), week insights (warming/cooling, snow/rain onset and end, frost,
  heat), umbrella and clothing answers.
- Providers: Home Assistant (`weather.*` + `get_forecasts`) and Open-Meteo, merged; file cache,
  offline fallback, 1.2 s deadline.
- Climate norms from the ERA5 archive, cached per place.
- CLI: `weather say [--now|--tomorrow|--week|--umbrella|--dress] [--json]`, `weather explain`,
  `weather climate update|show`.
