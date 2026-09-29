import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openRunStream } from "@/transport/stream";
import type { RunEvent } from "@/types/events";

/**
 * A controllable EventSource stand-in. jsdom has no EventSource, so we stub the
 * global and drive its callbacks by hand to exercise the transport's ordering,
 * gap detection, and terminal-close behaviour deterministically.
 */
class MockEventSource {
  static instances: MockEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(event: Partial<RunEvent> & { seq: number; channel: string }) {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent<string>);
  }
  fail() {
    this.onerror?.();
  }
  open() {
    this.onopen?.();
  }
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function ev(seq: number, channel: string): RunEvent {
  return {
    run_id: "r",
    trace_id: null,
    seq,
    channel,
    at: null,
    payload: {},
  } as unknown as RunEvent;
}

describe("openRunStream", () => {
  it("delivers events in order, exactly once", () => {
    const received: number[] = [];
    openRunStream("r", { onEvent: (e) => received.push(e.seq) });
    const src = MockEventSource.instances[0]!;
    src.emit(ev(0, "check"));
    src.emit(ev(1, "check"));
    expect(received).toEqual([0, 1]);
  });

  it("drops duplicate / replayed low-seq events", () => {
    const received: number[] = [];
    openRunStream("r", { onEvent: (e) => received.push(e.seq) });
    const src = MockEventSource.instances[0]!;
    src.emit(ev(0, "check"));
    src.emit(ev(1, "check"));
    src.emit(ev(0, "check")); // replay after a reconnect
    src.emit(ev(1, "check"));
    expect(received).toEqual([0, 1]);
  });

  it("detects a seq gap and reports expected/got", () => {
    const gaps: Array<[number, number]> = [];
    openRunStream("r", {
      onEvent: () => {},
      onGap: (expected, got) => gaps.push([expected, got]),
    });
    const src = MockEventSource.instances[0]!;
    src.emit(ev(0, "check"));
    src.emit(ev(3, "check")); // skipped 1 and 2
    expect(gaps).toEqual([[1, 3]]);
  });

  it("completes and closes after decision then cost", () => {
    const onComplete = vi.fn();
    openRunStream("r", { onEvent: () => {}, onComplete });
    const src = MockEventSource.instances[0]!;
    src.emit(ev(0, "check"));
    src.emit(ev(1, "decision"));
    src.emit(ev(2, "cost"));
    expect(onComplete).toHaveBeenCalledOnce();
    expect(src.closed).toBe(true);
  });

  it("treats a post-terminal error as a clean completion, not a failure", () => {
    const onComplete = vi.fn();
    const states: string[] = [];
    openRunStream("r", {
      onEvent: () => {},
      onComplete,
      onState: (s) => states.push(s),
    });
    const src = MockEventSource.instances[0]!;
    src.emit(ev(0, "decision"));
    src.fail(); // stream closed by backend after the run finished
    expect(onComplete).toHaveBeenCalledOnce();
    expect(states).not.toContain("error");
  });

  it("surfaces an error state on a mid-run connection drop (no teardown)", () => {
    const states: string[] = [];
    openRunStream("r", { onEvent: () => {}, onState: (s) => states.push(s) });
    const src = MockEventSource.instances[0]!;
    src.emit(ev(0, "check"));
    src.fail();
    expect(states).toContain("error");
    expect(src.closed).toBe(false); // EventSource will auto-reconnect
  });
});
