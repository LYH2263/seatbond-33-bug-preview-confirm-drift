export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text || res.statusText;
    try {
      const data = JSON.parse(text);
      if (data && typeof data.detail === "string") message = data.detail;
    } catch {
      // 非 JSON 响应，保留原文
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}
