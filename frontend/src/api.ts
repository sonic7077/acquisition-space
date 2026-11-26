export type Row = Record<string, any>;
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> =
    options.body instanceof FormData
      ? {}
      : { "Content-Type": "application/json" };
  const response = await fetch("/api" + path, {
    ...options,
    headers: { ...headers, ...options.headers },
    credentials: "same-origin",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "请检查输入字段或重新登录",
    );
  return data;
}
export const post = (path: string, data: any) =>
  api(path, { method: "POST", body: JSON.stringify(data) });
export const patch = (path: string, data: any) =>
  api(path, { method: "PATCH", body: JSON.stringify(data) });
export function uploadAsset(
  form: FormData,
  onProgress: (percent: number) => void,
  onTransferred: () => void,
): Promise<Row> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/assets");
    request.responseType = "json";
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.min(99, Math.floor(event.loaded * 100 / event.total)));
      }
    };
    request.upload.onload = onTransferred;
    request.onload = () => {
      if (request.status >= 200 && request.status < 300 && request.response?.id) {
        resolve(request.response);
      } else {
        const detail = request.response?.detail;
        reject(new Error(typeof detail === "string" ? detail : "上传未确认，请重新检查文件或登录状态"));
      }
    };
    request.onerror = () => reject(new Error("上传连接中断，请检查网络后重试"));
    request.onabort = () => reject(new Error("上传已中断"));
    request.send(form);
  });
}
export function saveText(name: string, text: string) {
  const url = URL.createObjectURL(
    new Blob([text], { type: "text/plain;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
