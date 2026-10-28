export const ALLOWED_HOSTS = new Set([
  "x.com",
  "www.x.com",
  "twitter.com",
  "www.twitter.com",
  "instagram.com",
  "www.instagram.com",
  "facebook.com",
  "www.facebook.com",
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "tiktok.com",
  "www.tiktok.com",
]);

export const PLATFORM_LABEL = {
  x: "X",
  instagram: "Instagram",
  facebook: "Facebook",
  youtube: "YouTube",
  tiktok: "TikTok",
};

export function safeProfileUrl(raw) {
  try {
    const url = new URL(String(raw));
    if (url.protocol !== "https:") return null;
    if (url.username || url.password) return null;
    if (url.port && url.port !== "443") return null;
    if (!ALLOWED_HOSTS.has(url.hostname.toLowerCase())) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function fold(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

export function filterFiches(fiches, { q = "", category = "", platform = "", sort = "count" } = {}) {
  const needle = fold(q.trim());
  const rows = fiches.filter((fiche) => {
    if (category && fiche.category !== category) return false;
    if (platform && fiche.platform !== platform) return false;
    if (!needle) return true;
    return fold(fiche.name).includes(needle) || fold(fiche.reason).includes(needle);
  });

  rows.sort((a, b) => {
    if (sort === "name") {
      return a.name.localeCompare(b.name, "fr") || b.count - a.count;
    }
    if (sort === "category") {
      return a.category.localeCompare(b.category, "fr") || b.count - a.count || a.name.localeCompare(b.name, "fr");
    }
    return b.count - a.count || a.name.localeCompare(b.name, "fr");
  });

  return rows;
}

export function pageWindow(rows, page, size) {
  const pages = Math.max(1, Math.ceil(rows.length / size) || 1);
  const current = Math.min(Math.max(1, page || 1), pages);
  const start = (current - 1) * size;
  return {
    page: current,
    pages,
    slice: rows.slice(start, start + size),
  };
}
