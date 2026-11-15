/**
 * @file  File-backed `Store`: one JSON file per key in a directory, written atomically.
 *
 * Accessed via: the CLI and the voice host. The only module in the library that touches the disk.
 *
 * Assumptions: the directory may be missing (created on first write) or unwritable (writes
 * fail silently upstream: no cache is a slower answer, not a broken one).
 */

import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import type { Store } from './cache';

export class FileStore implements Store {
  constructor(private readonly dir: string) {}

  async read(key: string): Promise<string | null> {
    try {
      return await readFile(join(this.dir, key), 'utf8');
    } catch {
      return null;
    }
  }

  async write(key: string, value: string): Promise<void> {
    await mkdir(this.dir, { recursive: true });
    const target = join(this.dir, key);
    const temporary = `${target}.${process.pid}.tmp`;
    await writeFile(temporary, value);
    await rename(temporary, target);
  }
}
