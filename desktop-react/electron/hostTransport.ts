import net, { type Socket } from "node:net";
import { serialize, deserialize } from "node:v8";
import { timingSafeEqual } from "node:crypto";

const MAX_FRAME_BYTES = 64 * 1024 * 1024;
type Frame = { type: string; token?: string; pid?: number; id?: number; channel?: string; args?: unknown[]; value?: unknown; error?: string };
export type HostHandler = (args: unknown[]) => unknown | Promise<unknown>;

function write(socket: Socket, frame: Frame): void {
  const body = serialize(frame);
  if (body.length > MAX_FRAME_BYTES) throw new Error("Host message exceeds 64 MiB");
  const header = Buffer.alloc(4);
  header.writeUInt32BE(body.length);
  socket.write(Buffer.concat([header, body]));
}

/** Retain fragments, then assemble a frame once. Recopying the accumulated
 * prefix on every socket chunk blocks the desktop main process on long reads. */
export class HostFrameDecoder {
  private header = Buffer.alloc(4);
  private headerBytes = 0;
  private length = 0;
  private bodyBytes = 0;
  private parts: Buffer[] = [];

  constructor(private receive: (frame: Frame) => void) {}

  push(chunk: Buffer): void {
    let offset = 0;
    while (offset < chunk.length) {
      if (this.headerBytes < 4) {
        const count = Math.min(4 - this.headerBytes, chunk.length - offset);
        chunk.copy(this.header, this.headerBytes, offset, offset + count);
        this.headerBytes += count;
        offset += count;
        if (this.headerBytes < 4) return;
        this.length = this.header.readUInt32BE(0);
        if (!this.length || this.length > MAX_FRAME_BYTES) throw new Error("Invalid Host frame length");
      }
      const count = Math.min(this.length - this.bodyBytes, chunk.length - offset);
      if (count) {
        this.parts.push(chunk.subarray(offset, offset + count));
        this.bodyBytes += count;
        offset += count;
      }
      if (this.bodyBytes < this.length) return;
      const body = this.parts.length === 1 ? this.parts[0] : Buffer.concat(this.parts, this.length);
      this.reset();
      const frame = deserialize(body) as Frame;
      if (!frame || typeof frame.type !== "string") throw new Error("Invalid Host message");
      this.receive(frame);
    }
  }

  reset(): void {
    this.headerBytes = 0;
    this.length = 0;
    this.bodyBytes = 0;
    this.parts = [];
  }
}

function read(socket: Socket, receive: (frame: Frame) => void): void {
  const decoder = new HostFrameDecoder(receive);
  socket.on("data", (chunk) => {
    try { decoder.push(chunk); }
    catch { decoder.reset(); socket.destroy(); }
  });
  socket.on("close", () => decoder.reset());
}

export class HostServer {
  private clients = new Set<Socket>();
  private clientPids = new Map<Socket, number>();
  private server = net.createServer((socket) => {
    let authenticated = false;
    const timeout = setTimeout(() => socket.destroy(), 5000);
    socket.on("error", () => undefined);
    socket.on("close", () => {
      clearTimeout(timeout);
      if (this.clients.delete(socket)) {
        this.clientPids.delete(socket);
        this.clientsChanged(this.clientProcessIds);
      }
    });
    read(socket, (frame) => {
      if (!authenticated) {
        const supplied = Buffer.from(frame.token ?? "");
        const expected = Buffer.from(this.token);
        if (frame.type !== "auth" || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
          socket.destroy(); return;
        }
        authenticated = true;
        clearTimeout(timeout);
        this.clients.add(socket);
        if (Number.isSafeInteger(frame.pid) && frame.pid! > 0) this.clientPids.set(socket, frame.pid!);
        this.clientsChanged(this.clientProcessIds);
        write(socket, { type: "ready" });
        return;
      }
      if (frame.type !== "call" || !Number.isSafeInteger(frame.id) || typeof frame.channel !== "string" || !Array.isArray(frame.args)) {
        socket.destroy(); return;
      }
      const handler = this.handlers.get(frame.channel);
      void Promise.resolve().then(() => {
        if (!handler) throw new Error("Unknown Host operation");
        return handler(frame.args!);
      }).then((value) => {
        if (!socket.destroyed) write(socket, { type: "result", id: frame.id, value });
      }).catch((error) => {
        if (!socket.destroyed) write(socket, { type: "result", id: frame.id, error: error instanceof Error ? error.message : String(error) });
      });
    });
  });

  constructor(private token: string, private handlers: Map<string, HostHandler>,
    private clientsChanged: (pids: number[]) => void = () => {}) {}

  get hasClients(): boolean { return this.clients.size > 0; }
  get clientProcessIds(): number[] { return [...new Set(this.clientPids.values())]; }

  async listen(endpoint: string): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      this.server.once("error", reject);
      this.server.listen(endpoint, () => { this.server.removeListener("error", reject); resolve(); });
    });
  }

  broadcast(channel: string, value: unknown): void {
    for (const socket of this.clients) {
      try { write(socket, { type: "event", channel, value }); } catch { socket.destroy(); }
    }
  }

  close(): void {
    for (const socket of this.clients) socket.destroy();
    this.server.close();
  }
}

export class HostClient {
  private socket: Socket | null = null;
  private connecting: Promise<void> | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();

  constructor(private notify: (channel: string, value: unknown) => void) {}

  connect(endpoint: string, token: string): Promise<void> {
    if (this.connecting) return this.connecting;
    if (this.socket && !this.socket.destroyed) return Promise.resolve();
    const connection = new Promise<void>((resolve, reject) => {
      const socket = net.createConnection(endpoint);
      const timeout = setTimeout(() => socket.destroy(new Error("Host connection timed out")), 5000);
        socket.on("connect", () => write(socket, { type: "auth", token, pid: process.pid }));
      socket.on("error", reject);
      socket.on("close", () => {
        clearTimeout(timeout);
        if (this.socket === socket) this.socket = null;
        const error = new Error("Loom Host disconnected");
        reject(error);
        for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(error); }
        this.pending.clear();
        this.notify("loom:host-disconnected", null);
      });
      read(socket, (frame) => {
        if (frame.type === "ready") {
          clearTimeout(timeout); this.socket = socket; resolve();
        } else if (frame.type === "event" && frame.channel) {
          this.notify(frame.channel, frame.value);
        } else if (frame.type === "result" && frame.id !== undefined) {
          const request = this.pending.get(frame.id);
          if (!request) return;
          clearTimeout(request.timer); this.pending.delete(frame.id);
          if (frame.error) request.reject(new Error(frame.error));
          else request.resolve(frame.value);
        }
      });
    });
    this.connecting = connection.finally(() => { this.connecting = null; });
    return this.connecting;
  }

  call(channel: string, args: unknown[]): Promise<unknown> {
    const socket = this.socket;
    if (!socket || socket.destroyed) return Promise.reject(new Error("Loom Host is unavailable"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`Host request timed out: ${channel}`)); }, 120000);
      this.pending.set(id, { resolve, reject, timer });
      try { write(socket, { type: "call", id, channel, args }); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
    });
  }

  close(): void { this.socket?.destroy(); }
}
