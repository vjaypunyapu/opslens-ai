import { renderHook, act, waitFor } from "@testing-library/react";
import { useChat } from "@/hooks/useChat";

// Mock Clerk's useAuth so the hook can run without a Clerk provider
jest.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: jest.fn().mockResolvedValue("test-token") }),
}));

const SESSION_ID = "session-abc";

function makeBodyReader(text: string) {
  const encoder = new TextEncoder();
  const chunks = [encoder.encode(text)];
  let done = false;
  return {
    getReader() {
      return {
        read(): Promise<{ done: boolean; value: Uint8Array | undefined }> {
          if (!done) {
            done = true;
            return Promise.resolve({ done: false, value: chunks[0] });
          }
          return Promise.resolve({ done: true, value: undefined });
        },
      };
    },
  };
}

function mockFetch(responses: Array<{ ok: boolean; json?: object; body?: string }>) {
  let call = 0;
  global.fetch = jest.fn().mockImplementation(() => {
    const resp = responses[call++] ?? responses[responses.length - 1];
    return Promise.resolve({
      ok: resp.ok,
      statusText: resp.ok ? "OK" : "Internal Server Error",
      json: () => Promise.resolve(resp.json ?? {}),
      body: resp.body != null ? makeBodyReader(resp.body) : null,
    });
  });
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe("useChat — loadMessages", () => {
  it("loads messages from the API into state", async () => {
    const messages = [
      { id: "1", session_id: SESSION_ID, role: "user", content: "Hello", sources: [], token_count: null, latency_ms: null, feedback: null, created_at: "" },
    ];
    mockFetch([{ ok: true, json: { messages } }]);

    const { result } = renderHook(() => useChat(SESSION_ID));
    await act(async () => { await result.current.loadMessages(); });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toBe("Hello");
  });

  it("sets an error when the API call fails", async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error("Network failure"));
    const { result } = renderHook(() => useChat(SESSION_ID));
    await act(async () => { await result.current.loadMessages(); });
    expect(result.current.error).toBe("Network failure");
  });
});

describe("useChat — sendMessage", () => {
  it("optimistically adds the user message before the response arrives", async () => {
    const sseBody =
      'data: {"type":"token","data":"Hello"}\n\n' +
      'data: {"type":"done","latency_ms":500}\n\n';
    mockFetch([{ ok: true, body: sseBody }]);

    const { result } = renderHook(() => useChat(SESSION_ID));

    act(() => { result.current.sendMessage("What broke?"); });

    // The user message should appear synchronously (optimistic update)
    expect(result.current.messages.some((m) => m.content === "What broke?" && m.role === "user")).toBe(true);

    // Let the stream finish to avoid act() warnings
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
  });

  it("streams tokens into the assistant placeholder message", async () => {
    const sseBody =
      'data: {"type":"token","data":"Pay"}\n\n' +
      'data: {"type":"token","data":"ment"}\n\n' +
      'data: {"type":"done","latency_ms":800}\n\n';
    mockFetch([{ ok: true, body: sseBody }]);

    const { result } = renderHook(() => useChat(SESSION_ID));
    act(() => { result.current.sendMessage("What broke?"); });

    await waitFor(() => expect(result.current.isStreaming).toBe(false));

    const assistant = result.current.messages.find((m) => m.role === "assistant");
    expect(assistant?.content).toBe("Payment");
  });

  it("sets sources on the assistant message when a sources event arrives", async () => {
    const sources = [{ title: "Ticket #1", url: null, source_type: "zendesk", score: 0.9 }];
    const sseBody =
      `data: {"type":"sources","data":${JSON.stringify(sources)}}\n\n` +
      'data: {"type":"done","latency_ms":600}\n\n';
    mockFetch([{ ok: true, body: sseBody }]);

    const { result } = renderHook(() => useChat(SESSION_ID));
    act(() => { result.current.sendMessage("Any tickets?"); });

    await waitFor(() => expect(result.current.isStreaming).toBe(false));

    const assistant = result.current.messages.find((m) => m.role === "assistant");
    expect(assistant?.sources).toHaveLength(1);
    expect(assistant?.sources[0].title).toBe("Ticket #1");
  });

  it("sets latency_ms on the assistant message when done event arrives", async () => {
    const sseBody = 'data: {"type":"done","latency_ms":1234}\n\n';
    mockFetch([{ ok: true, body: sseBody }]);

    const { result } = renderHook(() => useChat(SESSION_ID));
    act(() => { result.current.sendMessage("Quick query"); });

    await waitFor(() => expect(result.current.isStreaming).toBe(false));

    const assistant = result.current.messages.find((m) => m.role === "assistant");
    expect(assistant?.latency_ms).toBe(1234);
  });

  it("sets error state and removes placeholder when query fails", async () => {
    mockFetch([{ ok: false }]);

    const { result } = renderHook(() => useChat(SESSION_ID));
    act(() => { result.current.sendMessage("Will this fail?"); });

    await waitFor(() => expect(result.current.error).toMatch(/Query failed/));
    expect(result.current.messages.some((m) => m.role === "assistant")).toBe(false);
  });

  it("sets isStreaming to false after the stream completes", async () => {
    const sseBody = 'data: {"type":"done","latency_ms":100}\n\n';
    mockFetch([{ ok: true, body: sseBody }]);

    const { result } = renderHook(() => useChat(SESSION_ID));
    act(() => { result.current.sendMessage("Done?"); });

    await waitFor(() => expect(result.current.isStreaming).toBe(false));
  });

  it("does nothing if sendMessage is called while already streaming", async () => {
    // Never resolves — keeps isStreaming=true
    global.fetch = jest.fn().mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useChat(SESSION_ID));

    act(() => { result.current.sendMessage("First"); });
    // isStreaming=true, two messages added (user + placeholder)
    await waitFor(() => expect(result.current.isStreaming).toBe(true));
    const countBefore = result.current.messages.length;

    act(() => { result.current.sendMessage("Second while streaming"); });
    expect(result.current.messages.length).toBe(countBefore);
  });
});

describe("useChat — sendFeedback", () => {
  it("updates the message feedback field after a successful POST", async () => {
    const messages = [
      { id: "msg-1", session_id: SESSION_ID, role: "assistant", content: "Answer", sources: [], token_count: null, latency_ms: null, feedback: null, created_at: "" },
    ];
    mockFetch([{ ok: true, json: { messages } }, { ok: true, json: {} }]);

    const { result } = renderHook(() => useChat(SESSION_ID));
    await act(async () => { await result.current.loadMessages(); });
    await act(async () => { await result.current.sendFeedback("msg-1", "thumbs_up"); });

    const msg = result.current.messages.find((m) => m.id === "msg-1");
    expect(msg?.feedback).toBe("thumbs_up");
  });
});
