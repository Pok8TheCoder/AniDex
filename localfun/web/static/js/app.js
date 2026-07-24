async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    credentials: "same-origin",
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail || j.error || JSON.stringify(j);
      if (Array.isArray(msg)) msg = msg.map((x) => x.msg || x).join("; ");
    } catch (_) {
      try { msg = await res.text(); } catch (__) {}
    }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

async function pollJob(jobId, { onProgress, timeoutMs = 600000 } = {}) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const job = await api(`/api/jobs/${jobId}`);
    if (onProgress) onProgress(job);
    if (job.status === "done") return job;
    if (job.status === "error") throw new Error(job.error || job.message || "Job failed");
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("Timed out waiting for job");
}

function fmtEp(n) {
  if (n == null) return "?";
  return Number.isInteger(n) ? String(n) : String(n);
}

function $(sel, root = document) {
  return root.querySelector(sel);
}
function $all(sel, root = document) {
  return [...root.querySelectorAll(sel)];
}

// Capture LAN access token from ?token= (cookie set by server) and strip from URL
(function bootstrapToken() {
  try {
    const u = new URL(location.href);
    if (u.searchParams.has("token")) {
      u.searchParams.delete("token");
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    }
  } catch (_) {}
})();

window.LF = { api, pollJob, fmtEp, $, $all };
