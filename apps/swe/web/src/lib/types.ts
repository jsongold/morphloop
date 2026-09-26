// Hand-written wire types of the lab terminal WebSocket (contracts/schemas/websocket/)
// that TerminalPane reads. No code generation (ADR-0017); keep in sync by hand.

export type LabInstanceId = string;
export type TerminalId = string;

export type LabStatus = "starting" | "ready" | "resetting" | "stopped" | "error";

export interface LabState {
  lab_instance_id: LabInstanceId;
  attempt_id: string;
  status: LabStatus;
  terminal_id: TerminalId | null;
  terminal_path: string;
  replaced_by_lab_instance_id: LabInstanceId | null;
}

export interface TerminalOutputPayload {
  terminal_id: TerminalId;
  lab_instance_id: LabInstanceId;
  sequence: number;
  data: string;
  encoding: "utf-8" | "base64";
}
