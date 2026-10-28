import { PLATFORM_LABEL, filterFiches, pageWindow, safeProfileUrl } from "./liste.js";

const SUB_KIND = { new: "Ajout", removal: "Retrait", update: "Mise à jour" };

const PAGE_SIZE = 20;
const $ = (sel) => document.querySelector(sel);

const nf = new Intl.NumberFormat("fr-FR");

const MONTHS = [
  "janvier", "février", "mars", "avril", "mai", "juin",
  "juillet", "août", "septembre", "octobre", "novembre", "décembre",
];

let all = [];
let page = 1;
let seenAsOf = null;
let liveTimer = 0;

function formatInt(value) {
  return nf.format(Number(value) || 0);
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = formatInt(value);
}

function formatUtc(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()} à ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

function paintCatalog(data) {
  all = Array.isArray(data.fiches) ? data.fiches : [];
  seenAsOf = data.as_of || seenAsOf;
  setText("total-blocks", data.total_blocks);
  setText("fiche-count", data.fiche_count);
  setText("name-count", data.name_count);
  setText("fiche-sum", data.fiche_count_sum);
  setText("historical", data.historical_base);
  const lede = document.getElementById("hero-lede");
  if (lede) {
    lede.textContent = `${formatInt(data.total_blocks)} blocages déclarés. ${formatInt(data.fiche_count)} fiches. Le débat, lui, est resté dehors.`;
  }
  const caption = document.querySelector(".caption");
  if (caption) {
    const when = formatUtc(data.as_of);
    caption.textContent = when
      ? `Compteur global de la liste, relevé le ${when}. Une partie du total n'a même pas de nom.`
      : "Compteur global de la liste. Une partie du total n'a même pas de nom.";
  }
  const pull = document.getElementById("pull-line");
  if (pull) {
    const when = formatUtc(data.refreshed_at || data.snapshot_at || data.as_of);
    pull.textContent = when
      ? `La liste ci-dessous date du dernier pull, le ${when.replace(" UTC", "\u00A0UTC")}.`
      : "La liste ci-dessous date du dernier pull.";
  }
  fillSelect("cat", data.categories || [], "Tous");
  fillSelect("net", data.platforms || [], "Tous");
  $("#net").querySelectorAll("option").forEach((option) => {
    if (!option.value) return;
    const label = PLATFORM_LABEL[option.value] || option.value;
    const count = option.textContent.match(/\(([^)]+)\)$/);
    option.textContent = count ? `${label} (${count[1]})` : label;
  });
}

function paintLive(status) {
  const node = $("#live-line");
  if (!node || !status) return;
  if (status.ok && status.as_of) {
    node.textContent = `En direct. Dernier relevé le ${formatUtc(status.as_of)}. Le bouton, lui, ne dort pas.`;
    return;
  }
  if (status.error) {
    node.textContent = "Le relevé vient de rater. La copie affichée est la dernière qui a marché.";
  }
}



function fillSelect(id, pairs, allLabel) {
  const select = document.getElementById(id);
  const current = select.value;
  select.replaceChildren();
  if (!Array.isArray(pairs)) pairs = [];
  const first = document.createElement("option");
  first.value = "";
  first.textContent = allLabel;
  select.append(first);
  pairs.forEach((pair) => {
    if (!Array.isArray(pair)) return;
    const [label, count] = pair;
    const text = String(label ?? "");
    if (!text) return;
    const option = document.createElement("option");
    option.value = text;
    option.textContent = `${text} (${formatInt(count)})`;
    select.append(option);
  });
  const wanted = [...select.options].some((option) => option.value === current);
  if (wanted) select.value = current;
}

function readControls() {
  return {
    q: $("#q").value,
    category: $("#cat").value,
    platform: $("#net").value,
    sort: $("#sort").value || "count",
  };
}

function writeQuery() {
  const { q, category, platform, sort } = readControls();
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (category) params.set("cat", category);
  if (platform) params.set("net", platform);
  if (sort && sort !== "count") params.set("sort", sort);
  if (page > 1) params.set("page", String(page));
  const next = params.toString();
  const path = next ? `${location.pathname}?${next}` : location.pathname;
  history.replaceState(null, "", path + location.hash);
}

