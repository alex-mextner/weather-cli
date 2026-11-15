import { describe, expect, test } from 'bun:test';

import pkg from '../package.json' with { type: 'json' };

async function weather(...args: string[]): Promise<{ code: number; out: string; err: string }> {
  const proc = Bun.spawn(['bun', 'src/cli/main.ts', ...args], {
    cwd: new URL('..', import.meta.url).pathname,
    stdout: 'pipe',
    stderr: 'pipe',
    env: {
      PATH: process.env.PATH ?? '',
      HOME: '/nonexistent',
      WEATHER_CACHE_DIR: '/nonexistent/cache',
    },
  });
  const [out, err] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
  ]);
  return { code: await proc.exited, out: out.trim(), err: err.trim() };
}

const RAIN = ['--bundle', 'examples/rain-wind-evening.json', '--at', '2026-11-09T21:40:00+01:00'];
const CALM = ['--bundle', 'examples/calm-noon.json', '--at', '2026-10-12T12:15:00+02:00'];

describe('weather CLI on saved bundles (the README examples)', () => {
  test('say', async () => {
    expect((await weather('say', ...CALM)).out).toBe('На улице 18 градусов, ясно.');
    expect((await weather('say', ...RAIN)).out).toBe(
      'На улице 8 градусов, идёт дождь. Сильный ветер, зонт может вывернуть. Завтра днём 11, ночью 6 градусов, пасмурно.',
    );
  });

  test('modes', async () => {
    expect((await weather('say', '--umbrella', ...RAIN)).out).toBe(
      'Идёт дождь, а ветер сильный: зонт унесёт, лучше куртка с капюшоном.',
    );
    expect((await weather('say', '--tomorrow', ...CALM)).out).toStartWith(
      'Завтра днём 18, ночью 8 градусов, ясно',
    );
    expect((await weather('say', '--week', ...CALM)).out).toEndWith('Резких перемен не будет.');
  });

  test('--json carries the decisions', async () => {
    const parsed: { mode: string; text: string; decisions: { id: string; fired: boolean }[] } =
      JSON.parse((await weather('say', '--json', ...RAIN)).out);
    expect(parsed.mode).toBe('now');
    expect(parsed.decisions.find((d) => d.id === 'wind')?.fired).toBe(true);
  });

  test('explain shows silent decisions with their reasons', async () => {
    const { out } = await weather('explain', ...CALM);
    expect(out).toContain('· feels');
    expect(out).toContain('needs ≥ 6° either way');
    expect(out).toContain('→ На улице 18 градусов, ясно.');
  });
});

describe('weather CLI plumbing', () => {
  test('version is the package version', async () => {
    expect((await weather('--version')).out).toBe(pkg.version);
  });

  test('help works with nothing configured', async () => {
    const { code, out } = await weather('--help');
    expect(code).toBe(0);
    expect(out).toContain('weather say');
  });

  test('usage errors exit 2', async () => {
    expect((await weather('nonsense')).code).toBe(2);
    expect((await weather('say', '--week', '--tomorrow', ...CALM)).code).toBe(2);
    expect((await weather('say', ...CALM, '--at', 'yesterday')).code).toBe(2);
  });

  test('no location configured exits 1 with a hint', async () => {
    const { code, err } = await weather('say');
    expect(code).toBe(1);
    expect(err).toContain('no location');
  });
});
