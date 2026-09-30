import { compact, encodeSegment, requireId } from "../encoding.js";
import { ValidationError } from "../errors.js";
import type { HttpClient } from "../http.js";
import type { BatchResult, ListOptions, RequestOptions, User, UserImport, UserInput, UserList } from "../types.js";
import { toBatch, type WireBatch } from "./items.js";

/**
 * `likyly.users` - optional user profiles. Needs the **secret** API key (profiles are personal data).
 * You don't have to create a user before sending events for them.
 */
export class UsersResource {
  constructor(private readonly http: HttpClient) {}

  async get(userId: string, options?: RequestOptions): Promise<User> {
    const res = await this.http.request<WireUser>({ method: "GET", path: `/users/${encodeSegment(requireId(userId, "userId"))}`, idempotent: true, options });
    return toUser(res.data);
  }

  /** One page of users. Pagination is `limit` + `offset`; without `limit` the API returns every user. */
  async list(listOptions: ListOptions = {}, options?: RequestOptions): Promise<UserList> {
    const res = await this.http.request<WireUser[]>({
      method: "GET",
      path: "/users",
      query: { limit: listOptions.limit, offset: listOptions.offset },
      idempotent: true,
      options,
    });
    const total = res.headers.get("x-total-count");
    return { users: res.data.map(toUser), total: total === null ? undefined : Number(total), limit: listOptions.limit, offset: listOptions.offset ?? 0 };
  }

  /** Creates or replaces the profile (idempotent). `properties` is free-form: country, segment, language, ... */
  async upsert(userId: string, user: UserInput = {}, options?: RequestOptions): Promise<User> {
    const res = await this.http.request<WireUser>({
      method: "PUT",
      path: `/users/${encodeSegment(requireId(userId, "userId"))}`,
      body: compact({ properties: user.properties }),
      idempotent: true,
      options,
    });
    return toUser(res.data);
  }

  /** Erases the user: the profile **and every event recorded for them**. */
  async delete(userId: string, options?: RequestOptions): Promise<void> {
    await this.http.request({ method: "DELETE", path: `/users/${encodeSegment(requireId(userId, "userId"))}`, idempotent: true, options });
  }

  /** Batch upsert (1-1000 users). A failing entry is reported in `errors`. */
  async import(users: UserImport[], options?: RequestOptions): Promise<BatchResult> {
    if (!Array.isArray(users) || users.length === 0) throw new ValidationError("users must be a non-empty array");
    const res = await this.http.request<WireBatch>({
      method: "POST",
      path: "/users/import",
      body: { users: users.map((u) => ({ user_id: requireId(u.userId, "userId"), ...compact({ properties: u.properties }) })) },
      idempotent: true,
      options,
    });
    return toBatch(res.data);
  }
}

interface WireUser {
  user_id: string;
  properties?: Record<string, unknown>;
}

function toUser(w: WireUser): User {
  return { userId: w.user_id, properties: w.properties ?? {} };
}
