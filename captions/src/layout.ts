import type { Caption } from "@remotion/captions";

// Caption page layout for Slovak two-line captions.
//
// Line-breaking rules synthesized from the Netflix Slovak Timed Text Style
// Guide, Slovak typographic norms (JÚĽŠ SAV, STN 01 6910), BBC/ESIST
// subtitling standards and this project's own conventions:
//  - a sentence always starts at the start of a line, and a sentence never
//    starts on line 2 of a page and spills into the next page
//  - bottom-heavy pages: line 1 is never longer than line 2
//  - one-letter words (k s v z o u a i) never end a line            [hard]
//  - a lone word right after a comma never ends a line              [strong]
//  - prepositions/conjunctions should not end a line                [strong]
//  - clitics (sa si som mi ti mu ho…) never start a line            [strong]
//  - numbers stay glued to the following word (50 %, č. 5)          [strong]
//  - breaking right after , ; : is preferred                        [bonus]

export const LINE_MAX_CHARS = 24; // per caption line, at FONT_SIZE inside the 84%-wide block
export const PAGE_HOLD_MS = 300; // keep a finished page visible briefly after the last word
export const MIN_PAGE_MS = 1000; // anti-flash: a page never shows shorter than this
export const MAX_BRIDGE_MS = 1000; // gaps between pages below this are bridged (no flicker)
export const LINE_LEAD_MS = 80; // reveal a line slightly before its first word is spoken

export type PageToken = { text: string; fromMs: number; toMs: number };
export type Page = { startMs: number; endMs: number; lines: PageToken[][] };

// never allowed to end a line (Slovak one-letter prepositions/conjunctions)
const ONE_LETTER_WORDS = new Set(["a", "i", "k", "o", "s", "u", "v", "z"]);

// prepositions, conjunctions and relativizers that should not end a line
// (applies only when the word carries no punctuation — "tak," is a fine break)
const LINE_END_AVOID = new Set([
  "na", "do", "po", "za", "zo", "so", "ku", "od", "pod", "nad", "pri", "pre",
  "cez", "bez", "pred", "medzi", "podľa", "okolo", "oproti", "počas", "proti",
  "okrem", "popri", "vedľa", "voči", "kvôli", "skrz", "spod",
  "že", "aby", "ale", "keď", "či", "ako", "keby", "lebo", "keďže", "pretože",
  "než", "akoby", "žeby", "aj", "ani", "no", "tak", "nie",
  "ktorý", "ktorá", "ktoré", "ktorí", "ktorú", "ktorom", "ktorej", "ktorých", "ktorým",
  "čo", "kde", "kam", "kedy", "prečo",
]);

// unstressed second-position clitics — must not be stranded at a line start
const CLITICS = new Set([
  "sa", "si", "som", "sme", "ste", "by", "mi", "ti", "mu", "jej", "im",
  "ma", "ťa", "ho", "ju", "ich", "to", "nám", "vám", "nás", "vás",
]);

const isCapitalized = (text: string) => /^[\p{Lu}]/u.test(text.replace(/^[^\p{L}]+/u, ""));

const NUMBER_RE = /^\d+[.,]?\d*$/;

const bare = (text: string) => text.replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();

export const lineLen = (line: PageToken[]) =>
  line.reduce((n, t) => n + t.text.length, 0) + Math.max(0, line.length - 1);

