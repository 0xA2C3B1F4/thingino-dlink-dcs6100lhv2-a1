import type { JsonObject } from "../../api/contracts";
import { ApiClient } from "../../api/client";
import { routes } from "../../api/routes";
import { type FieldSpec } from "../../app/forms";

export const bool = (path: string, label: string, description?: string): FieldSpec => ({ path, label, type: "checkbox", ...(description ? { description } : {}) });
export const text = (path: string, label: string, required = false): FieldSpec => ({ path, label, type: "text", required });
export const number = (path: string, label: string, min?: number, max?: number): FieldSpec => ({ path, label, type: "number", ...(min !== undefined ? { min } : {}), ...(max !== undefined ? { max } : {}) });
export const select = (path: string, label: string, values: readonly (string | number)[], valueType: "number" | "string" = "string"): FieldSpec => ({
  path, label, type: "select", valueType, options: values.map((value) => ({ label: String(value), value: String(value) })),
});
export const secret = (path: string, label: string, description: string): FieldSpec => ({ path, label, type: "password", writeOnlySecret: true, description });
export const section = (name: string, fields: FieldSpec[]): FieldSpec[] => fields.map((field, index) => index === 0 ? { ...field, section: name } : field);

export const prudyntLoad = async (
  client: ApiClient,
  domains: ReadonlyArray<[string, (value: unknown) => JsonObject]>,
): Promise<JsonObject> => {
  const values = await Promise.all(domains.map(async ([domain, decode]) => decode(await client.json<unknown>(routes.prudynt.domain(domain)))));
  const result: JsonObject = {};
  domains.forEach(([domain], index) => { result[domain] = values[index]!; });
  return result;
};
