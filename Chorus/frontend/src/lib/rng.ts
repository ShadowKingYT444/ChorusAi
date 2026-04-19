/**
 * Deterministic pseudorandom number generator.
 * djb2 hash → LCG (Linear Congruential Generator)
 * All downstream generators must call in FIXED ORDER for determinism.
 */

export function hashIdea(text: string): number {
  let h = 5381;
  for (let i = 0; i < text.length; i++) {
    h = ((h << 5) + h) ^ text.charCodeAt(i);
    h = h >>> 0; // keep unsigned 32-bit
  }
  return h || 1; // never return 0
}

export class RNG {
  private state: number;

  constructor(seed: number) {
    this.state = seed >>> 0 || 1;
  }

  /** Returns [0, 1) */
  float(): number {
    // LCG parameters from Numerical Recipes
    this.state = (Math.imul(this.state, 1664525) + 1013904223) >>> 0;
    return this.state / 4294967296;
  }

  /** Returns integer in [min, max] inclusive */
  int(min: number, max: number): number {
    return Math.floor(this.float() * (max - min + 1)) + min;
  }

  /** Pick a random element from an array */
  pick<T>(arr: T[]): T {
    return arr[this.int(0, arr.length - 1)];
  }

  /** Fisher-Yates shuffle - mutates the array in place */
  shuffle<T>(arr: T[]): T[] {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = this.int(0, i);
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  /** Pick N unique elements from an array */
  pickN<T>(arr: T[], n: number): T[] {
    const copy = [...arr];
    this.shuffle(copy);
    return copy.slice(0, n);
  }
}
