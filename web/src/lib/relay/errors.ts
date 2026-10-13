export class RelayError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }

  toJSON() {
    return { error: this.code, message: this.message };
  }
}
