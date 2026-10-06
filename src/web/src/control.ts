export class ControlError extends Error {
  constructor(
    message: string,
    public cancelled = false,
  ) {
    super(message);
  }
}

type Message = {
  type: string;
  id?: number;
  ok?: boolean;
  error?: string;
  cancelled?: boolean;
  state?: Record<string, string>;
};

export class MachineControl {
  private socket: WebSocket | null = null;
  private ready: Promise<void> | null = null;
  private sequence = 0;
  private pending = new Map<
    number,
    {
      resolve: (result: unknown) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();

  constructor(
    private onState: (state: Record<string, string>) => void,
    private onError: (message: string) => void,
    private onDisconnect: () => void,
  ) {}

  private connect(): Promise<void> {
    if (this.ready) return this.ready;
    const url = new URL("/api/admin/machine/ws", window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    this.socket = socket;
    this.ready = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error("实时控制连接超时"));
        this.close();
      }, 5000);
      socket.onmessage = (event) => {
        if (this.socket !== socket) return;
        const result = JSON.parse(event.data) as Message;
        if (result.type === "connected") {
          clearTimeout(timer);
          resolve();
        } else if (result.type === "state" && result.state) {
          this.onState(result.state);
        } else if (result.type === "result" && result.id !== undefined) {
          const request = this.pending.get(result.id);
          if (!request) return;
          clearTimeout(request.timer);
          this.pending.delete(result.id);
          if (result.ok) request.resolve(result);
          else
            request.reject(
              new ControlError(
                result.error || "设备操作失败",
                result.cancelled,
              ),
            );
        } else if (result.type === "error") {
          const message = result.error || "实时控制连接出错";
          clearTimeout(timer);
          reject(new Error(message));
          this.onError(message);
        }
      };
      socket.onerror = () => {
        clearTimeout(timer);
        reject(new Error("无法建立实时控制连接"));
      };
      socket.onclose = () => {
        clearTimeout(timer);
        if (this.socket !== socket) return;
        reject(new Error("实时控制连接已断开"));
        this.close();
      };
    });
    return this.ready;
  }

  async request<T>(type: string, payload?: object): Promise<T> {
    await this.connect();
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN)
      throw new Error("实时控制连接已断开，请重新读取设备状态");
    const id = ++this.sequence;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        // 超时后关闭连接，不重发可能已执行的动作。
        this.close();
      }, 75000);
      this.pending.set(id, {
        resolve: (result) => resolve(result as T),
        reject,
        timer,
      });
      socket.send(JSON.stringify({ id, type, payload }));
    });
  }

  async disconnect() {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      this.close();
      return;
    }
    await this.request("disconnect");
    this.close();
  }

  close(notify = true) {
    const socket = this.socket;
    this.socket = null;
    this.ready = null;
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error("实时控制连接已断开；未确认动作不会自动重发"));
    }
    this.pending.clear();
    socket?.close();
    if (notify) this.onDisconnect();
  }
}
