#!/usr/bin/env bun
/**
 * @file  `weather` command line: say / explain / climate.
 *
 * Accessed via: bin/weather. Everything here is wiring; all wording and decisions live in the
 * library modules so the voice host and the CLI cannot disagree.
 *
 * Assumptions: exit codes — 0 ok, 1 runtime failure (no data, no location), 2 usage error.
 */

import { readFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';

import pkg from '../../package.json' with { type: 'json' };
import { MemoryStore, type Store } from '../cache';
import { FileStore } from '../cache-fs';
import { climateIsStale, loadClimate, refreshClimate } from '../climate';
import { say } from '../compose';
import { decodeBundleText } from '../cache';
import { getWeather } from '../service';
import { localParts } from '../time';
import type { Bundle, Mode, Place, Report } from '../types';
import { cacheDir, haConnection, readFileConfig, resolvePlace } from './config';

const HELP = `weather ${pkg.version} — weather in human Russian

Usage:
  weather say [--now|--tomorrow|--week|--umbrella|--dress] [--json]
  weather explain [mode flags]     which decisions fired, and why
  weather climate update|show      monthly norms (humidity, temperature) for the place

Place:    --lat N --lon N [--tz Europe/Belgrade]   (or WEATHER_LAT/WEATHER_LON/WEATHER_TZ,
          ~/.config/weather-cli/config.json, or Home Assistant's home zone)
Sources:  Open-Meteo always; Home Assistant when WEATHER_HA_URL + WEATHER_HA_TOKEN
          (HA_URL/HA_TOKEN) are set; --no-ha / --no-open-meteo to switch one off
Other:    --timeout MS (default 1200)   --offline (cache only)   --no-cache
          --bundle FILE --at ISO_TIME   (answer from a saved bundle at a given moment)
          --help  --version
`;

function modeOf(values: { [key: string]: boolean | string | undefined }): Mode {
  const flags: Mode[] = ['now', 'tomorrow', 'week', 'umbrella', 'dress'];
  const chosen = flags.filter((mode) => values[mode] === true);
  if (chosen.length > 1) throw new UsageError(`pick one mode, got: ${chosen.join(', ')}`);
  return chosen[0] ?? 'now';
}

class UsageError extends Error {}

function formatTime(ms: number, tz: string): string {
  const p = localParts(ms, tz);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${p.year}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`;
}

function renderExplain(report: Report, tz: string): string {
  const lines = [
    `mode ${report.mode} · data as of ${formatTime(report.asOf, tz)} (${report.sources.join('+') || '—'})${report.stale ? ' · STALE' : ''}`,
    '',
  ];
  for (const d of report.decisions) {
    const mark = d.fired ? (report.dropped.includes(d.id) ? '✂' : '✔') : '·';
    const said = d.fired && d.phrase ? `«${d.phrase}»` : 'silent';
    lines.push(`${mark} ${d.id.padEnd(13)} ${said}`);
    lines.push(`    ${d.why}`);
  }
  lines.push('', `→ ${report.text}`);
  if (report.dropped.length > 0)
    lines.push(`  (✂ fired but over the sentence budget: ${report.dropped.join(', ')})`);
  return lines.join('\n');
}

async function loadBundleFile(path: string): Promise<Bundle> {
  const bundle = decodeBundleText(await readFile(path, 'utf8'));
  if (bundle === null) throw new Error(`${path}: not a weather bundle`);
  return bundle;
}

async function run(argv: string[], env: Record<string, string | undefined>): Promise<number> {
  const [command, ...rest] = argv;
  if (command === undefined || command === '--help' || command === '-h' || command === 'help') {
    process.stdout.write(HELP);
    return 0;
  }
  if (command === '--version' || command === '-V') {
    process.stdout.write(`${pkg.version}\n`);
    return 0;
  }
  const { values, positionals } = parseArgs({
    args: rest,
    allowPositionals: true,
    options: {
      now: { type: 'boolean' },
      tomorrow: { type: 'boolean' },
      week: { type: 'boolean' },
      umbrella: { type: 'boolean' },
      dress: { type: 'boolean' },
      json: { type: 'boolean' },
      lat: { type: 'string' },
      lon: { type: 'string' },
      tz: { type: 'string' },
      timeout: { type: 'string' },
      offline: { type: 'boolean' },
      'no-cache': { type: 'boolean' },
      'no-ha': { type: 'boolean' },
      'no-open-meteo': { type: 'boolean' },
      bundle: { type: 'string' },
      at: { type: 'string' },
    },
  });
  if (command !== 'say' && command !== 'explain' && command !== 'climate') {
    throw new UsageError(`unknown command: ${command}`);
  }

  const timeoutMs = values.timeout === undefined ? 1200 : Number(values.timeout);
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0)
    throw new UsageError('--timeout must be a positive number');
  const at = values.at === undefined ? Date.now() : Date.parse(values.at);
  if (Number.isNaN(at))
    throw new UsageError('--at must be an ISO time with offset, e.g. 2026-11-09T22:30:00+01:00');

  const file = await readFileConfig(env);
  const ha = values['no-ha'] ? undefined : haConnection(env, file);
  const store: Store = values['no-cache'] ? new MemoryStore() : new FileStore(cacheDir(env));

  let bundle: Bundle | null = null;
  let place: Place;
  if (values.bundle !== undefined) {
    bundle = await loadBundleFile(values.bundle);
    place = bundle.place;
  } else {
    place = await resolvePlace(values, env, file, ha, timeoutMs);
  }

  if (command === 'climate') {
    const action = positionals[0] ?? 'show';
    if (action === 'update') {
      const climate = await refreshClimate(store, place, { timeoutMs: 60_000 });
      process.stdout.write(
        `climate norms built from ${climate.years[0]}–${climate.years[1]} for ${place.lat},${place.lon}\n`,
      );
      return 0;
    }
    if (action === 'show') {
      const climate = await loadClimate(store, place);
      process.stdout.write(
        climate === null
          ? 'no climate norms cached; run `weather climate update`\n'
          : `norms ${climate.years[0]}–${climate.years[1]}, built ${formatTime(climate.builtAt, place.tz)}${climateIsStale(climate, at) ? ' (stale)' : ''}\n`,
      );
      return 0;
    }
    throw new UsageError('climate: expected `update` or `show`');
  }

  let origin = 'file';
  const errors: string[] = [];
  if (bundle === null) {
    const result = await getWeather({
      place,
      store,
      timeoutMs,
      now: at,
      ...(ha ? { ha } : {}),
      ...(values['no-open-meteo'] ? { openMeteo: false } : {}),
      ...(values.offline ? { freshMs: Number.POSITIVE_INFINITY } : {}),
    });
    bundle = result.bundle;
    origin = result.origin;
    errors.push(...result.errors);
  }

  const climate = await loadClimate(store, place);
  const report = say({ bundle, now: at, mode: modeOf(values), climate });
  for (const error of errors) process.stderr.write(`warning: ${error}\n`);
  if (origin === 'stale')
    process.stderr.write(
      `warning: offline, using data from ${formatTime(bundle.fetchedAt, place.tz)}\n`,
    );

  if (values.json) {
    process.stdout.write(`${JSON.stringify({ ...report, origin }, null, 2)}\n`);
  } else if (command === 'explain') {
    process.stdout.write(`${renderExplain(report, place.tz)}\n`);
  } else {
    process.stdout.write(`${report.text}\n`);
  }
  return 0;
}

try {
  process.exitCode = await run(process.argv.slice(2), process.env);
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`weather: ${message}\n`);
  process.exitCode = error instanceof UsageError ? 2 : 1;
}