export const isSentenceEnd = (text: string) => /[.!?…]["')]*$/.test(text);

const hasTrailingPunct = (text: string) => /[.,!?…;:]["')]*$/.test(text);

/** Penalty for breaking a line after `line`, with `next` starting the following
 *  line of the same sentence (undefined when the sentence ends here). */
export const breakPenalty = (line: PageToken[], next: PageToken | undefined): number => {
  const last = line[line.length - 1];
  const lastBare = bare(last.text);
  let penalty = 0;

  // a single word longer than the line cap still needs a line of its own
  if (lineLen(line) > LINE_MAX_CHARS) return line.length === 1 ? 500 : Infinity;
  if (next === undefined) return 0; // sentence end — always a perfect break

  // one-letter prepositions/conjunctions never end a line (hard rule)
  if (ONE_LETTER_WORDS.has(lastBare) && !hasTrailingPunct(last.text)) return Infinity;

  if (hasTrailingPunct(last.text)) {
    penalty -= /[,;:]["')]*$/.test(last.text) ? 8 : 0; // breaking after a comma is the best break
  } else {
    // a lone word right after a comma must not end the line ("tomu, že" → break at the comma)
    if (line.length >= 2 && /,["')]*$/.test(line[line.length - 2].text)) penalty += 120;
    // prepositions/conjunctions don't end lines
    if (LINE_END_AVOID.has(lastBare)) penalty += 60;
    // numbers stay with what they count ("50 %", "č. 5")
    if (NUMBER_RE.test(lastBare)) penalty += 150;
  }

  // clitics never start a line ("Bolo to…" must not become "Bolo\nto…")
  if (CLITICS.has(bare(next.text))) penalty += 80;

  // multi-word proper names stay together ("Tomom Cruisom" — two capitalized
  // words mid-sentence are almost certainly a name pair in Slovak)
  if (isCapitalized(last.text) && isCapitalized(next.text) && !hasTrailingPunct(last.text)) penalty += 100;

  return penalty;
};

/** Best layout of a token run as ONE page (one line, or a bottom-heavy pair).
 *  Returns Infinity cost when the run cannot form a legal page. */
const layoutPage = (tokens: PageToken[]): { cost: number; lines: PageToken[][] } => {
  // single line — fine for sentence remainders and short sentences
  if (lineLen(tokens) <= LINE_MAX_CHARS) {
    return { cost: tokens.length === 1 && tokens[0].text.length <= 3 ? 25 : 0, lines: [tokens] };
  }
  let best: { cost: number; lines: PageToken[][] } = { cost: Infinity, lines: [tokens] };
  for (let cut = 1; cut < tokens.length; cut++) {
    const a = tokens.slice(0, cut);
    const b = tokens.slice(cut);
    const la = lineLen(a);
    const lb = lineLen(b);
    if (la > LINE_MAX_CHARS) break;
    if (lb > LINE_MAX_CHARS) continue;
    const breaking = breakPenalty(a, b[0]);
    if (breaking === Infinity) continue;
    let cost = breaking + (la <= lb ? 0 : 1000) + Math.abs(lb - la); // bottom-heavy, balanced
    if (a.length === 1 && tokens.length > 2) cost += 40; // no lonely word on the top line
    if (cost < best.cost) best = { cost, lines: [a, b] };
  }
  return best;
};

/** Partition one sentence into pages via DP, scoring page shape, break rules
 *  and display duration together (a page is the unit readers experience). */
const paginateSentence = (tokens: PageToken[]): PageToken[][][] => {
  const n = tokens.length;
  const best: number[] = Array(n + 1).fill(Infinity);
  const from: number[] = Array(n + 1).fill(0);
  const chosen: PageToken[][][] = Array(n + 1).fill(null);
  best[0] = 0;
  for (let end = 1; end <= n; end++) {
    for (let start = end - 1; start >= 0; start--) {
      const run = tokens.slice(start, end);
      if (lineLen(run) > 2 * LINE_MAX_CHARS + 2 && run.length > 1) break; // can't fit two lines
      const page = layoutPage(run);
      if (page.cost === Infinity) continue;
      // the break BETWEEN pages must also respect the line rules (the page
      // boundary is a line boundary too)
      const boundary = breakPenalty(page.lines[page.lines.length - 1], tokens[end]);
      if (boundary === Infinity) continue;
      const spanMs = run[run.length - 1].toMs + PAGE_HOLD_MS - run[0].fromMs;
      const flash = spanMs < MIN_PAGE_MS ? 60 : 0; // discourage blink-and-miss pages
      const cost = best[start] + 80 + page.cost + boundary + flash;
      if (cost < best[end]) {
        best[end] = cost;
        from[end] = start;
        chosen[end] = page.lines;
      }
    }
  }
  const pages: PageToken[][][] = [];
  for (let end = n; end > 0; end = from[end]) pages.unshift(chosen[end] ?? [tokens.slice(from[end], end)]);
  return pages;
};

/** Pages for one uninterrupted run of speech (see buildPages for `breaksMs`). */
const paginateRun = (tokens: PageToken[]): Page[] => {
  // sentences never share a line: a sentence always begins at a line start
  const sentences: PageToken[][] = [];
  let sentence: PageToken[] = [];
  for (const t of tokens) {
    sentence.push(t);
    if (isSentenceEnd(t.text)) {
      sentences.push(sentence);
      sentence = [];
    }
  }
  if (sentence.length) sentences.push(sentence);

  // paginate each sentence independently — a sentence always starts its own
  // page/line and never spills from line 2 of a page (Netflix rule)
  const rawPages: { lines: PageToken[][]; wholeSentence: boolean; endsSentence: boolean }[] = [];
  for (const s of sentences) {
    const paginated = paginateSentence(s);
    paginated.forEach((lines, i) =>
      rawPages.push({
        lines,
        wholeSentence: paginated.length === 1,
        endsSentence: i === paginated.length - 1,
      }),
    );
  }

  // two short sentences may share a page: sentence A ends on a single line and
  // sentence B is complete on one line (max two sentences per page)
  const pages: Page[] = [];
  for (let i = 0; i < rawPages.length; ) {
    const cur = rawPages[i];
    const next = rawPages[i + 1];
    let pageLines = cur.lines;
    if (
      cur.endsSentence &&
      cur.lines.length === 1 &&
      next?.wholeSentence &&
      next.lines.length === 1 &&
      lineLen(cur.lines[0]) <= lineLen(next.lines[0])
    ) {
      pageLines = [cur.lines[0], next.lines[0]];
      i += 2;
    } else {
      i += 1;
    }
    const flat = pageLines.flat();
    pages.push({
      startMs: Math.max(0, flat[0].fromMs - LINE_LEAD_MS),
      endMs: flat[flat.length - 1].toMs + PAGE_HOLD_MS,
      lines: pageLines,
    });
  }
  return pages;
};

/** Build the pages of a clip.
 *
 *  `breaksMs` are splice points in the clip (a hook-first export cuts the hook
 *  in front of the passage). A page may not span one: the shot changes there,
 *  so text from before the cut still standing after it reads as a stuck
 *  caption — and the two sides are different moments, not one sentence. */
export const buildPages = (captions: Caption[], breaksMs: number[] = []): Page[] => {
  const tokens: PageToken[] = captions.map((c) => ({
    text: c.text.trim(),
    fromMs: c.startMs,
    toMs: c.endMs,
  }));
  const breaks = [...breaksMs].sort((a, b) => a - b);

  // one run per piece of the clip; pages, and the sentences behind them, never
  // straddle a splice
  const pages: Page[] = [];
  let run: PageToken[] = [];
  let cursor = 0;
  for (const token of tokens) {
    while (cursor < breaks.length && token.fromMs >= breaks[cursor]) {
      pages.push(...paginateRun(run));
      run = [];
      cursor++;
    }
    run.push(token);
  }
  pages.push(...paginateRun(run));

  // every page stays inside its own piece of the clip: the reveal lead-in never
  // reaches back across the cut the page follows, and the hold never runs past
  // the cut it precedes
  const spokenAt = (page: Page) => page.lines[0][0].fromMs;
  for (const page of pages) {
    const previousCut = breaks.filter((b) => b <= spokenAt(page)).pop();
    if (previousCut !== undefined) page.startMs = Math.max(page.startMs, previousCut);
  }

  // timing polish: no sub-second flashes, no flicker between nearby pages
  for (let i = 0; i < pages.length; i++) {
    const next = pages[i + 1];
    pages[i].endMs = Math.max(pages[i].endMs, pages[i].startMs + MIN_PAGE_MS);
    if (next) {
      pages[i].endMs = Math.min(pages[i].endMs, next.startMs);
      const gap = next.startMs - pages[i].endMs;
      if (gap > 0 && gap < MAX_BRIDGE_MS) pages[i].endMs = next.startMs;
    }
    const nextCut = breaks.find((b) => b > spokenAt(pages[i]));
    if (nextCut !== undefined) pages[i].endMs = Math.min(pages[i].endMs, nextCut);
  }
  return pages;
};