function readQuery() {
  const params = new URLSearchParams(location.search);
  if (params.get("q")) $("#q").value = params.get("q");
  if (params.get("sort")) $("#sort").value = params.get("sort");
  const asked = Number(params.get("page"));
  if (asked) page = asked;
  return {
    cat: params.get("cat") || "",
    net: params.get("net") || "",
  };
}

function showState(message) {
  const list = $("#list");
  list.replaceChildren();
  const p = document.createElement("p");
  p.className = "state";
  p.textContent = message;
  list.append(p);
  list.removeAttribute("aria-busy");
}

function renderRow(fiche) {
  const article = document.createElement("article");
  article.className = "fiche";

  const name = document.createElement("h3");
  name.textContent = String(fiche.name ?? "");

  const category = document.createElement("p");
  category.className = "fiche-cat";
  category.textContent = String(fiche.category ?? "");

  const network = document.createElement("p");
  network.className = "fiche-net";
  network.textContent = PLATFORM_LABEL[fiche.platform] || String(fiche.platform ?? "");

  const count = document.createElement("p");
  count.className = "fiche-count";
  const num = document.createElement("span");
  num.textContent = formatInt(fiche.count);
  const unit = document.createElement("span");
  unit.className = "unit";
  unit.textContent = "blocages";
  count.append(num, unit);

  const href = safeProfileUrl(fiche.url);
  let action;
  if (href) {
    action = document.createElement("a");
    action.className = "btn btn-red fiche-go";
    action.href = href;
    action.target = "_blank";
    action.rel = "noopener noreferrer";
    action.textContent = "Profil public";
    const hidden = document.createElement("span");
    hidden.className = "sr-only";
    hidden.textContent = `, ${fiche.name}, nouvelle fenêtre`;
    action.append(hidden);
  } else {
    action = document.createElement("p");
    action.className = "fiche-go";
    action.textContent = "Lien retiré";
  }

  const reason = document.createElement("p");
  reason.className = "fiche-reason";
  reason.textContent = String(fiche.reason || "Motif non inscrit.");

  article.append(name, category, network, count, action, reason);
  return article;
}

function render() {
  const controls = readControls();
  const rows = filterFiches(all, controls);
  const windowed = pageWindow(rows, page, PAGE_SIZE);
  page = windowed.page;

  $("#status").textContent = rows.length
    ? `${formatInt(rows.length)} fiches au tiroir`
    : "Aucune fiche. Soit le nom est libre, soit l'orthographe a été bloquée avant lui.";
  $("#page-label").textContent = rows.length
    ? `Page ${windowed.page} sur ${windowed.pages}`
    : "Page 0. Le rayon est vide.";
  $("#prev").disabled = !rows.length || windowed.page <= 1;
  $("#next").disabled = !rows.length || windowed.page >= windowed.pages;

  const list = $("#list");
  list.replaceChildren();
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "state";
    p.textContent = "Rien dans ce rayon. L'orthographe a été bloquée avant le nom.";
    list.append(p);
  } else {
    const frag = document.createDocumentFragment();
    windowed.slice.forEach((fiche) => frag.append(renderRow(fiche)));
    list.append(frag);
  }
  list.removeAttribute("aria-busy");
  writeQuery();
}

function bind() {
  $("#filters").addEventListener("submit", (event) => {
    event.preventDefault();
    page = 1;
    render();
  });

  $("#q").addEventListener("input", () => {
    page = 1;
    render();
  });

  ["cat", "net", "sort"].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => {
      page = 1;
      render();
    });
  });

  $("#prev").addEventListener("click", () => {
    page -= 1;
    render();
    $("#list").scrollIntoView({ block: "nearest" });
  });

  $("#next").addEventListener("click", () => {
    page += 1;
    render();
    $("#list").scrollIntoView({ block: "nearest" });
  });
}

