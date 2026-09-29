import { describe, expect, it } from "vitest";
import { parseSseFrames } from "@/transport/api";

describe("parseSseFrames", () => {
  it("parses data lines into RunEvents, ignoring id/comment lines", () => {
    const text = [
      "id: 0",
      'data: {"run_id":"r","trace_id":null,"seq":0,"channel":"stage","at":null,"payload":{"name":"run","status":"running"}}',
      "",
      "id: 1",
      'data: {"run_id":"r","trace_id":null,"seq":1,"channel":"cost","at":null,"payload":{"input_tokens":10,"output_tokens":2,"usd":0.001}}',
      "",
    ].join("\n");
    const events = parseSseFrames(text);
    expect(events).toHaveLength(2);
    expect(events[0]?.channel).toBe("stage");
    expect(events[1]?.channel).toBe("cost");
  });

  it("skips malformed frames without throwing", () => {
    const text = 'data: {not json}\n\ndata: {"seq":5,"channel":"check"}\n\n';
    const events = parseSseFrames(text);
    expect(events).toHaveLength(1);
    expect(events[0]?.seq).toBe(5);
  });

  it("returns an empty array for an empty body", () => {
    expect(parseSseFrames("")).toEqual([]);
  });
});