function paintSubmissions(data) {
  const line = document.getElementById("sub-line");
  const list = document.getElementById("sub-list");
  if (!line || !list || !data || typeof data !== "object") return;
  const when = formatUtc(data.pulled_at).replace(" UTC", "\u00A0UTC");
  list.replaceChildren();
  const section = document.getElementById("guichet");
  if (section) section.classList.toggle("is-shut", !data.ok);
  if (!data.ok) {
    const status = Number(data.http_status) || 0;
    const message = String(data.message || "lecture ratée");
    line.textContent = when
      ? `Pull de /api/submissions, le ${when}. Réponse ${status} : ${message}`
      : `Pull de /api/submissions. Réponse ${status} : ${message}`;
    return;
  }
  const total = Number(data.total) || 0;
  const plural = total > 1 ? "s" : "";
  line.textContent = when
    ? `Pull de /api/submissions, le ${when}. ${formatInt(total)} ligne${plural} publique${plural}.`
    : `Pull de /api/submissions. ${formatInt(total)} ligne${plural} publique${plural}.`;
  const items = Array.isArray(data.items) ? data.items : [];
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "howto";
    empty.textContent = "La réponse ne contient aucune fiche publique. Le guichet a répondu par le vide.";
    list.append(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("article");
    row.className = "sub-row";
    const title = document.createElement("h3");
    title.textContent = String(item.name || "Profil public");
    row.append(title);
    const bits = [SUB_KIND[item.type] || "", PLATFORM_LABEL[item.platform] || ""].filter(Boolean);
    if (item.reason) bits.push(String(item.reason));
    if (bits.length) {
      const meta = document.createElement("p");
      meta.textContent = bits.join(" - ");
      row.append(meta);
    }
    const url = safeProfileUrl(item.url);
    if (url) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Profil public";
      row.append(link);
    }
    list.append(row);
  });
}

async function loadSubmissions() {
  const line = document.getElementById("sub-line");
  try {
    const response = await fetch(`data/submissions.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    paintSubmissions(await response.json());
  } catch {
    if (line) line.textContent = "Pull de /api/submissions raté. Le fichier n'est pas arrivé.";
    document.getElementById("guichet")?.classList.add("is-shut");
  }
}

async function loadFiches() {
  const response = await fetch(`data/fiches.json?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

function paintVisits(data) {
  const node = $("#visits");
  if (!node || !data) return;
  const total = Number(data.total) || 0;
  const today = Number(data.today) || 0;
  if (total <= 0) {
    node.textContent = "Aucun passage pour l'instant. Le registre attend.";
    return;
  }
  if (total === 1) {
    node.textContent = "1 passage. Le registre vient de s'ouvrir.";
    return;
  }
  const many = total > 1 ? "s" : "";
  node.textContent = `${formatInt(total)} passage${many}, dont ${formatInt(today)} aujourd'hui. Le registre, lui, ne débloque rien.`;
}

async function loadVisits() {
  const node = $("#visits");
  if (!node) return;
  try {
    const response = await fetch("/api/visits", { cache: "no-store" });
    if (response.status === 404) {
      node.hidden = true;
      return;
    }
    if (!response.ok) return;
    paintVisits(await response.json());
  } catch {
    /* le compteur manque, la page reste lisible */
  }
}

async function watchLive() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (response.status === 404) {
      window.clearInterval(liveTimer);
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    const status = await response.json();
    paintLive(status);
    loadVisits();
    loadSubmissions();
    if (status.ok && status.as_of && status.as_of !== seenAsOf) {
      const data = await loadFiches();
      paintCatalog(data);
      render();
    }
  } catch {
    const node = $("#live-line");
    if (node && seenAsOf) {
      node.textContent = "Le fil direct est coupé. La copie affichée reste la dernière qui a marché.";
    }
  }
}

async function init() {
  const pending = readQuery();
  try {
    const data = await loadFiches();
    paintCatalog(data);
    if (pending.cat && [...$("#cat").options].some((option) => option.value === pending.cat)) {
      $("#cat").value = pending.cat;
    }
    if (pending.net && [...$("#net").options].some((option) => option.value === pending.net)) {
      $("#net").value = pending.net;
    }
    bind();
    render();
    loadVisits();
    loadSubmissions();
    watchLive();
    liveTimer = window.setInterval(watchLive, 15000);
  } catch {
    showState("Le tiroir est coincé. Le fichier des fiches n'est pas arrivé.");
    $("#status").textContent = "Erreur de chargement. Le classeur a avalé la page.";
  }
}

if ($("#list")) init();
